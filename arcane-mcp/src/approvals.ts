import { createHash, randomUUID, timingSafeEqual } from "node:crypto";

export class GatewayError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

type ApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "consumed";

interface ApprovalSummary {
  method: string;
  path: string;
  title: string;
  origin: string;
  arguments: Record<string, unknown>;
  argumentsTruncated: boolean;
}

interface Approval {
  id: string;
  status: ApprovalStatus;
  expiresAt: number;
  summary: ApprovalSummary;
  binding: Buffer;
}

export type Audit = (event: string, details: Record<string, string | number | boolean>) => void;

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return "{" + Object.keys(object).sort().map(
      (key) => JSON.stringify(key) + ":" + canonicalJson(object[key]),
    ).join(",") + "}";
  }
  return JSON.stringify(value) ?? "null";
}

export function approvalBinding(input: {
  principal: string;
  origin: string;
  credential: string;
  snapshot: string;
  operation: string;
  method: string;
  path: string;
  args: unknown;
}): Buffer {
  return createHash("sha256").update(canonicalJson(input)).digest();
}

function view({ id, status, expiresAt, summary }: Approval) {
  return { id, status, expiresAt: new Date(expiresAt).toISOString(), ...summary };
}

export class Approvals {
  readonly #entries = new Map<string, Approval>();

  constructor(
    private readonly audit: Audit,
    private readonly ttlMs: number,
    private readonly maxEntries = 256,
  ) {}

  sweep(now = Date.now()) {
    for (const [id, entry] of this.#entries) {
      if (entry.expiresAt <= now && (entry.status === "pending" || entry.status === "approved")) {
        entry.status = "expired";
        this.audit("approval_expired", { approvalId: id });
      }
      if (entry.expiresAt + this.ttlMs <= now) this.#entries.delete(id);
    }
  }

  create(binding: Buffer, summary: ApprovalSummary) {
    this.sweep();
    if (this.#entries.size >= this.maxEntries) {
      throw new GatewayError(429, "approval_capacity", "The approval store is full. Try again after requests expire.");
    }
    const entry: Approval = {
      id: randomUUID(),
      status: "pending",
      expiresAt: Date.now() + this.ttlMs,
      summary,
      binding,
    };
    this.#entries.set(entry.id, entry);
    this.audit("approval_requested", { approvalId: entry.id });
    return view(entry);
  }

  list() {
    this.sweep();
    return [...this.#entries.values()].map(view);
  }

  decide(id: string, decision: "approve" | "reject") {
    this.sweep();
    const entry = this.#entries.get(id);
    if (!entry) throw new GatewayError(404, "approval_not_found", "Unknown approval request.");
    if (entry.status !== "pending") {
      throw new GatewayError(409, "approval_unavailable", "The approval request is no longer pending.");
    }
    entry.status = decision === "approve" ? "approved" : "rejected";
    this.audit("approval_" + entry.status, { approvalId: id });
    return view(entry);
  }

  consume(id: string, binding: Buffer) {
    this.sweep();
    const entry = this.#entries.get(id);
    if (!entry || !timingSafeEqual(entry.binding, binding)) {
      throw new GatewayError(403, "approval_invalid", "The approval does not match this request.");
    }
    if (entry.status !== "approved") {
      throw new GatewayError(403, "approval_" + entry.status, "The approval is unavailable for execution.");
    }
    entry.status = "consumed";
    this.audit("approval_consumed", { approvalId: id });
  }
}
