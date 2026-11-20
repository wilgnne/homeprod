import { createHash } from 'node:crypto';
import { Ajv2020 } from 'ajv/dist/2020.js';
import type { ValidateFunction } from 'ajv';
import addFormats from 'ajv-formats';

type RecordValue = Record<string, unknown>;
export type Risk = 'read' | 'write' | 'destructive' | 'unknown' | 'blocked';
type Location = 'path' | 'query' | 'headers';
type SearchOptions = { query: string; category?: string; risk?: 'read' | 'write' | 'destructive'; limit?: number };

export interface PreparedOperation {
  id: string;
  method: string;
  path: string;
  headers: Record<string, string>;
  body?: string;
  risk: Risk;
  title: string;
  canonicalArgs: RecordValue;
  queryKeys: string[];
}

interface Parameter {
  location: Location;
  name: string;
  required: boolean;
  schema: RecordValue;
  style: string;
  explode: boolean;
  description: string;
}

interface Operation {
  id: string;
  title: string;
  summary: string;
  method: string;
  path: string;
  risk: Risk;
  supported: boolean;
  reason_if_unsupported?: string;
  required_permissions?: string[];
  tags: string[];
  operationId: string;
  parameters: Parameter[];
  responseTypes: string[];
  schema?: RecordValue;
  validate?: ValidateFunction;
}

export class CatalogError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
  }
}

const methods = new Set(['get', 'head', 'post', 'put', 'patch', 'delete']);
const safeHeaders = new Set(['if-match', 'if-none-match', 'if-modified-since', 'if-unmodified-since']);
const safeResources = new Set(['agents', 'containers', 'projects', 'images', 'networks', 'volumes']);
const readActions = new Set(['list', 'get', 'inspect', 'status', 'stats', 'summary', 'details', 'search', 'counts', 'processes', 'sizes', 'usage', 'topology', 'tags', 'runtime', 'directories', 'references', 'history', 'attestations']);
const writeActions = new Set(['start', 'stop', 'restart', 'pause', 'unpause', 'kill', 'rename', 'create', 'update', 'edit', 'set', 'pull', 'build', 'deploy', 'tag', 'scale', 'connect', 'disconnect', 'commit', 'recreate', 'redeploy', 'up', 'down', 'unarchive', 'updateservices']);
const blocked = /\b(?:api ?keys?|secrets?|tokens?|credentials?|passwords?|authentication|authorization|auth|federated|roles|users|permissions|terminal|exec|websocket|oauth|oidc|mfa|passkeys?|workspace|keyring|encryption)\b/;
const destructive = /\b(?:prune|delete|remove|purge|destroy|restore|wipe|reset|overwrite)\b/;
const destructiveArgument = /\b(?:force|prune|delete|remove|purge|destroy|restore|wipe|reset|overwrite)\b/;
const ajv = new Ajv2020({ strict: false, validateFormats: true, allErrors: false, ownProperties: true, addUsedSchema: false, logger: false });
const registerFormats = addFormats as unknown as (instance: Ajv2020) => void;
registerFormats(ajv);

function object(value: unknown): RecordValue | undefined {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as RecordValue : undefined;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
}

function excerpt(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.slice(0, limit * 2).replace(/[\u0000-\u001f\u007f-\u009f]+/g, ' ').trim().slice(0, limit) : '';
}

