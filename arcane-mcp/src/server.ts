import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { isIP } from "node:net";
import { pathToFileURL } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import { approvalBinding, Approvals, GatewayError, type Audit } from "./approvals.js";
import { CatalogError } from "./catalog.js";
import { readUpstreamConfig, selectTarget, UpstreamClient, UpstreamError } from "./upstream.js";

const MAX_REQUEST_BYTES = 128 * 1024;
const MAX_CONCURRENT_REQUESTS = 24;
const REQUESTS_PER_MINUTE = 180;
const MAX_CONCURRENT_ADMIN_REQUESTS = 4;
const ADMIN_REQUESTS_PER_MINUTE = 60;

function flag(env: NodeJS.ProcessEnv, name: string, fallback: boolean): boolean {
  const value = env[name];
  if (value === undefined || value === "") return fallback;
  if (value === "true" || value === "false") return value === "true";
  throw new Error(name + " must be true or false.");
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const raw = env[name];
  if (raw === undefined || raw === "") return fallback;
  const value = Number(raw);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(name + " is outside its allowed range.");
  }
  return value;
}

function readToken(env: NodeJS.ProcessEnv, name: string): string | undefined {
  const value = env[name] || undefined;
  if (value && !/^[!-~]{1,4096}$/.test(value)) throw new Error(name + " must be a valid bearer token.");
  return value;
}

function parseHosts(raw: string | undefined): Set<string> {
  const hosts = new Set<string>();
  for (const value of raw?.split(",") ?? []) {
    const host = value.trim().toLowerCase();
    if (!host) continue;
    if (!/^(?:[a-z0-9.-]+|\[[a-f0-9:]+\])(?::\d{1,5})?$/.test(host)) {
      throw new Error("MCP_ALLOWED_HOSTS must contain exact HTTP Host values.");
    }
    hosts.add(host);
  }
  return hosts;
}

function parseOrigins(raw: string | undefined): Set<string> {
  const origins = new Set<string>();
  for (const value of raw?.split(",") ?? []) {
    const origin = value.trim();
    if (!origin) continue;
    try {
      const url = new URL(origin);
      if (!["http:", "https:"].includes(url.protocol) || origin !== url.origin) throw new Error();
      origins.add(origin);
    } catch {
      throw new Error("MCP_ALLOWED_REQUEST_ORIGINS must contain exact HTTP origins.");
    }
  }
  return origins;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const authToken = readToken(env, "MCP_AUTH_TOKEN");
  const adminToken = readToken(env, "MCP_APPROVAL_ADMIN_TOKEN");
  if (!authToken) throw new Error("MCP_AUTH_TOKEN is required.");
  if (authToken === adminToken) throw new Error("The MCP and approval admin tokens must be different.");
  const writePolicy = env.WRITE_POLICY || "read-only";
  if (writePolicy !== "read-only" && writePolicy !== "all-with-approval") {
    throw new Error("WRITE_POLICY must be read-only or all-with-approval.");
  }
  const readOnly = flag(env, "READ_ONLY", false);
  const writes = writePolicy === "all-with-approval" && !readOnly;
  const allowMutations = flag(env, "ALLOW_MUTATIONS", true);
  const allowDestructive = flag(env, "ALLOW_DESTRUCTIVE", true) && writes;
  if (allowDestructive && !adminToken) {
    throw new Error("MCP_APPROVAL_ADMIN_TOKEN is required for destructive operations.");
  }
  const logLevel = env.LOG_LEVEL || "info";
  if (!["debug", "info", "warn", "error"].includes(logLevel)) {
    throw new Error("LOG_LEVEL must be debug, info, warn, or error.");
  }
  const bindHost = env.MCP_BIND_HOST || "127.0.0.1";
  if (isIP(bindHost) === 0) throw new Error("MCP_BIND_HOST must be an IPv4 or IPv6 address.");
  return {
    port: integer(env, "PORT", 3000, 1, 65535),
    bindHost,
    upstream: readUpstreamConfig(env),
    authToken,
    adminToken,
    allowMutations: writes && allowMutations,
    allowDestructive,
    approvalTtlMs: integer(env, "APPROVAL_TTL_SECONDS", 300, 1, 3600) * 1000,
    allowedHosts: parseHosts(env.MCP_ALLOWED_HOSTS),
    allowedOrigins: parseOrigins(env.MCP_ALLOWED_REQUEST_ORIGINS),
    logLevel,
  };
}

