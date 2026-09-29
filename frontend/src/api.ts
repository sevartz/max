import type { Ad, AdsData, Bootstrap, ChannelDetail, StatsData } from './types';
import {
  hashIdentityScope,
  IdempotencyLedger,
  idempotencyStorageKey,
  isUncertainWrite,
  parsePendingWrite,
  pendingOperationStorageKey,
} from './domain/idempotency';
import type { PendingWrite } from './domain/idempotency';

const REQUEST_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  status: number;
  field?: string;
  code?: string;

  constructor(message: string, status: number, field?: string, code?: string) {
    super(message);
    this.status = status;
    this.field = field;
    this.code = code;
  }
}

export function initData(): string {
  const bridgeData = window.WebApp?.initData;
  if (typeof bridgeData === 'string' && bridgeData.length > 0) return bridgeData;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  return fragment.get('WebAppData') ?? '';
}

async function api<T>(path: string, init: RequestInit = {}, signedDataOverride?: string): Promise<T> {
  const signedData = signedDataOverride ?? initData();
  if (!signedData) {
    throw new ApiError('Откройте приложение из MAX, чтобы войти.', 401);
  }

  const controller = new AbortController();
  const externalSignal = init.signal;
  const abortFromCaller = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Max-Init-Data': signedData,
        ...init.headers,
      },
    });
    const data = await response.json().catch((cause) => {
      if (controller.signal.aborted) throw cause;
      return {};
    });
    if (!response.ok) {
      const detail = data?.detail;
      if (response.status === 401) {
        throw new ApiError('Данные входа устарели. Проверьте подключение MAX и повторите запрос.', 401);
      }
      throw new ApiError(
        detail?.message ?? 'Не получилось выполнить действие. Обновите данные и попробуйте снова.',
        response.status,
        detail?.field,
        detail?.code,
      );
    }
    return data as T;
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    throw new ApiError(
      controller.signal.aborted ? 'Запрос не завершился вовремя. Проверьте подключение и попробуйте ещё раз.' : 'Не удалось связаться с сервисом.',
      0,
    );
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener('abort', abortFromCaller);
  }
}

function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
}

const pendingLedger = new IdempotencyLedger({
  getItem(key) {
    try { return sessionStorage.getItem(key); } catch { return null; }
  },
  setItem(key, value) {
    try { sessionStorage.setItem(key, value); } catch { /* memory still retains the key */ }
  },
  removeItem(key) {
    try { sessionStorage.removeItem(key); } catch { /* ignore */ }
  },
});

const pendingOperations = new Map<string, PendingWrite>();

function savePendingWrite(write: PendingWrite): void {
  pendingOperations.set(write.scopeHash, write);
  try { sessionStorage.setItem(pendingOperationStorageKey(write.scopeHash), JSON.stringify({ version: 1, ...write })); } catch { /* the in-memory recovery remains available */ }
}

function clearPendingWrite(scopeHash: string): void {
  pendingOperations.delete(scopeHash);
  try { sessionStorage.removeItem(pendingOperationStorageKey(scopeHash)); } catch { /* ignore unavailable storage */ }
}

export async function getPendingWrite(): Promise<PendingWrite | null> {
  const signedData = initData();
  if (!signedData) return null;
  const scopeHash = await hashIdentityScope(signedData);
  const memory = pendingOperations.get(scopeHash);
  if (memory) return memory;
  let raw: string | null = null;
  try { raw = sessionStorage.getItem(pendingOperationStorageKey(scopeHash)); } catch { return null; }
  const write = parsePendingWrite(raw, scopeHash);
  if (!write) return null;
  const storageKey = await idempotencyStorageKey(signedData, write.path, write.body);
  if (!pendingLedger.get(storageKey)) {
    clearPendingWrite(scopeHash);
    return null;
  }
  pendingOperations.set(scopeHash, write);
  return write;
}

export async function mutate<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' = 'POST',
): Promise<T> {
  const signedData = initData();
  if (!signedData) throw new ApiError('Откройте приложение из MAX, чтобы войти.', 401);
  const scopeHash = await hashIdentityScope(signedData);
  const storageKey = await idempotencyStorageKey(signedData, path, body);
  const requestId = pendingLedger.getOrCreate(storageKey, newRequestId);
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    pendingLedger.clear(storageKey);
    throw new ApiError('Не удалось безопасно сохранить действие. Обновите экран и повторите попытку.', 400);
  }
  savePendingWrite({ scopeHash, path, method, body: body as Record<string, unknown> });

  try {
    const result = await api<T>(path, {
      method,
      headers: { 'Idempotency-Key': requestId },
      body: JSON.stringify(body),
    }, signedData);
    pendingLedger.clear(storageKey);
    clearPendingWrite(scopeHash);
    return result;
  } catch (error) {
    if (error instanceof ApiError) {
      if (!isUncertainWrite(error)) {
        pendingLedger.clear(storageKey);
        clearPendingWrite(scopeHash);
      }
    }
    throw error;
  }
}

export const getBootstrap = (signal?: AbortSignal) => api<Bootstrap>('/api/miniapp/bootstrap', { signal });
export const getChannel = (id: number, signal?: AbortSignal) => api<ChannelDetail>(`/api/miniapp/channels/${id}`, { signal });
export const getAds = (signal?: AbortSignal) => api<AdsData>('/api/miniapp/ads', { signal });
export const getStats = (signal?: AbortSignal) => api<StatsData>('/api/miniapp/stats', { signal });

export type MutationResponse = ChannelDetail | Ad | { accepted: boolean; name?: string };
