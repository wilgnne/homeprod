import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Approvals, approvalBinding, GatewayError } from '../dist/approvals.js';

const input = { principal: 'mock-client', origin: 'https://mock.example', credential: 'mock-key',
  snapshot: 'mock-snapshot', operation: 'mock-delete', method: 'DELETE', path: '/mock/resource', args: {} };
const summary = { method: 'DELETE', path: input.path, title: 'Mock delete', origin: input.origin,
  arguments: {}, argumentsTruncated: false };
function denied(fn, code) {
  assert.throws(fn, error => error instanceof GatewayError && error.status === 403 && error.code === code);
}
function approved() {
  const store = new Approvals(() => {}, 60_000);
  const binding = approvalBinding(input);
  const entry = store.create(binding, summary);
  store.decide(entry.id, 'approve');
  return { store, binding, entry };
}

test('approval expires before consumption without waiting for wall clock', () => {
  const { store, binding, entry } = approved();
  store.sweep(Date.parse(entry.expiresAt));
  denied(() => store.consume(entry.id, binding), 'approval_expired');
});

for (const [field, value] of [['credential', 'different-key'], ['origin', 'https://other.example']]) {
  test(`approval rejects different ${field} without consuming the original`, () => {
    const { store, binding, entry } = approved();
    denied(() => store.consume(entry.id, approvalBinding({ ...input, [field]: value })), 'approval_invalid');
    store.consume(entry.id, binding);
    denied(() => store.consume(entry.id, binding), 'approval_consumed');
  });
}

test('simulated upstream failure after consumption cannot reuse approval', async () => {
  const { store, binding, entry } = approved();
  let attempts = 0;
  async function execute() {
    store.consume(entry.id, binding);
    attempts++;
    throw new Error('simulated upstream failure');
  }
  await assert.rejects(execute(), /simulated upstream failure/);
  await assert.rejects(execute(), error => error instanceof GatewayError &&
    error.status === 403 && error.code === 'approval_consumed');
  assert.equal(attempts, 1);
});