type Config = ReturnType<typeof loadConfig>;
type Level = "debug" | "info" | "warn" | "error";
type ToolOutput = Record<string, unknown>;
const levels: Level[] = ["debug", "info", "warn", "error"];

function makeLogger(config: Config) {
  return (level: Level, event: string, details: Record<string, string | number | boolean> = {}) => {
    if (levels.indexOf(level) < levels.indexOf(config.logLevel as Level)) return;
    console[level](JSON.stringify({ timestamp: new Date().toISOString(), level, event, ...details }));
  };
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value).digest();
}

function acceptsToken(req: IncomingMessage, expected: Buffer | undefined): boolean {
  const value = req.headersDistinct.authorization;
  if (!expected || value?.length !== 1) return false;
  const token = /^Bearer (\S+)$/i.exec(value[0] ?? "")?.[1];
  return token !== undefined && timingSafeEqual(expected, digest(token));
}

function privatePeer(address: string | undefined): boolean {
  if (!address) return false;
  const normalized = address.toLowerCase().replace(/^::ffff:/, "");
  if (normalized === "::1" || /^(?:fc|fd)[0-9a-f]{2}:/.test(normalized)) return true;
  const octets = normalized.split(".").map(Number);
  if (octets.length !== 4 || octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)) {
    return false;
  }
  const [first, second] = octets;
  return first === 127 || first === 10 || (first === 192 && second === 168)
    || (first === 172 && second !== undefined && second >= 16 && second <= 31)
    || (first === 100 && second !== undefined && second >= 64 && second <= 127);
}

function allowedHost(req: IncomingMessage, config: Config): boolean {
  const values = req.headersDistinct.host;
  if (values?.length !== 1) return false;
  const host = values[0]?.toLowerCase() ?? "";
  return /^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$/.test(host)
    || config.allowedHosts.has(host);
}

function sendJson(res: ServerResponse, status: number, data: unknown) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(data));
}

function publicError(error: unknown): GatewayError {
  if (error instanceof GatewayError) return error;
  if (error instanceof CatalogError || error instanceof UpstreamError) {
    return new GatewayError(error.status, error.code, error.message);
  }
  return new GatewayError(500, "internal_error", "The gateway could not complete the request.");
}

function redact(text: string, secrets: readonly (string | undefined)[]): string {
  for (const secret of secrets) {
    if (secret) text = text.replaceAll(secret, "[redacted]").replaceAll(encodeURIComponent(secret), "[redacted]");
  }
  return text;
}

function safeSummaryText(text: string, secrets: readonly (string | undefined)[]): string {
  return redact(text, secrets).replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, "");
}

function summarizeArguments(args: unknown, secrets: readonly (string | undefined)[]) {
  let remaining = 4096;
  let argumentsTruncated = false;
  const sensitive = /(?:password|secret|credential|private.?key|api.?key|passphrase|authorization|token|cookie|environment|(?:^|[-_])env(?:$|[-_]))/i;
  function summarize(value: unknown, depth: number): unknown {
    if (depth > 10 || remaining <= 0) {
      argumentsTruncated = true;
      return "[omitted]";
    }
    remaining -= 4;
    if (typeof value === "string") {
      const safe = safeSummaryText(value, secrets);
      const shown = safe.slice(0, Math.max(0, Math.min(remaining, 512)));
      remaining -= shown.length;
      if (shown.length < safe.length) argumentsTruncated = true;
      return shown;
    }
    if (Array.isArray(value)) {
      const shown: unknown[] = [];
      for (const item of value) {
        if (remaining <= 0) {
          argumentsTruncated = true;
          break;
        }
        shown.push(summarize(item, depth + 1));
      }
      return shown;
    }
    if (value !== null && typeof value === "object") {
      const shown: Record<string, unknown> = Object.create(null);
      for (const [key, item] of Object.entries(value)) {
        if (sensitive.test(key)) {
          argumentsTruncated = true;
          continue;
        }
        if (key.length + 4 > remaining) {
          argumentsTruncated = true;
          break;
        }
        remaining -= key.length + 4;
        shown[safeSummaryText(key, secrets)] = summarize(item, depth + 1);
      }
      return shown;
    }
    return value;
  }
  const summary = summarize(args, 0) as Record<string, unknown>;
  return { arguments: summary, argumentsTruncated };
}

