import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

export async function connect(url, token, headers = {}) {
  const client = new Client({ name: 'arcane-mcp-integration', version: '1.0.0' });
  try {
    await client.connect(new StreamableHTTPClientTransport(new URL(url), {
      requestInit: { headers: { Authorization: `Bearer ${token}`, ...headers } },
      reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 10, maxReconnectionDelay: 10, reconnectionDelayGrowFactor: 1 }
    }));
    return client;
  } catch (error) {
    await client.close().catch(() => {});
    throw error;
  }
}

export function payload(result) {
  if (result.structuredContent) return result.structuredContent;
  const text = result.content?.filter(item => item.type === 'text').map(item => item.text).join('\n');
  try { return JSON.parse(text); } catch { throw new Error(`Expected JSON tool result: ${text}`); }
}

export function operations(value) {
  if (Array.isArray(value)) return value;
  for (const key of ['operations', 'results', 'items']) if (Array.isArray(value?.[key])) return value[key];
  throw new Error(`Expected operation list: ${JSON.stringify(value)}`);
}
