import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connect, payload, operations } from './helpers/client.mjs';

const enabled = process.env.ARCANE_REAL_TEST === '1';
test('opt-in real MCP: discovery and optional explicitly selected GET only', { skip: !enabled, timeout: 30000 }, async t => {
  const { REAL_MCP_URL, REAL_MCP_TOKEN, REAL_READ_PATH, REAL_READ_ARGS } = process.env;
  assert.ok(REAL_MCP_URL && REAL_MCP_TOKEN, 'REAL_MCP_URL and REAL_MCP_TOKEN required');
  const client = await connect(REAL_MCP_URL, REAL_MCP_TOKEN);
  t.after(() => client.close());
  assert.deepEqual((await client.listTools()).tools.map(tool => tool.name).sort(),
    ['arcane_describe', 'arcane_execute', 'arcane_search']);
  // Default real check does discovery only. No administrative approval requests.
  const found = operations(payload(await client.callTool({ name: 'arcane_search', arguments: {
    query: process.env.REAL_SEARCH_QUERY || 'containers', risk: 'read', limit: 20
  } })));
  assert.ok(found.length, 'No operations discovered; review real OpenAPI compatibility');
  const candidate = found.find(op => op.method.toUpperCase() === 'GET' && op.risk === 'read' && op.supported && (!REAL_READ_PATH || op.path === REAL_READ_PATH));
  assert.ok(candidate, 'No supported GET classified as read found');
  const described = await client.callTool({ name: 'arcane_describe', arguments: { id: candidate.id } });
  assert.notEqual(described.isError, true);
  if (!REAL_READ_PATH) return;
  const args = JSON.parse(REAL_READ_ARGS || '{}');
  assert.ok(Object.keys(args).every(key => ['path', 'query'].includes(key)), 'Real test allows only path/query arguments');
  const result = await client.callTool({ name: 'arcane_execute', arguments: { id: candidate.id, args } });
  assert.notEqual(result.isError, true, 'Real GET failed; inspect response without exposing credentials');
});