function toolResult(data: ToolOutput, isError = false) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(data) }],
    structuredContent: data,
    ...(isError ? { isError: true } : {}),
  };
}

function checkJsonDepth(value: unknown, depth = 0): void {
  if (depth > 64) throw new GatewayError(400, "invalid_json", "JSON nesting exceeds the gateway limit.");
  if (value !== null && typeof value === "object") {
    if (Object.hasOwn(value, "__proto__")) {
      throw new GatewayError(400, "invalid_json", "JSON objects must omit reserved prototype properties.");
    }
    for (const child of Object.values(value)) checkJsonDepth(child, depth + 1);
  } else if (typeof value === "number" && !Number.isFinite(value)) {
    throw new GatewayError(400, "invalid_json", "JSON numbers must be finite.");
  }
}

async function parseMcpBody(req: IncomingMessage): Promise<unknown> {
  const type = req.headers["content-type"]?.split(";")[0]?.trim().toLowerCase();
  if (type !== "application/json") {
    throw new GatewayError(415, "invalid_content_type", "Use application/json for MCP requests.");
  }
  if (Number(req.headers["content-length"]) > MAX_REQUEST_BYTES) {
    throw new GatewayError(413, "request_too_large", "MCP request exceeds the size limit.");
  }
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req.iterator({ destroyOnReturn: false })) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > MAX_REQUEST_BYTES) {
      throw new GatewayError(413, "request_too_large", "MCP request exceeds the size limit.");
    }
    chunks.push(bytes);
  }
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)));
  } catch {
    throw new GatewayError(400, "invalid_json", "MCP request must contain valid JSON.");
  }
  checkJsonDepth(body);
  return body;
}

const operationId = z.string().min(1).max(200);
const searchInput = z.strictObject({
  query: z.string().trim().min(1).max(300),
  category: z.string().trim().min(1).max(100).optional(),
  risk: z.enum(["read", "write", "destructive"]).optional(),
  limit: z.number().int().min(1).max(20).default(8),
});
const describeInput = z.strictObject({ id: operationId });
const executeInput = z.strictObject({
  id: operationId,
  args: z.record(z.string(), z.unknown()),
  approvalId: z.uuid().optional(),
});

function requestBudget(maxActive: number, maxPerMinute: number) {
  let active = 0;
  let expiresAt = 0;
  let count = 0;
  return {
    enter(res: ServerResponse) {
      const now = Date.now();
      if (expiresAt <= now) {
        expiresAt = now + 60_000;
        count = 0;
      }
      if (++count > maxPerMinute || active >= maxActive) {
        const retryAfter = active >= maxActive ? 1 : Math.ceil((expiresAt - now) / 1000);
        res.setHeader("Retry-After", String(retryAfter));
        throw new GatewayError(429, "rate_limited", "Gateway request limit exceeded.");
      }
      active++;
    },
    leave() { active--; },
  };
}