function terms(value: string): string {
  return value.replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function rejectExternalRefs(value: unknown, depth = 0, budget = { remaining: 200_000 }): void {
  if (depth > 72 || --budget.remaining < 0) throw new CatalogError(502, 'invalid_openapi', 'OpenAPI structure exceeds limits.');
  const entry = object(value);
  if (entry) {
    if ('$dynamicRef' in entry || ('$ref' in entry && (typeof entry.$ref !== 'string' || !entry.$ref.startsWith('#/')))) {
      throw new CatalogError(502, 'invalid_openapi', 'OpenAPI contains unsupported references.');
    }
    for (const child of Object.values(entry)) rejectExternalRefs(child, depth + 1, budget);
  } else if (Array.isArray(value)) {
    for (const child of value) rejectExternalRefs(child, depth + 1, budget);
  }
}

function resolver(document: RecordValue) {
  let remaining = 600_000;
  return function resolve(value: unknown, depth = 0, active = new Set<string>(), operationBudget = { remaining: 18_000 }): unknown {
    if (depth > 56 || --remaining < 0 || --operationBudget.remaining < 0) throw new Error('OpenAPI operation reference expansion exceeds limits.');
    if (Array.isArray(value)) return value.map((child) => resolve(child, depth + 1, active, operationBudget));
    const entry = object(value);
    if (!entry) return value;

    const fields = Object.fromEntries(Object.entries(entry)
      .filter(([key]) => key !== '$ref')
      .map(([key, child]) => [key, resolve(child, depth + 1, active, operationBudget)]));
    if (typeof entry.$ref !== 'string') return fields;
    if (active.has(entry.$ref)) throw new Error('Recursive OpenAPI references are unsupported.');
    const target = decodeURIComponent(entry.$ref.slice(1)).slice(1).split('/').reduce<unknown>((node, segment) => {
      const key = segment.replace(/~1/g, '/').replace(/~0/g, '~');
      return (object(node) || Array.isArray(node)) && Object.hasOwn(node as object, key) ? (node as RecordValue)[key] : undefined;
    }, document);
    if (target === undefined) throw new Error('OpenAPI reference could not be resolved.');
    const expanded = resolve(target, depth + 1, new Set([...active, entry.$ref]), operationBudget);
    if (typeof expanded === 'boolean' && Object.keys(fields).length === 0) return expanded;
    const expandedObject = object(expanded);
    if (!expandedObject) throw new Error('OpenAPI reference target is unsupported.');
    // Structural OpenAPI references only allow annotation overrides. Preserve the
    // conjunction for JSON Schema references that include validation siblings.
    if (Object.keys(fields).some((key) => key !== 'description' && key !== 'summary')) {
      if ('name' in expandedObject || 'content' in expandedObject || 'responses' in expandedObject) {
        throw new Error('OpenAPI reference has unsupported sibling fields.');
      }
      return { allOf: [expandedObject, fields] };
    }
    return { ...expandedObject, ...fields };
  };
}

const schemaMaps = new Set(['properties', 'patternProperties', 'dependentSchemas']);
const schemaLists = new Set(['allOf', 'anyOf', 'oneOf', 'prefixItems']);
const schemaFields = new Set(['items', 'contains', 'additionalProperties', 'unevaluatedProperties', 'unevaluatedItems', 'propertyNames', 'not', 'if', 'then', 'else']);
const scalarConstraints = new Set(['type', 'enum', 'const', 'format', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'uniqueItems', 'minContains', 'maxContains', 'minProperties', 'maxProperties', 'required', 'dependentRequired', 'readOnly', 'writeOnly']);

function checkPattern(value: unknown): void {
  if (typeof value !== 'string' || value.length > 256) throw new Error('JSON Schema pattern exceeds limits.');
  // Accept character classes, literals and anchors, with a single repetition.
  // Restrict patterns from an upstream document before running them in Node.
  const tokens = value.replace(/\[(?:\\.|[^\]\\])*\]|\\[pP]\{[A-Za-z_]+\}|\\[^1-9k]/g, 'x');
  const quantifiers = tokens.match(/[+*?]|\{\d+(?:,\d*)?\}/g) ?? [];
  const unbounded = quantifiers.some((entry) => entry === '*' || entry === '+' || entry.endsWith(',}'));
  if (/[()|\\]/.test(tokens) || quantifiers.length > 1 || (unbounded && !tokens.startsWith('^')) ||
      quantifiers.some((entry) => /\d{4}/.test(entry))) {
    throw new Error('JSON Schema pattern is unsupported.');
  }
}

