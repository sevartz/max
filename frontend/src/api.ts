import type { Ad, AdsData, Bootstrap, ChannelDetail, StatsData } from './types';

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

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const signedData = initData();
  if (!signedData) {
    throw new ApiError('Откройте приложение из MAX, чтобы войти.', 401);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Max-Init-Data': signedData,
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError('Не удалось связаться с сервисом.', 0);
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail;
    if (response.status === 401) {
      throw new ApiError('Данные входа устарели. Закройте приложение и откройте его снова.', 401);
    }
    throw new ApiError(
      detail?.message ?? 'Не получилось выполнить действие. Обновите данные и попробуйте снова.',
      response.status,
      detail?.field,
      detail?.code,
    );
  }
  return data as T;
}

function newRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
}

async function requestFingerprint(path: string, body: unknown): Promise<string> {
  const raw = `${path}\n${JSON.stringify(body)}`;
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (let i = 0; i < raw.length; i += 1) hash = Math.imul(hash ^ raw.charCodeAt(i), 16777619);
  return (hash >>> 0).toString(16);
}

const pendingRequestIds = new Map<string, string>();

function readPendingRequestId(key: string): string | null {
  try {
    const value = localStorage.getItem(key) ?? sessionStorage.getItem(key);
    if (value) return value;
  } catch {
    // MAX WebViews can disable persistent storage; an in-memory retry still works.
  }
  return pendingRequestIds.get(key) ?? null;
}

function savePendingRequestId(key: string, value: string): void {
  pendingRequestIds.set(key, value);
  try { localStorage.setItem(key, value); return; } catch { /* use session storage */ }
  try { sessionStorage.setItem(key, value); } catch { /* keep it in memory */ }
}

function clearPendingRequestId(key: string): void {
  pendingRequestIds.delete(key);
  try { localStorage.removeItem(key); } catch { /* ignore unavailable storage */ }
  try { sessionStorage.removeItem(key); } catch { /* ignore unavailable storage */ }
}

export async function mutate<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' = 'POST',
): Promise<T> {
  const fingerprint = await requestFingerprint(path, body);
  const storageKey = `ctxads.pending.${fingerprint}`;
  const priorKey = readPendingRequestId(storageKey);
  const requestId = priorKey ?? newRequestId();
  if (!priorKey) savePendingRequestId(storageKey, requestId);

  try {
    const result = await api<T>(path, {
      method,
      headers: { 'Idempotency-Key': requestId },
      body: JSON.stringify(body),
    });
    clearPendingRequestId(storageKey);
    return result;
  } catch (error) {
    if (error instanceof ApiError) {
      const uncertain = error.status === 0 || error.status >= 500 || error.code === 'mutation_in_progress';
      if (!uncertain) clearPendingRequestId(storageKey);
    }
    throw error;
  }
}

export const getBootstrap = () => api<Bootstrap>('/api/miniapp/bootstrap');
export const getChannel = (id: number) => api<ChannelDetail>(`/api/miniapp/channels/${id}`);
export const getAds = () => api<AdsData>('/api/miniapp/ads');
export const getStats = () => api<StatsData>('/api/miniapp/stats');

export type MutationResponse = ChannelDetail | Ad | { accepted: boolean; name?: string };
