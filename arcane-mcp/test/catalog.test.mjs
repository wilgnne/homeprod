import assert from 'node:assert/strict';
import { test } from 'node:test';
import { compileCatalog } from '../dist/catalog.js';

const documentFor = (paths, components) => ({
  openapi: '3.1.0',
  info: { title: 'Fixture', version: '1' },
  paths,
  components,
});
const json = { '200': { description: 'Result', content: { 'application/json': {} } } };
const pathId = { in: 'path', name: 'id', required: true, schema: { type: 'string' } };

test('validates local request schemas and omits transport-owned header definitions', () => {
  const doc = documentFor({
    '/containers/{id}/restart': { post: {
      operationId: 'restartContainer',
      parameters: [pathId, { in: 'header', name: 'X-Api-Key', schema: { type: 'string' } }],
      requestBody: { required: true, content: { 'application/json': { schema: { $ref: '#/components/schemas/Options' } } } },
      responses: json,
    } },
  }, { schemas: { Options: { type: 'object', required: ['timeout'], properties: { timeout: { type: 'integer', minimum: 1 } } } } });
  const catalog = compileCatalog(doc, 'first');
  const [operation] = catalog.search({ query: 'restart', risk: 'write' });
  assert.ok(operation.supported);
  const description = catalog.describe(operation.id);
  assert.ok(!JSON.stringify(description).includes('X-Api-Key'));
  const valid = { path: { id: 'web app' }, body: { timeout: 5 } };
  assert.equal(catalog.prepare(operation.id, valid).path, '/api/containers/web%20app/restart');
  assert.equal(catalog.prepare(operation.id, valid).body, '{"timeout":5}');
  for (const args of [
    { ...valid, headers: { 'X-Api-Key': 'value' } },
    { ...valid, body: { timeout: 5, extra: true } },
    { ...valid, body: { timeout: 0 } },
  ]) {
    assert.throws(() => catalog.prepare(operation.id, args), { code: 'invalid_arguments' });
  }
});

test('validates action choices and elevates unreviewed or destructive values', () => {
  const document = documentFor({
    '/containers/{id}': { post: {
      operationId: 'containerAction',
      parameters: [pathId, { in: 'query', name: 'action', required: true, schema: { type: 'string', enum: ['restart', 'prune', 'custom'] } }],
      responses: json,
    } },
  });
  const catalog = compileCatalog(document, 'first');
  const [operation] = catalog.search({ query: 'restart container', risk: 'write' });
  for (const [action, risk] of [['restart', 'write'], ['prune', 'destructive'], ['custom', 'destructive']]) {
    const prepared = catalog.prepare(operation.id, { path: { id: 'web' }, query: { action } });
    assert.equal(prepared.risk, risk);
    assert.equal(prepared.path, '/api/containers/web?action=' + action);
  }
  assert.throws(() => catalog.prepare(operation.id, { path: { id: 'web' }, query: { action: 'missing' } }), { code: 'invalid_arguments' });
});

test('keeps static operations distinct from parameterized operations', () => {
  const catalog = compileCatalog(documentFor({
    '/containers/{id}': { get: { operationId: 'getContainer', parameters: [pathId], responses: json } },
    '/containers/counts': { get: { operationId: 'containerCounts', responses: json } },
  }), 'first');
  const operations = catalog.search({ query: 'containers', risk: 'read' });
  const get = operations.find(operation => operation.path.endsWith('/{id}'));
  const counts = operations.find(operation => operation.path.endsWith('/counts'));
  assert.equal(catalog.prepare(get.id, { path: { id: 'web' } }).path, '/api/containers/web');
  assert.throws(() => catalog.prepare(get.id, { path: { id: 'counts' } }), { code: 'invalid_arguments' });
  assert.equal(catalog.prepare(counts.id, {}).path, '/api/containers/counts');
});