function strictSchema(value: unknown, compositionBranch = false): RecordValue | boolean {
  if (typeof value === 'boolean') return value;
  const input = object(value);
  if (!input) throw new Error('Invalid JSON Schema.');
  const output: RecordValue = {};
  for (const [key, child] of Object.entries(input)) {
    if (schemaMaps.has(key)) {
      const map = object(child);
      if (!map) throw new Error('Invalid JSON Schema map.');
      if (key === 'patternProperties') for (const name of Object.keys(map)) checkPattern(name);
      output[key] = Object.fromEntries(Object.entries(map).map(([name, schema]) => [
        name,
        key === 'properties' && object(schema)?.readOnly === true ? false : strictSchema(schema, key === 'dependentSchemas'),
      ]));
    } else if (schemaLists.has(key)) {
      if (!Array.isArray(child)) throw new Error('Invalid JSON Schema composition.');
      output[key] = child.map((schema) => strictSchema(schema, key !== 'prefixItems'));
    } else if (schemaFields.has(key)) {
      output[key] = strictSchema(child, ['not', 'if', 'then', 'else'].includes(key));
    } else if (scalarConstraints.has(key)) {
      if (key === 'pattern') checkPattern(child);
      output[key] = child;
    } else if (key === 'description') {
      output[key] = excerpt(child, 160);
    }
  }
  if (object(input.properties) && Array.isArray(output.required)) {
    output.required = output.required.filter((key) => typeof key !== 'string' || object(object(input.properties)?.[key])?.readOnly !== true);
  }

  if (!compositionBranch && !('additionalProperties' in output) && !('unevaluatedProperties' in output)) {
    if (['allOf', 'anyOf', 'oneOf', 'dependentSchemas', 'then', 'else'].some((key) => key in output)) {
      output.unevaluatedProperties = false;
    } else if (output.type === undefined || output.type === 'object' || strings(output.type).includes('object') || 'properties' in output || 'patternProperties' in output) {
      output.additionalProperties = false;
    }
  }
  return output;
}

function permissions(operation: RecordValue): string[] | undefined {
  const extension = operation['x-required-permissions'] ?? operation['x-arcane-permissions'];
  const values = (typeof extension === 'string' ? [extension] : strings(extension))
    .map((value) => excerpt(value, 100)).filter(Boolean).slice(0, 30);
  return values.length ? values : undefined;
}

function classify(operation: Operation): Risk {
  const segments = operation.path.split('/').filter((segment) => segment && !segment.startsWith('{'));
  const names = terms([...segments, operation.operationId, ...operation.tags].join(' '));
  const permissionNames = terms((operation.required_permissions ?? []).join(' '));
  if (blocked.test(names) || blocked.test(permissionNames)) return 'blocked';
  if (operation.method === 'DELETE' || destructive.test(names) || destructive.test(permissionNames) ||
      operation.parameters.some((parameter) => destructiveArgument.test(terms(parameter.name)))) return 'destructive';

  const resourceIndex = segments.findLastIndex((segment) => safeResources.has(segment.toLowerCase()));
  if (resourceIndex < 0) return 'unknown';
  const parents = segments.slice(1, resourceIndex).map((segment) => segment.toLowerCase());
  if (parents.length > 1 || (parents.length === 1 && parents[0] !== 'agents')) return 'unknown';
  const actions = segments.slice(resourceIndex + 1).map((segment) => segment.toLowerCase());
  const actionParameter = operation.parameters.find(({ location, name }) => location === 'query' && name === 'action');
  if (operation.method === 'GET' || operation.method === 'HEAD') {
    if (!actionParameter && actions.every((action) => readActions.has(action)) && !names.split(' ').some((term) => writeActions.has(term))) return 'read';
    return 'unknown';
  }
  const namedWrite = terms(operation.operationId).split(' ').some((term) => writeActions.has(term));
  const hasApprovedAction = actionParameter?.required && strings(actionParameter.schema.enum).some((action) => writeActions.has(action.toLowerCase()));
  if (actions.every((action) => writeActions.has(action)) && (actions.length || namedWrite || hasApprovedAction)) return 'write';
  return 'unknown';
}

