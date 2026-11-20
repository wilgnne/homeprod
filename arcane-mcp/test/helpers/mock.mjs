import { createServer } from 'node:http';
import { networkInterfaces } from 'node:os';
import { readFile } from 'node:fs/promises';

// Deliberately synthetic; no production Arcane routes are used as gateway contracts.
export async function startMock() {
  const document = JSON.parse(await readFile(new URL('../fixtures/openapi.json', import.meta.url), 'utf8'));
  const requests = [];
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const entry = { method: req.method, url: req.url, key: req.headers['x-api-key'], body: Buffer.concat(chunks).toString() };
    requests.push(entry);
    res.setHeader('Content-Type', 'application/json');
    if (!['fixture-key', 'readonly-key'].includes(entry.key)) {
      res.writeHead(401).end(JSON.stringify({ error: 'unauthorized' }));
      return;
    }
    if (req.url === '/api/openapi.json') {
      res.setHeader('ETag', '"mock-v1"');
      res.end(JSON.stringify(document));
      return;
    }
    if (entry.key === 'readonly-key' && !['GET', 'HEAD'].includes(req.method)) {
      res.writeHead(403).end(JSON.stringify({ error: 'forbidden' }));
      return;
    }
    if (req.url.startsWith('/api/containers')) {
      res.end(JSON.stringify(req.url.split('?')[0] === '/api/containers'
        ? [{ id: 'demo', name: 'fixture-container' }]
        : { id: 'demo', name: 'fixture-container', method: req.method }));
      return;
    }
    res.writeHead(404).end(JSON.stringify({ error: 'not_found' }));
  });
  // Production egress policy denies loopback even when allowlisted. Bind only a
  // local RFC1918 interface and authorize its exact origin for this fixture.
  const host = Object.values(networkInterfaces()).flat().find(address =>
    address?.family === 'IPv4' && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(address.address))?.address;
  if (!host) throw new Error('Mock integration requires a local RFC1918 IPv4 interface (for example a Docker network).');
  await new Promise(resolve => server.listen(0, host, resolve));
  return { origin: `http://${host}:${server.address().port}`, requests, document,
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }) };
}
