export function isUncertainWrite(error: { status?: number; code?: string }): boolean {
  return error.status === 0 || error.status === 401 || (typeof error.status === 'number' && error.status >= 500) || error.code === 'mutation_in_progress';
}

export function identityFromSignedInitData(signedData: string): string {
  const sources = [signedData];
  const outer = new URLSearchParams(signedData.replace(/^#/, ''));
  const nested = outer.get('WebAppData');
  if (nested) sources.push(nested);
  for (const source of sources) {
    try {
      const user = new URLSearchParams(source.replace(/^#/, '')).get('user');
      if (!user) continue;
      const parsed = JSON.parse(user) as { id?: unknown };
      if (typeof parsed.id === 'string' || typeof parsed.id === 'number') return `user:${parsed.id}`;
    } catch { /* fall back to a digest of the signed payload */ }
  }
  return `signed:${signedData}`;
}

export async function hashIdentityScope(signedData: string): Promise<string> {
  const identity = identityFromSignedInitData(signedData);
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(identity));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  const mask = (1n << 64n) - 1n;
  let hash = 14_695_981_039_346_656_037n;
  for (let i = 0; i < identity.length; i += 1) {
    hash = ((hash ^ BigInt(identity.charCodeAt(i))) * 1_099_511_628_211n) & mask;
  }
  return hash.toString(16);
}

export async function idempotencyStorageKey(signedData: string, path: string, body: unknown): Promise<string> {
  const scopeHash = await hashIdentityScope(signedData);
  const raw = `${scopeHash}\n${path}\n${JSON.stringify(body)}`;
  let fingerprint: string;
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
    fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  } else {
    const mask = (1n << 64n) - 1n;
    let hash = 14_695_981_039_346_656_037n;
    for (let i = 0; i < raw.length; i += 1) {
      hash = ((hash ^ BigInt(raw.charCodeAt(i))) * 1_099_511_628_211n) & mask;
    }
    fingerprint = hash.toString(16);
  }
  return `ctxads.pending.${scopeHash}.${fingerprint}`;
}

export type PendingWrite = { scopeHash: string; path: string; method: 'POST' | 'PUT'; body: Record<string, unknown> };

export function pendingOperationStorageKey(scopeHash: string): string {
  return `ctxads.pending-operation.${scopeHash}`;
}

export function parsePendingWrite(raw: string | null, expectedScopeHash: string): PendingWrite | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<PendingWrite> & { version?: unknown };
    if (value.version !== 1 || value.scopeHash !== expectedScopeHash) return null;
    if (value.method !== 'POST' && value.method !== 'PUT') return null;
    if (typeof value.path !== 'string' || !/^\/api\/miniapp\/[a-zA-Z0-9_/-]+$/u.test(value.path) || value.path.includes('..')) return null;
    if (!value.body || typeof value.body !== 'object' || Array.isArray(value.body)) return null;
    if (JSON.stringify(value.body).length > 64_000) return null;
    return { scopeHash: expectedScopeHash, path: value.path, method: value.method, body: value.body as Record<string, unknown> };
  } catch {
    return null;
  }
}

export class IdempotencyLedger {
  private memory = new Map<string, string>();
  private readonly storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

  constructor(storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
    this.storage = storage;
  }

  getOrCreate(key: string, create: () => string): string {
    try {
      const stored = this.storage?.getItem(key);
      if (stored) return stored;
    } catch { /* in-memory retry remains available */ }
    const existing = this.memory.get(key);
    if (existing) return existing;
    const value = create();
    this.memory.set(key, value);
    try { this.storage?.setItem(key, value); } catch { /* in-memory retry remains available */ }
    return value;
  }

  get(key: string): string | null {
    try {
      const stored = this.storage?.getItem(key);
      if (stored) return stored;
    } catch { /* in-memory retry remains available */ }
    return this.memory.get(key) ?? null;
  }

  clear(key: string): void {
    this.memory.delete(key);
    try { this.storage?.removeItem(key); } catch { /* ignore unavailable storage */ }
  }
}
