import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { compileCatalog, CatalogError } from '../dist/catalog.js';

// Provenance: official Arcane 3.0.0 export supplied at
// /tmp/triton-arcane-v3.0.0.json, captured from the binary on 2026-11-19.
// The gzip fixture preserves the complete original bytes, including all refs,
// components and the original http://localhost:/api server. No live API calls.
const bytes = gunzipSync(readFileSync(new URL('./fixtures/arcane-v3.0.0.openapi.json.gz', import.meta.url)));
const hash = createHash('sha256').update(bytes).digest('hex');
const document = JSON.parse(bytes);
const catalog = compileCatalog(document, hash);

function discover(query, path) {
  const operation = catalog.search({ query, risk: 'read', limit: 20 })
    .find(item => item.method === 'GET' && item.path === path);
  assert.ok(operation, `Official export must expose GET ${path} in search(${query})`);
  assert.equal(operation.supported, true, operation.reason_if_unsupported);
  assert.equal(operation.risk, 'read');
  const description = catalog.describe(operation.id);
  assert.equal(description.id, operation.id);
  assert.equal(description.path, path);
  assert.equal(description.supported, true);
  assert.equal(description.schema.type, 'object');
  assert.equal(description.schema.additionalProperties, false);
  assert.ok(description.response_types.includes('application/json'));
  assert.equal(description.approval.required, false);
  assert.equal(description.approval.conditional, false);
  return operation;
}

function invalid(operation, args) {
  assert.throws(() => catalog.prepare(operation.id, args), error =>
    error instanceof CatalogError && error.code === 'invalid_arguments');
}

test('official fixture preserves complete Arcane 3.0.0 export and /api server base', () => {
  assert.equal(hash, 'bfd7be4fc6ffc77890aed4cb96dcd94de54d78931337068cfb34f1de040cb6cf');
  assert.equal(document.openapi, '3.1.0');
  assert.equal(document.info.version, '3.0.0');
  assert.deepEqual(document.servers, [{ url: 'http://localhost:/api' }]);
  assert.ok(document.paths['/agents/{id}/containers']);
  assert.equal(document.paths['/api/agents'], undefined);
});

test('agents discovery works despite Environments tag and listEnvironments operationId', () => {
  assert.equal(document.paths['/agents'].get.operationId, 'listEnvironments');
  assert.deepEqual(document.paths['/agents'].get.tags, ['Environments']);
  const operation = discover('agents', '/api/agents');
  const request = catalog.prepare(operation.id, { query: { search: 'home lab', start: 0, limit: 5 } });
  assert.equal(request.method, 'GET');
  const url = new URL(request.path, 'https://fixture.invalid');
  assert.equal(url.pathname, '/api/agents');
  assert.equal(url.searchParams.get('search'), 'home lab');
  assert.equal(url.searchParams.get('start'), '0');
  assert.equal(url.searchParams.get('limit'), '5');
  assert.equal(request.body, undefined);
  invalid(operation, { query: { undocumented: true } });
  invalid(operation, { query: { limit: '5' } });
});

for (const resource of ['containers', 'projects']) {
  test(`${resource} list prepares agent scope, real query types and /api exactly once`, () => {
    const operation = discover(`list ${resource}`, `/api/agents/{id}/${resource}`);
    const query = { search: 'web & db', start: 0, limit: 10 };
    if (resource === 'containers') query.includeInternal = false;
    else query.tags = 'home,production';
    const args = { path: { id: 'agent east' }, query };
    const request = catalog.prepare(operation.id, args);
    assert.equal(request.method, 'GET');
    assert.equal(request.risk, 'read');
    const url = new URL(request.path, 'https://fixture.invalid');
    assert.equal(url.pathname, `/api/agents/agent%20east/${resource}`);
    assert.equal(url.searchParams.get('search'), query.search);
    assert.equal(url.searchParams.get('limit'), '10');
    if (resource === 'containers') assert.equal(url.searchParams.get('includeInternal'), 'false');
    else assert.equal(url.searchParams.get('tags'), query.tags);
    assert.equal(request.body, undefined);
    invalid(operation, { query });
    invalid(operation, { ...args, path: { id: 'agent/east' } });
    invalid(operation, { ...args, query: { ...query, start: 0.5 } });
    invalid(operation, { ...args, query: { ...query, undocumented: true } });
    if (resource === 'containers') invalid(operation, { ...args, query: { includeInternal: 'false' } });
  });

  test(`${resource} detail requires agent and resource identifiers`, () => {
    const parameter = resource === 'containers' ? 'containerId' : 'projectId';
    const operation = discover(`get ${resource}`, `/api/agents/{id}/${resource}/{${parameter}}`);
    const request = catalog.prepare(operation.id, { path: { id: 'local', [parameter]: 'web service' } });
    assert.equal(request.path, `/api/agents/local/${resource}/web%20service`);
    assert.equal(request.method, 'GET');
    invalid(operation, { path: { id: 'local' } });
    invalid(operation, { path: { [parameter]: 'web' } });
    invalid(operation, { path: { id: 'local', [parameter]: '../web' } });
  });
}

test('operation IDs from the official snapshot cannot be reused with another snapshot', () => {
  const operation = discover('agents', '/api/agents');
  const changed = compileCatalog(document, 'different-snapshot');
  assert.throws(() => changed.prepare(operation.id, {}), CatalogError);
});