export function createGateway(config: Config) {
  const log = makeLogger(config);
  const upstream = new UpstreamClient(config.upstream);
  const audit: Audit = (event, details) => {
    console.info(JSON.stringify({ timestamp: new Date().toISOString(), level: "info", event, ...details }));
  };
  const approvals = new Approvals(audit, config.approvalTtlMs);
  const sweep = setInterval(() => approvals.sweep(), Math.min(config.approvalTtlMs, 30_000));
  sweep.unref();
  const authDigest = digest(config.authToken);
  const adminDigest = config.adminToken ? digest(config.adminToken) : undefined;
  const principal = authDigest.toString("hex");
  const mcpBudget = requestBudget(MAX_CONCURRENT_REQUESTS, REQUESTS_PER_MINUTE);
  const adminBudget = requestBudget(MAX_CONCURRENT_ADMIN_REQUESTS, ADMIN_REQUESTS_PER_MINUTE);

  async function handleMcp(req: IncomingMessage, res: ServerResponse) {
    if (!acceptsToken(req, authDigest)) {
      res.setHeader("WWW-Authenticate", "Bearer");
      throw new GatewayError(401, "unauthorized", "A valid MCP bearer token is required.");
    }
    const origins = req.headersDistinct.origin;
    if (origins && (origins.length !== 1 || !config.allowedOrigins.has(origins[0] ?? ""))) {
      throw new GatewayError(403, "origin_denied", "The browser origin is not allowed.");
    }
    if (req.method !== "POST") {
      res.setHeader("Allow", "POST");
      throw new GatewayError(405, "method_not_allowed", "This stateless MCP endpoint accepts POST.");
    }
    if ((req.headersDistinct["x-arcane-url"]?.length ?? 0) > 1
      || (req.headersDistinct["x-arcane-api-key"]?.length ?? 0) > 1) {
      throw new GatewayError(400, "invalid_target_headers", "Supply one Arcane URL header and one API key header.");
    }
    const target = selectTarget(config.upstream, req.headers);
    const body = await parseMcpBody(req);
    const secrets = [config.authToken, config.adminToken, target.apiKey, config.upstream.defaultTarget?.apiKey];
    const mcp = new McpServer({ name: "arcane-mcp", version: "0.1.0" });
    const transport = new StreamableHTTPServerTransport({ enableJsonResponse: true });
    const call = async (handler: () => Promise<ToolOutput>) => {
      try {
        const data = await handler();
        return toolResult(data, typeof data.error === "string");
      } catch (error) {
        const issue = publicError(error);
        log(issue.status >= 500 ? "warn" : "debug", "tool_error", { code: issue.code, status: issue.status });
        return toolResult({ error: issue.code, status: issue.status, message: redact(issue.message, secrets) }, true);
      }
    };

    mcp.registerTool("arcane_search", {
      description: "Find operations in the configured Arcane OpenAPI catalog.",
      inputSchema: searchInput,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    }, (input) => call(async () => {
      const { catalog } = await upstream.getSnapshot(target, principal);
      return { operations: catalog.search(input) };
    }));
    mcp.registerTool("arcane_describe", {
      description: "Get the argument schema, risk, response format, and approval requirements for an Arcane operation.",
      inputSchema: describeInput,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
    }, ({ id }) => call(async () => {
      const { catalog } = await upstream.getSnapshot(target, principal);
      return { ...catalog.describe(id) };
    }));
    mcp.registerTool("arcane_execute", {
      description: "Execute an operation using its catalog ID. Destructive actions require approval through the separate administrator endpoint.",
      inputSchema: executeInput,
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    }, ({ id, args, approvalId }) => call(async () => {
      const { catalog, hash } = await upstream.getSnapshot(target, principal);
      const operation = catalog.prepare(id, args);
      const needsApproval = operation.risk === "destructive" || operation.risk === "unknown";
      if (operation.risk === "blocked" || (operation.risk === "write" && !config.allowMutations)
        || (needsApproval && !config.allowDestructive)) {
        throw new GatewayError(403, "policy_denied", "The active write policy denies this operation.");
      }
      if (approvalId && !needsApproval) {
        throw new GatewayError(403, "approval_invalid", "The approval does not match this request.");
      }
      if (needsApproval) {
        const binding = approvalBinding({
          principal, origin: target.origin, credential: target.credentialFingerprint,
          snapshot: hash, operation: id, method: operation.method, path: operation.path, args: operation.canonicalArgs,
        });
        if (!approvalId) {
          const approval = approvals.create(binding, {
            origin: target.origin,
            method: operation.method,
            path: safeSummaryText(operation.path.split("?")[0] ?? "", secrets),
            title: safeSummaryText(operation.title, secrets),
            ...summarizeArguments(operation.canonicalArgs, secrets),
          });
          return {
            error: "approval_required", approvalId: approval.id, expiresAt: approval.expiresAt, operation: approval,
            message: "Review and approve the request through the separate administrative endpoint, then retry with the same arguments and approvalId.",
          };
        }
        approvals.consume(approvalId, binding);
      }

      let status = 500;
      try {
        const result = await upstream.execute(target, operation);
        status = result.status;
        return {
          ...result,
          ...(result.truncated ? {
            warning: "The response was truncated. Use filters or pagination documented for this operation.",
          } : {}),
        };
      } catch (error) {
        status = publicError(error).status;
        throw error;
      } finally {
        if (needsApproval && approvalId) audit("approval_executed", { approvalId, status, success: status < 400 });
        else log("info", "operation_completed", { method: operation.method, status, risk: operation.risk });
      }
    }));

    res.setHeader("Cache-Control", "no-store");
    await mcp.connect(transport);
    try {
      await transport.handleRequest(req, res, body);
    } finally {
      await mcp.close();
    }
  }

  const server = createServer({ requestTimeout: 15_000, headersTimeout: 10_000 }, (req, res) => {
    void (async () => {
      try {
        const path = req.url ?? "";
        if ((path === "/healthz" || path === "/readyz") && req.method === "GET") {
          return sendJson(res, 200, { status: path === "/healthz" ? "ok" : "ready" });
        }
        if (!allowedHost(req, config)) throw new GatewayError(403, "host_denied", "The HTTP Host is not allowed.");
        const adminMatch = /^\/admin\/approvals\/([a-f0-9-]{36})\/(approve|reject)$/.exec(path);
        const isAdmin = path === "/admin/approvals" || adminMatch !== null;
        if (path !== "/mcp" && !isAdmin) throw new GatewayError(404, "not_found", "Unknown gateway endpoint.");
        const budget = isAdmin ? adminBudget : mcpBudget;
        budget.enter(res);
        try {
          if (!isAdmin) return await handleMcp(req, res);
          if (!privatePeer(req.socket.remoteAddress) || req.headers.origin !== undefined) {
            throw new GatewayError(403, "admin_denied", "Approval administration requires a private non-browser connection.");
          }
          if (!acceptsToken(req, adminDigest)) {
            res.setHeader("WWW-Authenticate", "Bearer");
            throw new GatewayError(401, "unauthorized", "A valid approval administrator bearer token is required.");
          }
          if (path === "/admin/approvals" && req.method === "GET") return sendJson(res, 200, { approvals: approvals.list() });
          if (adminMatch && req.method === "POST") {
            return sendJson(res, 200, approvals.decide(adminMatch[1]!, adminMatch[2] === "approve" ? "approve" : "reject"));
          }
          res.setHeader("Allow", adminMatch ? "POST" : "GET");
          throw new GatewayError(405, "method_not_allowed", "Unsupported approval administrator method.");
        } finally {
          budget.leave();
        }
      } catch (error) {
        const issue = publicError(error);
        if (issue.status >= 500) log("error", "request_error", { code: issue.code, status: issue.status });
        if (res.headersSent) {
          res.end();
          return;
        }
        if (!req.complete) res.setHeader("Connection", "close");
        const message = redact(issue.message, [config.authToken, config.adminToken, config.upstream.defaultTarget?.apiKey]);
        if (req.url === "/mcp") {
          return sendJson(res, issue.status, { jsonrpc: "2.0", id: null, error: {
            code: issue.code === "invalid_json" ? -32700 : -32000, message, data: { code: issue.code },
          } });
        }
        sendJson(res, issue.status, { error: issue.code, message });
      }
    })();
  });
  server.on("close", () => clearInterval(sweep));
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const config = loadConfig();
    createGateway(config).listen(config.port, config.bindHost, () => {
      makeLogger(config)("info", "gateway_listening", { host: config.bindHost, port: config.port });
    });
  } catch (error) {
    console.error(JSON.stringify({ level: "error", event: "configuration_error", message: error instanceof Error ? error.message : "Invalid configuration." }));
    process.exitCode = 1;
  }
}
