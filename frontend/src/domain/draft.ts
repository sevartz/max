import type { DraftValues } from './validation';

export type WizardSnapshot = { step: number | 'preview'; values: DraftValues };
export const WIZARD_VERSION = 2;
export const EMPTY_DRAFT: DraftValues = {
  title: '', body: '', url: '', category: '', pricing_model: '', price: '', budget: '',
};

function draftIdentity(signedInitData: string): string {
  const sources = [signedInitData];
  const outer = new URLSearchParams(signedInitData.replace(/^#/, ''));
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
  return `signed:${signedInitData}`;
}

export function wizardStorageKey(signedInitData: string): string {
  const identity = draftIdentity(signedInitData);
  const mask = (1n << 64n) - 1n;
  let hash = 14_695_981_039_346_656_037n;
  for (let i = 0; i < identity.length; i += 1) {
    hash = ((hash ^ BigInt(identity.charCodeAt(i))) * 1_099_511_628_211n) & mask;
  }
  return `ctxads.miniapp.creation.v${WIZARD_VERSION}.${hash.toString(16)}`;
}

export function parseWizardSnapshot(raw: string | null): WizardSnapshot | null {
  if (!raw) return null;
  try {
    const envelope = JSON.parse(raw) as { version?: unknown; wizard?: unknown };
    if (envelope.version !== WIZARD_VERSION || !envelope.wizard || typeof envelope.wizard !== 'object') return null;
    const candidate = envelope.wizard as { step?: unknown; values?: unknown };
    const step = candidate.step;
    if (!(step === 'preview' || (typeof step === 'number' && Number.isInteger(step) && step >= 1 && step <= 7))) return null;
    if (!candidate.values || typeof candidate.values !== 'object') return null;
    const values = candidate.values as Record<string, unknown>;
    const keys = Object.keys(EMPTY_DRAFT) as Array<keyof DraftValues>;
    if (keys.some((key) => typeof values[key] !== 'string' || (values[key] as string).length > (key === 'body' ? 1000 : key === 'url' ? 2048 : key === 'title' ? 100 : 128))) return null;
    return { step, values: Object.fromEntries(keys.map((key) => [key, values[key]])) as DraftValues };
  } catch {
    return null;
  }
}