function safePath(path: string): boolean {
  return /^\/api\/(?:[a-zA-Z0-9._~!$&'()+,;=:@/-]|\{[a-zA-Z0-9_.-]+\})+$/.test(path)
    && !path.includes('//') && !path.split('/').some((segment) => segment === '.' || segment === '..' || (segment.match(/\{/g)?.length ?? 0) > 1);
}

interface Route {
  id: string;
  specificity: number[];
  segments: RegExp[];
}

function routeFor(operation: Operation): Route {
  const parts = operation.path.split('/');
  const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return {
    id: operation.id,
    specificity: parts.map((part) => part.includes('{') ? part.replace(/\{[^}]+\}/, '').length : Number.MAX_SAFE_INTEGER),
    segments: parts.map((part) => {
      const parameter = /\{[^}]+\}/.exec(part);
      if (!parameter) return new RegExp('^' + escape(part) + '$', 'u');
      return new RegExp('^' + escape(part.slice(0, parameter.index)) + '[^/]+' + escape(part.slice(parameter.index + parameter[0].length)) + '$', 'u');
    }),
  };
}

function canShadow(candidate: Route, selected: Route): boolean {
  for (const [index, rank] of candidate.specificity.entries()) {
    const otherRank = selected.specificity[index];
    if (otherRank !== undefined && rank !== otherRank) return rank > otherRank;
  }
  return true;
}

function jsonMedia(value: string): boolean {
  return /^application\/(?:json|[a-z0-9.-]+\+json)$/i.test(value);
}

function parseParameter(raw: unknown): Parameter | undefined {
  const parameter = object(raw);
  if (!parameter || typeof parameter.name !== 'string') throw new Error('Malformed OpenAPI parameter.');
  const { name } = parameter;
  if (parameter.in === 'header' && !safeHeaders.has(name.toLowerCase())) {
    if (parameter.required === true) throw new Error('Operation requires a header the gateway does not expose.');
    return undefined;
  }
  if (parameter.in !== 'query' && parameter.in !== 'path' && parameter.in !== 'header') {
    throw new Error('Parameter location is unsupported.');
  }
  if (!name || name.length > 120 || /[\u0000-\u0020\u007f]/.test(name)) throw new Error('Invalid parameter name.');
  const location = parameter.in === 'header' ? 'headers' : parameter.in;
  const style = parameter.style ?? (location === 'query' ? 'form' : 'simple');
  const explode = parameter.explode ?? style === 'form';
  if (typeof style !== 'string' || typeof explode !== 'boolean' || parameter.allowReserved === true ||
      (location === 'query' ? !['form', 'spaceDelimited', 'pipeDelimited', 'deepObject'].includes(style) : style !== 'simple')) {
    throw new Error('Parameter serialization style is unsupported.');
  }
  const schema = object(strictSchema(parameter.schema));
  if (!schema) throw new Error('Parameter schema is unsupported.');
  const types = typeof schema.type === 'string' ? [schema.type] : strings(schema.type);
  const [type] = types.filter((candidate) => candidate !== 'null');
  const serializedTypes = ['string', 'number', 'integer', 'boolean', 'array', 'object', 'null'];
  if (typeof type !== 'string' || !serializedTypes.includes(type) || types.filter((candidate) => candidate !== 'null').length !== 1 ||
      (types.includes('null') && (location !== 'query' || parameter.required === true)) ||
      (style === 'deepObject' && (type !== 'object' || !explode)) ||
      (['pipeDelimited', 'spaceDelimited'].includes(style) && (type !== 'array' || explode))) {
    throw new Error('Parameter type is unsupported for its serialization style.');
  }
  if (location !== 'query' && (type === 'object' || type === 'array')) throw new Error('Path and header parameters must be scalar.');
  if (location === 'query' && type === 'object' && style === 'form' && explode) {
    throw new Error('Exploded query objects may overlap the names of other parameters.');
  }
  return { location, name, required: location === 'path' || parameter.required === true, schema, style, explode, description: excerpt(parameter.description, 180) };
}

function operationSummary(operation: Operation) {
  const { id, title, summary, method, path, risk, supported, reason_if_unsupported, required_permissions } = operation;
  return { id, title, summary, method, path, risk, supported, reason_if_unsupported, required_permissions };
}

function requestSchema(operation: Operation, requestBody: unknown): RecordValue {
  const properties = Object.fromEntries(['path', 'query', 'headers'].map((location) => {
    const list = operation.parameters.filter((parameter) => parameter.location === location);
    return [location, {
      type: 'object',
      additionalProperties: false,
      properties: Object.fromEntries(list.map((parameter) => [parameter.name, parameter.schema])),
      required: list.filter((parameter) => parameter.required).map((parameter) => parameter.name),
    }];
  })) as RecordValue;
  const required = [...new Set(operation.parameters.filter((parameter) => parameter.required).map((parameter) => parameter.location))] as string[];
  if (requestBody !== undefined) {
    const request = object(requestBody);
    const content = object(request?.content);
    const media = object(content?.['application/json']);
    if (!request || !media || !('schema' in media) || Object.keys(content ?? {}).some((type) => !jsonMedia(type))) {
      throw new Error('Request body media type is unsupported.');
    }
    if (operation.method === 'GET' || operation.method === 'HEAD') throw new Error('Request body on a read method is unsupported.');
    properties.body = strictSchema(media.schema);
    if (request.required === true) required.push('body');
  }
  return { type: 'object', additionalProperties: false, properties, required };
}

function responseTypes(responses: RecordValue, method: string): string[] {
  const entries = Object.entries(responses).filter(([status]) => /^2(?:[0-9]{2}|XX)$/.test(status));
  if (!entries.length) throw new Error('No successful response is documented.');
  const types = new Set<string>();
  for (const [, response] of entries) {
    const entry = object(response);
    if (!entry) throw new Error('Malformed OpenAPI response.');
    const mediaTypes = Object.keys(object(entry.content) ?? {});
    if (mediaTypes.some((type) => !jsonMedia(type) && !/^text\/plain$/i.test(type))) {
      throw new Error('Successful response media type is unsupported.');
    }
    if (!mediaTypes.length || method === 'HEAD') types.add('empty');
    else for (const type of mediaTypes) types.add(type);
  }
  return [...types];
}

function validateData(input: unknown, depth = 0, budget = { remaining: 24_000 }): void {
  if (depth > 48 || --budget.remaining < 0) throw new CatalogError(400, 'invalid_arguments', 'Arguments exceed complexity limits.');
  if (typeof input === 'number' && !Number.isFinite(input)) throw new CatalogError(400, 'invalid_arguments', 'Arguments contain an invalid number.');
  if (Array.isArray(input)) for (const child of input) validateData(child, depth + 1, budget);
  const entry = object(input);
  if (!entry) return;
  for (const [key, child] of Object.entries(entry)) {
    if (['__proto__', 'constructor', 'prototype'].includes(key)) throw new CatalogError(400, 'invalid_arguments', 'Arguments contain a reserved property name.');
    validateData(child, depth + 1, budget);
  }
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  const entry = object(value);
  if (!entry) return value;
  return Object.fromEntries(Object.keys(entry).sort().map((key) => [key, canonical(entry[key])]));
}

function scalar(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') return String(value);
  throw new CatalogError(400, 'invalid_arguments', 'Parameter value cannot be serialized.');
}

function encode(value: string): string {
  try {
    return encodeURIComponent(value).replace(/[!'()*]/g, (character) => '%' + character.charCodeAt(0).toString(16).toUpperCase());
  } catch {
    throw new CatalogError(400, 'invalid_arguments', 'Parameter contains invalid Unicode.');
  }
}

function queryPairs(parameter: Parameter, value: unknown): [string, string][] {
  const name = encode(parameter.name);
  if (Array.isArray(value)) {
    const values = value.map((item) => encode(scalar(item)));
    if (parameter.explode) return values.map((item) => [name, item]);
    const separator = parameter.style === 'spaceDelimited' ? '%20' : parameter.style === 'pipeDelimited' ? '%7C' : ',';
    return [[name, values.join(separator)]];
  }
  const entry = object(value);
  if (entry) {
    const fields = Object.entries(entry).map(([key, item]) => [encode(key), encode(scalar(item))] as const);
    if (parameter.style === 'deepObject') return fields.map(([key, item]) => [name + '%5B' + key + '%5D', item]);
    return [[name, fields.flat().join(',')]];
  }
  return [[name, encode(scalar(value))]];
}

function bodyHasDestructiveValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(bodyHasDestructiveValue);
  const entry = object(value);
  if (!entry) return false;
  return Object.entries(entry).some(([key, child]) => {
    if (destructiveArgument.test(terms(key)) && child !== false && child !== null && child !== 0 && child !== '') return true;
    return bodyHasDestructiveValue(child);
  });
}

function discoverOperation(
  snapshot: string, method: string, path: string, rawPath: RecordValue, rawOperation: unknown, resolve: ReturnType<typeof resolver>,
): Operation {
  const raw = object(rawOperation);
  const operation: Operation = {
    id: snapshot + '.' + createHash('sha256').update(method + ' ' + path).digest('base64url').slice(0, 24),
    title: excerpt(raw?.summary, 120) || excerpt(raw?.operationId, 120) || method + ' ' + path,
    summary: excerpt(raw?.description, 320) || excerpt(raw?.summary, 320),
    method,
    path,
    risk: 'unknown',
    supported: false,
    tags: strings(raw?.tags).map((tag) => excerpt(tag, 100)).slice(0, 20),
    operationId: excerpt(raw?.operationId, 120),
    parameters: [],
    responseTypes: [],
  };

  try {
    if (!safePath(path)) throw new Error('Operation path is unsupported.');
    const resolved = object(resolve(rawOperation));
    if (!resolved) throw new Error('Malformed OpenAPI operation.');
    operation.title = excerpt(resolved.summary, 120) || excerpt(resolved.operationId, 120) || operation.title;
    operation.summary = excerpt(resolved.description, 320) || operation.summary;
    operation.tags = strings(resolved.tags).map((tag) => excerpt(tag, 100)).slice(0, 20);
    operation.operationId = excerpt(resolved.operationId, 120) || operation.operationId;
    operation.required_permissions = permissions(resolved);
    if ((resolved.parameters !== undefined && !Array.isArray(resolved.parameters)) || (rawPath.parameters !== undefined && !Array.isArray(rawPath.parameters))) {
      throw new Error('Malformed OpenAPI parameter list.');
    }
    const pathParameters = resolve(rawPath.parameters ?? []) as unknown[];
    const operationParameters = (resolved.parameters ?? []) as unknown[];
    const effective = new Map<string, unknown>();
    for (const group of [pathParameters, operationParameters]) {
      if (group.length > 100) throw new Error('Operation has too many parameters.');
      const keys = new Set<string>();
      for (const candidate of group) {
        const entry = object(candidate);
        const name = entry?.in === 'header' && typeof entry.name === 'string' ? entry.name.toLowerCase() : entry?.name;
        const key = String(entry?.in) + ':' + String(name);
        if (keys.has(key)) throw new Error('Operation declares duplicate parameters.');
        keys.add(key);
        effective.set(key, candidate);
      }
    }
    operation.parameters = [...effective.values()].map(parseParameter).filter((parameter): parameter is Parameter => Boolean(parameter));
    operation.risk = classify(operation);
    if (operation.risk === 'blocked') throw new Error('Operation is blocked by gateway policy.');

    const names = new Set([...path.matchAll(/\{([^}]+)\}/g)].map((match) => match[1]));
    const pathNames = operation.parameters.filter((parameter) => parameter.location === 'path').map((parameter) => parameter.name);
    if (names.size !== pathNames.length || pathNames.some((name) => !names.has(name))) throw new Error('Path placeholders differ from declared parameters.');
    operation.responseTypes = responseTypes(object(resolved.responses) ?? {}, method);
    operation.schema = requestSchema(operation, resolved.requestBody);
    if (Buffer.byteLength(JSON.stringify(operation.schema)) > 20_000) throw new Error('Operation argument schema exceeds limits.');
    try {
      operation.validate = ajv.compile(operation.schema);
    } catch {
      throw new Error('Operation argument schema could not be compiled.');
    }
    operation.supported = true;
  } catch (error) {
    operation.risk = classify(operation);
    operation.reason_if_unsupported = error instanceof Error ? error.message : 'Operation schema is unsupported.';
    operation.schema = undefined;
  }
  return operation;
}

export class Catalog {
  readonly #operations: Map<string, Operation>;
  readonly #routesByMethod = new Map<string, Route[]>();
  readonly #routes = new Map<string, Route>();

  constructor(operations: Operation[]) {
    this.#operations = new Map(operations.map((operation) => [operation.id, operation]));
    for (const operation of operations) {
      if (!safePath(operation.path)) continue;
      const route = routeFor(operation);
      if (!this.#routesByMethod.has(operation.method)) this.#routesByMethod.set(operation.method, []);
      this.#routesByMethod.get(operation.method)?.push(route);
      this.#routes.set(operation.id, route);
    }
  }

  search({ query, category, risk, limit = 8 }: SearchOptions) {
    if (typeof query !== 'string' || query.length > 240 || (category !== undefined && (typeof category !== 'string' || category.length > 100)) ||
        (risk !== undefined && !['read', 'write', 'destructive'].includes(risk)) || !Number.isInteger(limit) || limit < 1 || limit > 20) {
      throw new CatalogError(400, 'invalid_arguments', 'Invalid search options.');
    }
    const tokens = [...new Set(terms(query).split(' ').filter(Boolean))];
    const categoryTerm = category && terms(category);
    return [...this.#operations.values()].flatMap((operation) => {
      const effectiveRisk = operation.risk === 'unknown' ? 'destructive' : operation.risk;
      if (risk && risk !== effectiveRisk) return [];
      const actions = operation.parameters.flatMap((parameter) => parameter.name === 'action' ? strings(parameter.schema.enum) : []);
      const tags = terms(operation.tags.join(' '));
      const path = terms(operation.path);
      if (categoryTerm && !(' ' + tags + ' ' + path + ' ').includes(' ' + categoryTerm + ' ')) return [];
      const title = terms([operation.title, operation.operationId, ...actions].join(' '));
      const summary = terms(operation.summary);
      const score = tokens.reduce((total, token) => {
        const matches = (value: string) => value.split(' ').some((word) => word === token || word === token + 's' || word + 's' === token);
        return total + (matches(title) ? 8 : matches(tags) || matches(path) ? 4 : matches(summary) ? 1 : 0);
      }, 0);
      return score || tokens.length === 0 ? [{ operation, score }] : [];
    }).sort((left, right) => right.score - left.score || Number(right.operation.supported) - Number(left.operation.supported) ||
      left.operation.path.localeCompare(right.operation.path) || left.operation.method.localeCompare(right.operation.method))
      .slice(0, limit).map(({ operation }) => operationSummary(operation));
  }

  describe(id: string) {
    const operation = this.#get(id);
    return {
      ...operationSummary(operation),
      schema: operation.schema,
      parameters: operation.parameters.map(({ location, name, required, description }) => ({ location, name, required, description })),
      response_types: operation.responseTypes,
      approval: operation.supported ? {
        required: operation.risk === 'unknown' || operation.risk === 'destructive',
        conditional: operation.risk === 'write',
        channel: 'external',
      } : undefined,
    };
  }

  prepare(id: string, args: unknown): PreparedOperation {
    const operation = this.#get(id);
    if (operation.risk === 'blocked') throw new CatalogError(403, 'operation_blocked', 'Operation is blocked by gateway policy.');
    if (!operation.validate || !operation.supported) throw new CatalogError(400, 'unsupported_operation', operation.reason_if_unsupported ?? 'Operation is unsupported.');
    validateData(args);
    const argsJson = JSON.stringify(args);
    if (argsJson === undefined || Buffer.byteLength(argsJson) > 256_000) throw new CatalogError(400, 'invalid_arguments', 'Arguments exceed size limits.');
    if (!operation.validate(args)) {
      const issue = operation.validate.errors?.[0];
      const location = issue?.instancePath ? ' at ' + excerpt(issue.instancePath, 120) : '';
      throw new CatalogError(400, 'invalid_arguments', 'Arguments do not satisfy the operation schema' + location + '.');
    }

    const canonicalArgs = canonical(args) as RecordValue;
    const pathArgs = object(canonicalArgs.path) ?? {};
    const requestPath = operation.path.replace(/\{([^}]+)\}/g, (_placeholder, key: string) => {
      const value = scalar(pathArgs[key]);
      if (!value || value === '.' || value === '..' || /[\\/%\u0000-\u001f\u007f]/.test(value)) {
        throw new CatalogError(400, 'invalid_arguments', 'Invalid path segment.');
      }
      return encode(value);
    });
    if (requestPath.split('/').some((segment) => segment === '.' || segment === '..')) {
      throw new CatalogError(400, 'invalid_arguments', 'Invalid path segment.');
    }
    const selectedRoute = this.#routes.get(operation.id);
    if (selectedRoute && operation.path.includes('{')) {
      const segments = requestPath.split('/').map((part) => decodeURIComponent(part));
      const shadowed = this.#routesByMethod.get(operation.method)?.some((route) => route.id !== operation.id &&
        route.segments.length === segments.length && canShadow(route, selectedRoute) &&
        route.segments.every((pattern, index) => pattern.test(segments[index] ?? '')));
      if (shadowed) throw new CatalogError(400, 'invalid_arguments', 'Path parameters match another documented operation.');
    }
    const queryArgs = object(canonicalArgs.query) ?? {};
    const headerArgs = object(canonicalArgs.headers) ?? {};
    const headers: Record<string, string> = {};
    const query: [string, string][] = [];
    for (const parameter of operation.parameters) {
      if (parameter.location === 'query' && Object.hasOwn(queryArgs, parameter.name) && queryArgs[parameter.name] !== null) {
        query.push(...queryPairs(parameter, queryArgs[parameter.name]));
      }
      if (parameter.location === 'headers' && Object.hasOwn(headerArgs, parameter.name)) {
        const value = scalar(headerArgs[parameter.name]);
        if (/[^\x20-\x7e]/.test(value) || value.length > 4096) throw new CatalogError(400, 'invalid_arguments', 'Invalid header value.');
        headers[parameter.name.toLowerCase()] = value;
      }
    }
    const requestedPath = requestPath + (query.length ? '?' + query.map(([name, value]) => name + '=' + value).join('&') : '');
    if (requestedPath.length > 8192) throw new CatalogError(400, 'invalid_arguments', 'Encoded request path exceeds size limits.');
    const action = queryArgs.action;
    if (typeof action === 'string' && blocked.test(terms(action))) {
      throw new CatalogError(403, 'operation_blocked', 'Action is blocked by gateway policy.');
    }
    const risk = operation.risk === 'write' && ((typeof action === 'string' && !writeActions.has(action.toLowerCase())) ||
      bodyHasDestructiveValue(canonicalArgs.body)) ? 'destructive' : operation.risk;
    return {
      id,
      method: operation.method,
      path: requestedPath,
      headers,
      body: Object.hasOwn(canonicalArgs, 'body') ? JSON.stringify(canonicalArgs.body) : undefined,
      risk,
      title: operation.title,
      canonicalArgs,
      queryKeys: operation.parameters.filter((parameter) => parameter.location === 'query').map((parameter) => parameter.name),
    };
  }

  #get(id: string): Operation {
    const operation = this.#operations.get(id);
    if (!operation) throw new CatalogError(409, 'stale_operation', 'Operation ID is unknown or expired. Search the current catalog again.');
    return operation;
  }
}

