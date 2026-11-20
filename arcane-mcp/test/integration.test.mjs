import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { startMock } from './helpers/mock.mjs';
import { connect, payload, operations } from './helpers/client.mjs';

async function unusedPort() {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function gateway(t, policy = 'all-with-approval') {
  const mock = await startMock();
  t.after(() => mock.close());
  const port = await unusedPort();
  // Replace inherited configuration so developer credentials never enter the fixture test.
  const child = spawn(process.execPath, ['dist/server.js'], {
    cwd: new URL('..', import.meta.url),
    env: { PATH: process.env.PATH, PORT: String(port), ARCANE_URL: mock.origin,
      ARCANE_API_KEY: 'fixture-key', MCP_AUTH_TOKEN: 'fixture-client-token',
      MCP_APPROVAL_ADMIN_TOKEN: 'fixture-human-admin-token', WRITE_POLICY: policy,
      MCP_ALLOWED_ORIGINS: mock.origin, MCP_ALLOW_HEADER_TARGETS: 'true',
      OPENAPI_TTL_SECONDS: '1', REQUEST_TIMEOUT_MS: '1000', LOG_LEVEL: 'info' },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  let logs = '';
  child.stdout.on('data', chunk => { logs += chunk; });
  child.stderr.on('data', chunk => { logs += chunk; });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      child.kill('SIGTERM');
      const timer = setTimeout(() => child.kill('SIGKILL'), 2000);
      await exited;
      clearTimeout(timer);
    }
    for (const secret of ['fixture-key', 'fixture-client-token', 'fixture-human-admin-token']) {
      assert.ok(!logs.includes(secret), 'logs must not contain fixture credentials');
    }
  });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error(`Gateway exited: ${logs}`);
    try { if ((await fetch(`${base}/readyz`)).ok) { ready = true; break; } } catch {}
    await delay(100);
  }
  assert.ok(ready, `Gateway did not become ready: ${logs}`);
  const client = await connect(`${base}/mcp`, 'fixture-client-token');
  t.after(() => client.close());
  return { mock, base, client };
}

async function search(client, query = 'containers') {
  return operations(payload(await client.callTool({ name: 'arcane_search', arguments: { query, limit: 20 } })));
}
async function rejectedCall(client, name, args) {
  try {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true, `Expected rejected operation: ${JSON.stringify(result)}`);
  } catch (error) {
    if (error.code === 'ERR_ASSERTION') throw error;
    assert.ok(error instanceof Error);
  }
}

