import { createHash } from 'node:crypto'
import { lookup } from 'node:dns/promises'
import { request as httpRequest, validateHeaderValue, type IncomingHttpHeaders } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { BlockList, isIP } from 'node:net'
import { compileCatalog, type Catalog } from './catalog.js'

export class UpstreamError extends Error {
  constructor(readonly code: string, readonly status: number, message: string) {
    super(message)
    this.name = 'UpstreamError'
  }
}

export interface Target {
  origin: string
  apiKey: string
  credentialFingerprint: string
}

export interface UpstreamConfig {
  defaultTarget?: Target
  allowedOrigins: ReadonlySet<string>
  headerTargetsAllowed: boolean
  ttlMs: number
  timeoutMs: number
  maxResponseBytes: number
}

function parseOrigin(value: string): string {
  try {
    if (!/^https?:\/\/[^\s/?#\\@]+\/?$/i.test(value)) throw new Error()
    return new URL(value).origin
  } catch {
    throw new UpstreamError('invalid_origin', 400, 'Expected an HTTP(S) origin with no path or credentials.')
  }
}

function targetFor(origin: string, apiKey: string): Target {
  if (apiKey.length === 0 || apiKey.length > 4096 || !/^[!-~]+$/.test(apiKey)) {
    throw new UpstreamError('invalid_credential', 400, 'An Arcane API key is required in a valid header format.')
  }
  return { origin, apiKey, credentialFingerprint: digest(apiKey) }
}

function digest(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function readLimit(env: NodeJS.ProcessEnv, key: string, fallback: number, max: number): number {
  const value = env[key] === undefined ? fallback : Number(env[key])
  if (!Number.isSafeInteger(value) || value < 1 || value > max) {
    throw new Error(`${key} must be an integer between 1 and ${max}.`)
  }
  return value
}

export function readUpstreamConfig(env: NodeJS.ProcessEnv = process.env): UpstreamConfig {
  const allowedOrigins = new Set(
    (env.MCP_ALLOWED_ORIGINS ?? '').split(',').map(value => value.trim()).filter(Boolean).map(parseOrigin),
  )
  const headerSetting = env.MCP_ALLOW_HEADER_TARGETS ?? 'false'
  if (headerSetting !== 'true' && headerSetting !== 'false') {
    throw new Error('MCP_ALLOW_HEADER_TARGETS must be true or false.')
  }
  const headerTargetsAllowed = headerSetting === 'true'
  const origin = env.ARCANE_URL ? parseOrigin(env.ARCANE_URL) : undefined
  if (Boolean(origin) !== Boolean(env.ARCANE_API_KEY)) {
    throw new Error('ARCANE_URL and ARCANE_API_KEY must be configured together.')
  }
  if (origin?.startsWith('http:') && !allowedOrigins.has(origin)) {
    throw new Error('HTTP targets require an explicit entry in MCP_ALLOWED_ORIGINS.')
  }
  const defaultTarget = origin ? targetFor(origin, env.ARCANE_API_KEY!) : undefined
  if (!defaultTarget && (!headerTargetsAllowed || allowedOrigins.size === 0)) {
    throw new Error('Configure a default Arcane target or enable header targets with a nonempty allowlist.')
  }
  return {
    defaultTarget, allowedOrigins, headerTargetsAllowed,
    ttlMs: readLimit(env, 'OPENAPI_TTL_SECONDS', 3600, 86400) * 1000,
    timeoutMs: readLimit(env, 'REQUEST_TIMEOUT_MS', 30000, 120000),
    maxResponseBytes: readLimit(env, 'MAX_RESPONSE_BYTES', 1048576, 32 * 1024 * 1024),
  }
}

export function selectTarget(config: UpstreamConfig, headers: IncomingHttpHeaders): Target {
  const url = headers['x-arcane-url']
  const key = headers['x-arcane-api-key']
  if (url === undefined && key === undefined && config.defaultTarget) return config.defaultTarget
  if (!config.headerTargetsAllowed && (url !== undefined || key !== undefined)) {
    throw new UpstreamError('header_targets_disabled', 403, 'Header-based Arcane targets are disabled.')
  }
  if (typeof url !== 'string' || (key !== undefined && typeof key !== 'string')) {
    throw new UpstreamError('invalid_target_headers', 400, 'Supply one Arcane URL header and one API key header.')
  }
  const origin = parseOrigin(url)
  if (!config.allowedOrigins.has(origin)) {
    throw new UpstreamError('origin_denied', 403, 'The Arcane origin is outside the configured allowlist.')
  }
  if (key === undefined && config.defaultTarget?.origin === origin) return config.defaultTarget
  if (key === undefined || key.includes(',')) {
    throw new UpstreamError('invalid_target_headers', 400, 'Supply one Arcane API key header.')
  }
  return targetFor(origin, key)
}

const permittedPrivate = new BlockList()
permittedPrivate.addSubnet('10.0.0.0', 8)
permittedPrivate.addSubnet('172.16.0.0', 12)
permittedPrivate.addSubnet('192.168.0.0', 16)
permittedPrivate.addSubnet('100.64.0.0', 10)
const privateV6 = new BlockList()
privateV6.addSubnet('fc00::', 7, 'ipv6')

const specialV4 = new BlockList()
for (const [address, prefix] of [
  ['0.0.0.0', 8], ['127.0.0.0', 8], ['169.254.0.0', 16], ['192.0.0.0', 24],
  ['192.0.2.0', 24], ['192.88.99.0', 24], ['198.18.0.0', 15], ['198.51.100.0', 24],
  ['168.63.129.16', 32], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
] as const) specialV4.addSubnet(address, prefix)

const globalV6 = new BlockList()
globalV6.addSubnet('2000::', 3, 'ipv6')
const specialV6 = new BlockList()
for (const [address, prefix] of [
  ['2001::', 23], ['2001:db8::', 32], ['2002::', 16], ['3fff::', 20],
] as const) specialV6.addSubnet(address, prefix, 'ipv6')

function addressAllowed(address: string, family: number, listed: boolean, isHttp: boolean): boolean {
  if (isIP(address) !== family || (family !== 4 && family !== 6)) return false
  if (family === 4 ? permittedPrivate.check(address) : privateV6.check(address, 'ipv6')) return listed
  if (isHttp) return false
  if (family === 4) return !specialV4.check(address)
  return globalV6.check(address, 'ipv6') && !specialV6.check(address, 'ipv6')
}

const blockedRequestHeaders = new Set([
  'accept-encoding', 'authorization', 'connection', 'content-encoding', 'content-length', 'content-type',
  'cookie', 'expect', 'host', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'x-api-key', 'x-arcane-api-key', 'x-arcane-url',
])

interface UpstreamRequest {
  method: string
  path: string
  headers?: Readonly<Record<string, string>>
  body?: string
}

interface UpstreamResponse {
  status: number
  headers: IncomingHttpHeaders
  body: Buffer
}

export interface ExecutionResult {
  status: number
  data?: unknown
  truncated?: boolean
}

export interface Snapshot {
  catalog: Catalog
  hash: string
}

interface CachedSnapshot extends Snapshot {
  expiresAt: number
  etag?: string
  lastModified?: string
}

function contentType(headers: IncomingHttpHeaders): string {
  return headers['content-type']?.split(';', 1)[0]?.trim().toLowerCase() ?? ''
}

function isJson(type: string): boolean {
  return type === 'application/json' || /^application\/[a-z0-9!#$&^_.+-]+\+json$/.test(type)
}

function invalidResponse(message: string): UpstreamError {
  return new UpstreamError('invalid_upstream_response', 502, message)
}

function sanitizeText(value: string, apiKey: string): string {
  const text = value.replace(
    /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/g, '',
  )
  const mask = apiKey.length >= 10 && !'[redacted]'.includes(apiKey) ? '[redacted]' : apiKey === '*' ? '#' : '*'
  return text.replaceAll(apiKey, mask)
}

function containsCredential(document: unknown, apiKey: string): boolean {
  // Short placeholder keys can coincide with ordinary words inside a document.
  const matches = (text: string) => apiKey.length >= 8 ? text.includes(apiKey) : text === apiKey
  const pending = [document]
  while (pending.length > 0) {
    const value = pending.pop()
    if (typeof value === 'string' && matches(value)) return true
    if (value === null || typeof value !== 'object') continue
    if (Array.isArray(value)) {
      for (const child of value) pending.push(child)
      continue
    }
    for (const [key, child] of Object.entries(value)) {
      if (matches(key)) return true
      pending.push(child)
    }
  }
  return false
}

function boundedData(value: unknown, apiKey: string): { data: unknown, truncated: boolean } {
  let nodes = 0
  let truncated = false
  function visit(entry: unknown, depth: number): unknown {
    if (++nodes > 10000 || depth > 32) {
      truncated = true
      return null
    }
    if (typeof entry === 'string') return sanitizeText(entry, apiKey)
    if (Array.isArray(entry)) {
      if (entry.length > 100) truncated = true
      return entry.slice(0, 100).map(item => visit(item, depth + 1))
    }
    if (entry === null || typeof entry !== 'object') return entry
    const result: Record<string, unknown> = Object.create(null)
    for (const [key, item] of Object.entries(entry)) {
      if (nodes >= 10000) {
        truncated = true
        break
      }
      const safeKey = sanitizeText(key, apiKey)
      result[safeKey] = /^(?:(?:x[-_])?api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|secret|client[-_]?secret|authorization)$/i.test(key)
        ? '[redacted]'
        : visit(item, depth + 1)
    }
    return result
  }
  return { data: visit(value, 0), truncated }
}

export class UpstreamClient {
  private readonly cached = new Map<string, CachedSnapshot>()
  private readonly loading = new Map<string, Promise<Snapshot>>()
  private active = 0

  constructor(private readonly config: UpstreamConfig) {}

  async getSnapshot(target: Target, principal: string): Promise<Snapshot> {
    const cacheKey = JSON.stringify([target.origin, principal, target.credentialFingerprint])
    const current = this.cached.get(cacheKey)
    if (current && current.expiresAt > Date.now()) return current
    const existing = this.loading.get(cacheKey)
    if (existing) return existing
    const load = this.loadSnapshot(target, cacheKey, current)
    this.loading.set(cacheKey, load)
    try {
      return await load
    } finally {
      this.loading.delete(cacheKey)
    }
  }

  private async loadSnapshot(target: Target, key: string, previous?: CachedSnapshot): Promise<Snapshot> {
    const headers: Record<string, string> = {}
    if (previous?.etag) headers['if-none-match'] = previous.etag
    if (previous?.lastModified) headers['if-modified-since'] = previous.lastModified
    const response = await this.send(target, { method: 'GET', path: '/api/openapi.json', headers })
    if (response.status === 304 && previous && Object.keys(headers).length > 0) {
      previous.expiresAt = Date.now() + this.config.ttlMs
      return previous
    }
    if (response.status < 200 || response.status >= 300) throw statusError(response.status)
    if (!isJson(contentType(response.headers))) throw invalidResponse('The Arcane OpenAPI document must be JSON.')
    const hash = digest(response.body)
    let document: unknown
    try {
      document = JSON.parse(response.body.toString('utf8'))
    } catch {
      throw invalidResponse('The Arcane OpenAPI document contains invalid JSON.')
    }
    if (containsCredential(document, target.apiKey)) {
      throw invalidResponse('The Arcane OpenAPI document contains reflected credentials.')
    }
    let catalog: Catalog
    try {
      catalog = previous?.hash === hash ? previous.catalog : compileCatalog(document, hash)
    } catch {
      throw invalidResponse('The Arcane OpenAPI document is invalid or exceeds catalog limits.')
    }
    const entry: CachedSnapshot = {
      catalog, hash, expiresAt: Date.now() + this.config.ttlMs,
      etag: response.headers.etag, lastModified: response.headers['last-modified'],
    }
    this.cached.delete(key)
    const oldestKey = this.cached.keys().next().value
    if (this.cached.size >= 32 && oldestKey !== undefined) this.cached.delete(oldestKey)
    this.cached.set(key, entry)
    return entry
  }

  async execute(target: Target, operation: UpstreamRequest): Promise<ExecutionResult> {
    const response = await this.send(target, operation)
    const { status, body } = response
    if (status < 200 || status >= 300) throw statusError(status)
    if (body.length === 0) return { status }
    const type = contentType(response.headers)
    if (isJson(type)) {
      let value: unknown
      try {
        value = JSON.parse(body.toString('utf8'))
      } catch {
        throw invalidResponse('Arcane returned invalid JSON.')
      }
      const result = { status, ...boundedData(value, target.apiKey) }
      if (Buffer.byteLength(JSON.stringify(result.data)) > this.config.maxResponseBytes) {
        return { status, truncated: true }
      }
      return result
    }
    if (type === 'text/plain') {
      const sanitized = sanitizeText(body.toString('utf8'), target.apiKey)
      return { status, data: sanitized.slice(0, 8192), truncated: sanitized.length > 8192 }
    }
    throw invalidResponse('Arcane returned an unsupported response media type.')
  }

  private async send(target: Target, operation: UpstreamRequest): Promise<UpstreamResponse> {
    if (this.active >= 16) throw new UpstreamError('upstream_busy', 503, 'The gateway upstream request limit is busy.')
    this.active++
    try {
      return await this.sendPinned(target, operation)
    } finally {
      this.active--
    }
  }

  private async sendPinned(target: Target, operation: UpstreamRequest): Promise<UpstreamResponse> {
    const origin = new URL(target.origin)
    const path = operation.path
    if (!path.startsWith('/') || path.startsWith('//') || /[\\#\s]/.test(path)) {
      throw new UpstreamError('invalid_path', 400, 'The operation path is invalid.')
    }
    const url = new URL(path, origin)
    if (url.origin !== target.origin || url.pathname + url.search !== path || path.length > 8192) {
      throw new UpstreamError('invalid_path', 400, 'The operation path is invalid or too long.')
    }
    const headers: Record<string, string> = Object.create(null)
    for (const [key, value] of Object.entries(operation.headers ?? {})) {
      const name = key.toLowerCase()
      if (!/^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name) || blockedRequestHeaders.has(name) ||
          name.startsWith('proxy-') || name.startsWith('x-forwarded-') || value.length > 4096) {
        throw new UpstreamError('invalid_header', 400, 'An operation header is invalid or reserved.')
      }
      try {
        validateHeaderValue(name, value)
      } catch {
        throw new UpstreamError('invalid_header', 400, 'An operation header has an invalid value.')
      }
      headers[name] = value
    }
    headers['x-api-key'] = target.apiKey
    headers.accept = 'application/json, text/plain'
    headers['accept-encoding'] = 'identity'
    if (operation.body !== undefined) {
      const length = Buffer.byteLength(operation.body)
      if (length > 1024 * 1024) throw new UpstreamError('request_too_large', 413, 'The request body exceeds the byte limit.')
      headers['content-type'] = 'application/json'
      headers['content-length'] = String(length)
    }
    const signal = AbortSignal.timeout(this.config.timeoutMs)
    const host = origin.hostname.replace(/^\[|\]$/g, '')
    const family = isIP(host)
    try {
      const addresses = family ? [{ address: host, family }] : await resolveHost(host, signal)
      const isHttp = origin.protocol === 'http:'
      const listed = this.config.allowedOrigins.has(target.origin)
      const selected = addresses[0]
      if (!selected || addresses.some(address => !addressAllowed(address.address, address.family, listed, isHttp))) {
        throw new UpstreamError('address_denied', 403, 'The Arcane origin resolved to a prohibited network address.')
      }
      const request = isHttp ? httpRequest : httpsRequest
      return await new Promise<UpstreamResponse>((resolve, reject) => {
        const req = request(url, {
          method: operation.method, headers, agent: false, signal, rejectUnauthorized: true,
          family: selected.family,
          lookup: (_hostname, _options, callback) => callback(null, selected.address, selected.family),
        }, response => {
          const status = response.statusCode ?? 502
          if (status === 304) {
            response.destroy()
            resolve({ status, headers: response.headers, body: Buffer.alloc(0) })
            return
          }
          if (status < 200 || status >= 300) {
            response.destroy()
            reject(statusError(status))
            return
          }
          const encoding = response.headers['content-encoding']
          if (encoding && encoding.toLowerCase() !== 'identity') {
            response.destroy()
            reject(invalidResponse('Arcane returned an unsupported content encoding.'))
            return
          }
          const chunks: Buffer[] = []
          let received = 0
          response.on('error', reject)
          response.on('data', (chunk: Buffer) => {
            received += chunk.length
            if (received > this.config.maxResponseBytes) {
              reject(new UpstreamError('response_too_large', 502, 'The Arcane response exceeds the byte limit.'))
              response.destroy()
              return
            }
            chunks.push(chunk)
          })
          response.on('end', () => resolve({ status, headers: response.headers, body: Buffer.concat(chunks, received) }))
        })
        req.on('error', reject)
        req.end(operation.body)
      })
    } catch (error) {
      if (error instanceof UpstreamError) throw error
      if (signal.aborted) throw new UpstreamError('upstream_timeout', 504, 'The Arcane request timed out.')
      throw new UpstreamError('upstream_unavailable', 502, 'The gateway could not contact Arcane.')
    }
  }
}

function statusError(status: number): UpstreamError {
  if (status >= 300 && status < 400) return new UpstreamError('upstream_redirect', 502, 'Arcane redirects are disabled.')
  return new UpstreamError('upstream_http_error', status, `Arcane returned HTTP ${status}.`)
}

async function resolveHost(host: string, signal: AbortSignal): Promise<Awaited<ReturnType<typeof lookup>>[]> {
  signal.throwIfAborted()
  let onAbort: (() => void) | undefined
  const aborted = new Promise<never>((_, reject) => {
    onAbort = () => reject(signal.reason)
    signal.addEventListener('abort', onAbort, { once: true })
  })
  try {
    return await Promise.race([lookup(host, { all: true }), aborted])
  } finally {
    if (onAbort) signal.removeEventListener('abort', onAbort)
  }
}