export function compileCatalog(document: unknown, snapshotHash: string): Catalog {
  const root = object(document);
  const paths = object(root?.paths);
  if (!root || typeof root.openapi !== 'string' || !/^3\.1\.\d+(?:[-+][\w.-]+)?$/.test(root.openapi) || !paths || !object(root.info)) {
    throw new CatalogError(502, 'invalid_openapi', 'Arcane must publish a valid OpenAPI 3.1 document.');
  }
  rejectExternalRefs(root);
  const resolve = resolver(root);
  const operations: Operation[] = [];
  const seen = new Set<string>();
  for (const [rawPath, item] of Object.entries(paths)) {
    if (rawPath.startsWith('x-')) continue;
    if (!rawPath.startsWith('/') || rawPath.length > 800) throw new CatalogError(502, 'invalid_openapi', 'Invalid OpenAPI path.');
    // Arcane 3 exports paths relative to its /api base. The proxy uses its
    // configured origin, regardless of server URLs in the document.
    const path = rawPath === '/api' || rawPath.startsWith('/api/') ? rawPath : '/api' + rawPath;
    let pathItem = object(item);
    if (typeof pathItem?.$ref === 'string') {
      try {
        pathItem = object(resolve(item));
      } catch {
        throw new CatalogError(502, 'invalid_openapi', 'OpenAPI path reference could not be resolved.');
      }
    }
    if (!pathItem) throw new CatalogError(502, 'invalid_openapi', 'Invalid OpenAPI path item.');
    for (const [key, operation] of Object.entries(pathItem)) {
      if (!methods.has(key)) continue;
      const method = key.toUpperCase();
      const route = method + ' ' + path;
      if (seen.has(route) || operations.length >= 2500) throw new CatalogError(502, 'invalid_openapi', 'OpenAPI routes are ambiguous or exceed limits.');
      seen.add(route);
      operations.push(discoverOperation(snapshotHash, method, path, pathItem, operation, resolve));
    }
  }
  if (!operations.length) throw new CatalogError(502, 'invalid_openapi', 'OpenAPI contains no HTTP operations.');
  return new Catalog(operations);
}