// Every mutating call in this file targets only the disposable local simulator.
test('official SDK: initialize, tools/list, discovery, describe and execute', { timeout: 20000 }, async t => {
  const { client, base, mock } = await gateway(t);
  assert.deepEqual((await client.listTools()).tools.map(tool => tool.name).sort(),
    ['arcane_describe', 'arcane_execute', 'arcane_search']);
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  assert.equal((await fetch(`${base}/mcp`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 401);
  const found = await search(client, 'containers');
  const get = found.find(op => op.method.toUpperCase() === 'GET' && op.path === '/api/containers/{id}');
  assert.ok(get, 'fixture GET must be discovered from OpenAPI');
  assert.ok(payload(await client.callTool({ name: 'arcane_describe', arguments: { id: get.id } })));
  const result = await client.callTool({ name: 'arcane_execute', arguments: { id: get.id, args: { path: { id: 'demo' } } } });
  assert.notEqual(result.isError, true, JSON.stringify(result));
  assert.match(JSON.stringify(payload(result)), /fixture-container/);
  assert.ok(mock.requests.some(req => req.url === '/api/containers/demo' && req.key === 'fixture-key'));
  const before = mock.requests.length;
  await rejectedCall(client, 'arcane_execute', { id: get.id, args: { path: { id: 'demo' }, query: { undocumented: true } } });
  assert.equal(mock.requests.length, before, 'invalid arguments must not reach upstream');
  await rejectedCall(client, 'arcane_execute', { id: 'obsolete-catalog-operation', args: {} });
  const stream = found.find(op => op.path.endsWith('/logs'));
  if (stream) {
    assert.equal(stream.supported, false);
    await rejectedCall(client, 'arcane_execute', { id: stream.id, args: { path: { id: 'demo' } } });
  }
});

test('mock: mutation succeeds, Arcane 403 propagates, credentials remain per request', { timeout: 20000 }, async t => {
  const { client, base, mock } = await gateway(t);
  const op = (await search(client, 'restart')).find(op => op.path.endsWith('/restart'));
  assert.ok(op);
  const args = { path: { id: 'demo' }, body: { timeout: 1 } };
  const result = await client.callTool({ name: 'arcane_execute', arguments: { id: op.id, args } });
  assert.notEqual(result.isError, true, JSON.stringify(result));
  const readClient = await connect(`${base}/mcp`, 'fixture-client-token', {
    'X-Arcane-Url': mock.origin, 'X-Arcane-Api-Key': 'readonly-key'
  });
  t.after(() => readClient.close());
  const readOp = (await search(readClient, 'restart')).find(op => op.path.endsWith('/restart'));
  const forbidden = await readClient.callTool({ name: 'arcane_execute', arguments: { id: readOp.id, args } });
  assert.equal(forbidden.isError, true);
  assert.deepEqual(payload(forbidden), { error: 'upstream_http_error', status: 403, message: 'Arcane returned HTTP 403.' });
  assert.ok(mock.requests.some(req => req.method === 'POST' && req.key === 'readonly-key'));
  const again = await client.callTool({ name: 'arcane_execute', arguments: { id: op.id, args } });
  assert.notEqual(again.isError, true);
  assert.equal(mock.requests.at(-1).key, 'fixture-key');
});

test('mock: read-only policy blocks writes before upstream', { timeout: 20000 }, async t => {
  const { client, mock } = await gateway(t, 'read-only');
  const op = (await search(client, 'restart')).find(op => op.path.endsWith('/restart'));
  assert.ok(op);
  const before = mock.requests.length;
  await rejectedCall(client, 'arcane_execute', { id: op.id, args: { path: { id: 'demo' }, body: { timeout: 1 } } });
  assert.equal(mock.requests.length, before);
});

test('mock: destructive approval is independent, argument-bound and single-use', { timeout: 20000 }, async t => {
  const { client, mock, base } = await gateway(t);
  const op = (await search(client, 'delete')).find(op => op.method.toUpperCase() === 'DELETE');
  assert.ok(op);
  const input = { id: op.id, args: { path: { id: 'demo' } } };
  const before = mock.requests.filter(req => req.method === 'DELETE').length;
  const pending = payload(await client.callTool({ name: 'arcane_execute', arguments: input }));
  assert.equal(pending.error, 'approval_required');
  assert.ok(pending.approvalId);
  assert.equal(mock.requests.filter(req => req.method === 'DELETE').length, before);
  const endpoint = `${base}/admin/approvals/${pending.approvalId}/approve`;
  assert.equal((await fetch(endpoint, { method: 'POST', headers: { Authorization: 'Bearer fixture-client-token' } })).status, 401);
  const adminHeaders = { Authorization: 'Bearer fixture-human-admin-token' };
  assert.equal((await fetch(endpoint, { method: 'POST', headers: adminHeaders })).status, 200);
  await rejectedCall(client, 'arcane_execute', { ...input, args: { path: { id: 'different' } }, approvalId: pending.approvalId });
  assert.equal(mock.requests.filter(req => req.method === 'DELETE').length, before);
  const approved = { ...input, approvalId: pending.approvalId };
  assert.notEqual((await client.callTool({ name: 'arcane_execute', arguments: approved })).isError, true);
  assert.equal(mock.requests.filter(req => req.method === 'DELETE').length, before + 1);
  await rejectedCall(client, 'arcane_execute', approved);
  assert.equal(mock.requests.filter(req => req.method === 'DELETE').length, before + 1);
  const another = payload(await client.callTool({ name: 'arcane_execute', arguments: input }));
  assert.equal((await fetch(`${base}/admin/approvals/${another.approvalId}/reject`, { method: 'POST', headers: adminHeaders })).status, 200);
  await rejectedCall(client, 'arcane_execute', { ...input, approvalId: another.approvalId });
  assert.equal(mock.requests.filter(req => req.method === 'DELETE').length, before + 1);
});
