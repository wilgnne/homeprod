import { deepEqual, equal, rejects } from 'node:assert/strict'
import dns from 'node:dns'
import dnsPromises from 'node:dns/promises'
import { once } from 'node:events'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { syncBuiltinESMExports } from 'node:module'
import { networkInterfaces } from 'node:os'
import { setTimeout as delay } from 'node:timers/promises'
import { test } from 'node:test'
import { readUpstreamConfig, selectTarget, UpstreamClient } from '../dist/upstream.js'

const privateAddress = Object.values(networkInterfaces()).flat().find(entry =>
  entry && entry.family === 'IPv4' && !entry.internal &&
  /^(?:10\.|192\.168\.|172\.(?:1[6-9]|2[0-9]|3[01])\.)/.test(entry.address)
)?.address

test('upstream revalidates snapshots and isolates principals and credentials', { skip: !privateAddress }, async t => {
  const fixture = await readFile(new URL('./fixtures/openapi.json', import.meta.url))
  const calls = []
  const server = createServer((req, res) => {
    const conditional = req.headers['if-none-match']
    calls.push([req.headers['x-api-key'], conditional])
    res.setHeader('etag', '"revision-1"')
    if (conditional === '"revision-1"') return res.writeHead(304).end()
    res.setHeader('content-type', 'application/json')
    res.end(fixture)
  })
  const origin = await startFixture(t, server)
  const config = { ...fixtureConfig(origin), ttlMs: 10 }
  const client = new UpstreamClient(config)
  const first = selectTarget(config, {})
  const second = selectTarget(config, { 'x-arcane-url': origin, 'x-arcane-api-key': 'second-key' })

  const [snapshot, concurrent] = await Promise.all([client.getSnapshot(first, 'client-a'), client.getSnapshot(first, 'client-a')])
  equal(concurrent.catalog, snapshot.catalog)
  deepEqual(calls, [['first-key', undefined]])

  await delay(25)
  const revalidated = await client.getSnapshot(first, 'client-a')
  equal(revalidated.catalog, snapshot.catalog)
  equal(revalidated.hash, snapshot.hash)

  await client.getSnapshot(second, 'client-a')
  await client.getSnapshot(first, 'client-b')
  deepEqual(calls, [['first-key', undefined], ['first-key', '"revision-1"'], ['second-key', undefined], ['first-key', undefined]])
})

test('upstream stops at redirects and enforces byte and time limits', { skip: !privateAddress }, async t => {
  const paths = []
  const server = createServer((req, res) => {
    paths.push(req.url)
    if (req.url === '/redirect') return res.writeHead(302, { location: '/other' }).end()
    if (req.url === '/long') return res.writeHead(200, { 'content-type': 'text/plain' }).end('a'.repeat(129))
    if (req.url === '/slow') return
    res.writeHead(404).end()
  })
  const origin = await startFixture(t, server)
  const config = { ...fixtureConfig(origin), timeoutMs: 250, maxResponseBytes: 128 }
  const client = new UpstreamClient(config)
  const target = selectTarget(config, {})

  await rejects(client.execute(target, { method: 'GET', path: '/redirect' }), { code: 'upstream_redirect' })
  await rejects(client.execute(target, { method: 'GET', path: '/long' }), { code: 'response_too_large' })
  await rejects(client.execute(target, { method: 'GET', path: '/slow' }), { code: 'upstream_timeout' })
  deepEqual(paths, ['/redirect', '/long', '/slow'])
})

test('upstream denies non-allowlisted origins and special network destinations', async () => {
  const origin = 'http://127.0.0.1:3000'
  const config = fixtureConfig(origin)
  const client = new UpstreamClient(config)
  await rejects(client.execute(selectTarget(config, {}), { method: 'GET', path: '/' }), { code: 'address_denied' })
  await rejects(
    async () => selectTarget(config, { 'x-arcane-url': 'https://example.com', 'x-arcane-api-key': 'other-key' }),
    { code: 'origin_denied' },
  )
})

test('upstream connects through the checked DNS address and rejects a mixed result', { skip: !privateAddress }, async t => {
  const requestedHosts = []
  const server = createServer((req, res) => {
    requestedHosts.push(req.headers.host)
    res.setHeader('content-type', 'application/json')
    res.end('{"ready":true}')
  })
  await startFixture(t, server)
  const hostname = 'arcane-fixture.test'
  const host = `${hostname}:${server.address().port}`
  const config = fixtureConfig(`http://${host}`)
  const client = new UpstreamClient(config)
  const target = selectTarget(config, {})
  const operation = { method: 'GET', path: '/ready' }
  let addresses = [{ address: privateAddress, family: 4 }]
  const resolve = t.mock.method(dnsPromises, 'lookup', async (name, options) => {
    equal(name, hostname)
    equal(options.all, true)
    return addresses
  })
  const socketLookup = t.mock.method(dns, 'lookup', () => {
    throw new Error('The socket must use the checked address.')
  })
  syncBuiltinESMExports()
  try {
    deepEqual((await client.execute(target, operation)).data, Object.assign(Object.create(null), { ready: true }))
    equal(resolve.mock.callCount(), 1)
    deepEqual(requestedHosts, [host])

    addresses = [...addresses, { address: '127.0.0.1', family: 4 }]
    await rejects(client.execute(target, operation), { code: 'address_denied' })
    equal(resolve.mock.callCount(), 2)
    equal(socketLookup.mock.callCount(), 0)
    deepEqual(requestedHosts, [host])
  } finally {
    resolve.mock.restore()
    socketLookup.mock.restore()
    syncBuiltinESMExports()
  }
})

test('upstream excludes reflected credentials from catalogs and bounds sanitized results', { skip: !privateAddress }, async t => {
  const fixture = JSON.parse(await readFile(new URL('./fixtures/openapi.json', import.meta.url), 'utf8'))
  fixture.info.description = 'Reflected fixture credential first-key'
  const server = createServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify(req.url === '/api/openapi.json' ? fixture : { token: 'x' }))
  })
  const origin = await startFixture(t, server)
  const config = fixtureConfig(origin)
  const target = selectTarget(config, {})
  await rejects(new UpstreamClient(config).getSnapshot(target, 'client'), {
    code: 'invalid_upstream_response',
    message: 'The Arcane OpenAPI document contains reflected credentials.',
  })

  const client = new UpstreamClient({ ...config, maxResponseBytes: 16 })
  deepEqual(await client.execute(target, { method: 'GET', path: '/data' }), { status: 200, truncated: true })
})

async function startFixture(t, server) {
  server.listen(0, privateAddress)
  await once(server, 'listening')
  t.after(() => new Promise(resolve => {
    server.close(resolve)
    server.closeAllConnections()
  }))
  return `http://${privateAddress}:${server.address().port}`
}

function fixtureConfig(origin) {
  return readUpstreamConfig({
    ARCANE_URL: origin, ARCANE_API_KEY: 'first-key',
    MCP_ALLOWED_ORIGINS: origin, MCP_ALLOW_HEADER_TARGETS: 'true',
  })
}
