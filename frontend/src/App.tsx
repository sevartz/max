import { Button, MaxUI } from '@maxhub/max-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, getAds, getBootstrap, getChannel, getPendingWrite, getStats, initData, mutate } from './api';
import type {
  Ad,
  AdsData,
  Bootstrap,
  ChannelDetail,
  ChannelStats,
  MiniPage,
  PricingModel,
  StatsData,
  Tab,
} from './types';
import { EMPTY_DRAFT, WIZARD_VERSION, parseWizardSnapshot, wizardStorageKey } from './domain/draft';
import type { WizardSnapshot } from './domain/draft';
import { proposalActionMessage, proposalIsActionable, proposalStateText } from './domain/proposals';
import { RequestCoordinator } from './domain/request-coordinator';
import { isUncertainWrite } from './domain/idempotency';
import { formatMoney, normalizedMoney, validateAdEditField, validateDraftField } from './domain/validation';
import './styles.css';

type Draft = typeof EMPTY_DRAFT;
type WizardState = WizardSnapshot;
type Recovery = { message: string; retry: () => void };
type MutationMethod = 'POST' | 'PUT';
type BusyOperation =
  | { kind: 'consent' }
  | { kind: 'advertiser' }
  | { kind: 'proposal'; entityId: number; action: 'approve' | 'reject' | 'next' | 'block_cat' }
  | { kind: 'category'; entityId: number; category: string }
  | { kind: 'channel-pause'; entityId: number; action: 'pause' | 'resume' }
  | { kind: 'ad-pause'; entityId: number; action: 'pause' | 'resume' }
  | { kind: 'ad-create' }
  | { kind: 'ad-edit'; entityId: number; field: string }
  | { kind: 'ad-topup'; entityId: number }
  | { kind: 'recovery'; action: 'retry' };
type ResourceState = { status: 'idle' | 'loading' | 'ready' | 'error' | 'missing'; refreshing: boolean; error: string };
const initialResource: ResourceState = { status: 'idle', refreshing: false, error: '' };
const FIELD_TO_STEP: Record<string, number> = {
  title: 1,
  body: 2,
  url: 3,
  category: 4,
  pricing_model: 5,
  price: 6,
  budget: 7,
};

function readWizard(): WizardState {
  try {
    const saved = parseWizardSnapshot(sessionStorage.getItem(wizardStorageKey(initData())));
    if (saved) return saved;
  } catch {
    // The creation flow still works if web storage is unavailable.
  }
  return { step: 1, values: { ...EMPTY_DRAFT } };
}

const money = formatMoney;

function localTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return `${new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(date)} МСК`;
}

function count(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(value ?? 0);
}

function hasAdActivity(ad: Pick<Ad, 'placements' | 'views' | 'clicks' | 'spent'>): boolean {
  return ad.placements > 0 || ad.views > 0 || ad.clicks > 0 || Number(ad.spent) > 0;
}

function hasChannelActivity(stats: ChannelStats): boolean {
  return stats.placements > 0 || stats.views > 0 || stats.clicks > 0 || Number(stats.earned) > 0;
}

function channelStatus(status: string): { label: string; tone: string } {
  const map: Record<string, { label: string; tone: string }> = {
    active: { label: 'Работает', tone: 'good' },
    paused: { label: 'На паузе', tone: 'muted' },
    pending_consent: { label: 'Ждёт согласия', tone: 'warning' },
    insufficient_rights: { label: 'Нужны права', tone: 'warning' },
    removed: { label: 'Отключён', tone: 'muted' },
  };
  return map[status] ?? { label: 'Статус уточняется', tone: 'muted' };
}

function adStatus(status: string): { label: string; tone: string } {
  const map: Record<string, { label: string; tone: string }> = {
    active: { label: 'В подборе', tone: 'good' },
    paused: { label: 'На паузе', tone: 'muted' },
    exhausted: { label: 'Бюджет исчерпан', tone: 'warning' },
  };
  return map[status] ?? { label: 'Статус уточняется', tone: 'muted' };
}

function proposalStatus(status: string): { label: string; tone: string } {
  const map: Record<string, { label: string; tone: string }> = {
    pending: { label: 'Нужно решение', tone: 'accent' },
    approved: { label: 'Одобрено · ждёт публикации', tone: 'warning' },
    published: { label: 'Опубликовано', tone: 'good' },
    rejected: { label: 'Отклонено', tone: 'muted' },
    expired: { label: 'Срок предложения истёк', tone: 'muted' },
    cancelled: { label: 'Предложение отменено', tone: 'muted' },
    superseded: { label: 'Предложение заменено', tone: 'muted' },
  };
  return map[status] ?? { label: 'Статус уточняется', tone: 'muted' };
}

function permissionSummary(permissions: string[]): string {
  const labels: Record<string, string> = {
    read_all_messages: 'чтение всех сообщений',
    write: 'публикация сообщений',
    delete: 'удаление сообщений',
    delete_message: 'удаление сообщений',
    post_edit_delete_message: 'удаление сообщений',
  };
  const translated = [...new Set(permissions.map((permission) => labels[permission]).filter(Boolean))];
  return translated.length ? translated.join(', ') : 'права пока не получены';
}

function Icon({ name }: { name: 'channels' | 'ads' | 'stats' | 'back' | 'chevron' }) {
  const paths: Record<typeof name, React.ReactNode> = {
    channels: <><rect x="3.5" y="5" width="17" height="14" rx="3" /><path d="M7.5 9h9M7.5 13h5" /></>,
    ads: <><path d="M4 6.5h16M4 12h16M4 17.5h10" /><circle cx="18" cy="17.5" r="2.5" /></>,
    stats: <><path d="M4 19V5M4 19h16" /><path d="m7 15 3-4 3 2 5-6" /></>,
    back: <><path d="m14.5 5-7 7 7 7" /><path d="M8 12h11" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
  };
  return (
    <svg aria-hidden="true" className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  maxLength,
  placeholder,
  multiline = false,
  inputMode,
  type = 'text',
  autoComplete,
  disabled,
  autoFocus = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  maxLength?: number;
  placeholder?: string;
  multiline?: boolean;
  inputMode?: 'text' | 'url' | 'decimal';
  type?: string;
  autoComplete?: string;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const id = `field-${label.toLowerCase().replace(/[^a-zа-я0-9]+/gi, '-')}`;
  const common = {
    id,
    value,
    maxLength,
    placeholder,
    disabled,
    autoFocus,
    'data-auto-focus': autoFocus ? 'true' : undefined,
    'aria-invalid': Boolean(error),
    'aria-describedby': error ? `${id}-error` : undefined,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(event.target.value),
  };
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      {multiline ? (
        <textarea {...common} rows={5} />
      ) : (
        <input {...common} type={type} inputMode={inputMode} autoComplete={autoComplete} />
      )}
      <div className="field-foot">
        {error ? <span className="field-error" id={`${id}-error`} role="alert">{error}</span> : <span />}
        {maxLength ? <span className="field-count">{value.length}/{maxLength}</span> : null}
      </div>
    </div>
  );
}

function Status({ label, tone }: { label: string; tone: string }) {
  return <span className={`status status-${tone}`}><span className="status-dot" aria-hidden="true" />{label}</span>;
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="stat-pair"><span>{label}</span><strong>{value}</strong></div>;
}

function Skeleton() {
  return (
    <div className="skeleton-stack" aria-label="Загружаем данные" aria-busy="true">
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-line" />
      <div className="skeleton skeleton-row" />
      <div className="skeleton skeleton-row" />
    </div>
  );
}

function apiMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function proposalBusyName(action: 'approve' | 'reject' | 'next' | 'block_cat'): string {
  switch (action) {
    case 'approve': return 'Одобряем';
    case 'reject': return 'Отклоняем';
    case 'next': return 'Ищем вариант';
    case 'block_cat': return 'Запрещаем категорию';
  }
}

function busyOperationCopy(operation: BusyOperation | null): string {
  if (!operation) return '';
  switch (operation.kind) {
    case 'consent': return 'Принимаем';
    case 'advertiser': return 'Создаём кабинет';
    case 'proposal': return proposalBusyName(operation.action);
    case 'category': return `Сохраняем категорию:${operation.category}`;
    case 'channel-pause': return operation.action === 'pause' ? 'Приостанавливаем канал' : 'Возобновляем канал';
    case 'ad-pause': return operation.action === 'pause' ? 'Приостанавливаем объявление' : 'Возобновляем объявление';
    case 'ad-create': return 'Запускаем';
    case 'ad-edit': return 'Сохраняем';
    case 'ad-topup': return 'Пополняем';
    case 'recovery': return 'Проверяем результат';
  }
}

function AppContent() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [adsData, setAdsData] = useState<AdsData | null>(null);
  const [statsData, setStatsData] = useState<StatsData | null>(null);
  const [channelDetails, setChannelDetails] = useState<Record<number, ChannelDetail>>({});
  const [bootstrapState, setBootstrapState] = useState<ResourceState>({ status: 'loading', refreshing: false, error: '' });
  const [adsState, setAdsState] = useState<ResourceState>({ ...initialResource, status: 'loading' });
  const [statsState, setStatsState] = useState<ResourceState>({ ...initialResource, status: 'loading' });
  const [channelStates, setChannelStates] = useState<Record<number, ResourceState>>({});
  const [page, setPage] = useState<MiniPage>({ type: 'tabs' });
  const [tab, setTab] = useState<Tab>('channels');
  const [wizard, setWizard] = useState<WizardState>(readWizard);
  const wizardKey = useRef(wizardStorageKey(initData())).current;
  const hasPendingWizard = Object.values(wizard.values).some((value) => value.trim().length > 0);
  const [brandName, setBrandName] = useState('');
  const [editValue, setEditValue] = useState('');
  const [topupValue, setTopupValue] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [busyOperation, setBusyOperation] = useState<BusyOperation | null>(null);
  const [error, setError] = useState('');
  const [authExpired, setAuthExpired] = useState(false);
  const [authError, setAuthError] = useState('');
  const [notice, setNotice] = useState('');
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [nextExhaustedId, setNextExhaustedId] = useState<number | null>(null);
  const [authChecking, setAuthChecking] = useState(false);
  const [, setExpiryPulse] = useState(0);
  // Serialize writes so retries cannot overlap; reads and page navigation stay available.
  const busyRef = useRef(false);
  const recoveryRef = useRef<Recovery | null>(null);
  const scrollRef = useRef<Record<string, number>>({});
  const lastPageKey = useRef('tabs');
  const currentPageKey = useRef('tabs');
  const requests = useRef(new RequestCoordinator());

  const pageKey = useMemo(() => {
    if (page.type === 'tabs') return `tabs:${tab}`;
    if (page.type === 'channel' || page.type === 'ad') return `${page.type}:${page.id}`;
    if (page.type === 'edit' || page.type === 'topup') return `${page.type}:${page.id}`;
    return page.type;
  }, [page, tab]);
  currentPageKey.current = pageKey;

  const loadAds = useCallback(async (quiet = false) => {
    setAdsState((state) => ({ status: state.status === 'ready' ? 'ready' : 'loading', refreshing: quiet || state.status === 'ready', error: '' }));
    try {
      await requests.current.run('ads', getAds, {
        onSuccess: (data) => { setAdsData(data); setAdsState({ status: 'ready', refreshing: false, error: '' }); },
        onError: (cause) => {
          const message = apiMessage(cause, 'Не удалось загрузить объявления.');
          setAdsState((state) => ({ status: state.status === 'ready' ? 'ready' : 'error', refreshing: false, error: message }));
          if (cause instanceof ApiError && cause.status === 401) setAuthExpired(true);
        },
        onSettled: () => setAdsState((state) => ({ ...state, refreshing: false })),
      });
    } catch { /* state carries the resource-scoped error */ }
  }, []);

  const loadStats = useCallback(async (quiet = false) => {
    setStatsState((state) => ({ status: state.status === 'ready' ? 'ready' : 'loading', refreshing: quiet || state.status === 'ready', error: '' }));
    try {
      await requests.current.run('stats', getStats, {
        onSuccess: (data) => { setStatsData(data); setStatsState({ status: 'ready', refreshing: false, error: '' }); },
        onError: (cause) => {
          const message = apiMessage(cause, 'Не удалось загрузить статистику.');
          setStatsState((state) => ({ status: state.status === 'ready' ? 'ready' : 'error', refreshing: false, error: message }));
          if (cause instanceof ApiError && cause.status === 401) setAuthExpired(true);
        },
        onSettled: () => setStatsState((state) => ({ ...state, refreshing: false })),
      });
    } catch { /* state carries the resource-scoped error */ }
  }, []);

  const refresh = useCallback(async (quiet = false) => {
    setBootstrapState((state) => ({ status: state.status === 'ready' ? 'ready' : 'loading', refreshing: quiet || state.status === 'ready', error: '' }));
    try {
      const data = await requests.current.run('bootstrap', getBootstrap, {
        onSuccess: (value) => {
          setBootstrap(value);
          setBootstrapState({ status: 'ready', refreshing: false, error: '' });
          setError('');
          setAuthExpired(false);
          setAuthError('');
          if (!value.consented) {
            setAdsData(null);
            setStatsData(null);
            setAdsState({ status: 'idle', refreshing: false, error: '' });
            setStatsState({ status: 'idle', refreshing: false, error: '' });
          }
        },
        onError: (cause) => {
          const message = apiMessage(cause, 'Не удалось загрузить данные.');
          setBootstrapState((state) => ({ status: state.status === 'ready' ? 'ready' : 'error', refreshing: false, error: message }));
          if (cause instanceof ApiError && cause.status === 401) setAuthExpired(true);
        },
        onSettled: () => setBootstrapState((state) => ({ ...state, refreshing: false })),
      });
      if (data.consented) await Promise.all([loadAds(quiet), loadStats(quiet)]);
    } catch { /* bootstrap state carries the error */ }
  }, [loadAds, loadStats]);

  useEffect(() => { void refresh(false); }, [refresh]);

  useEffect(() => { void restorePendingRecovery(); }, []);

  useEffect(() => {
    const current = window.WebApp;
    const handleBack = () => goBack();
    if (page.type === 'tabs') current?.BackButton?.hide();
    else {
      current?.BackButton?.show();
      current?.BackButton?.onClick(handleBack);
    }
    return () => current?.BackButton?.offClick(handleBack);
  });

  useEffect(() => {
    const viewport = window.visualViewport;
    const updateHeight = () => {
      const height = viewport?.height ?? window.innerHeight;
      document.documentElement.style.setProperty('--viewport-height', `${height}px`);
    };
    updateHeight();
    viewport?.addEventListener('resize', updateHeight);
    window.addEventListener('resize', updateHeight);
    void Promise.resolve(window.WebApp?.getViewportSize?.()).then((size) => {
      if (!size) return;
      const height = Number(size.height);
      if (Number.isFinite(height) && height > 0) {
        document.documentElement.style.setProperty('--viewport-height', `${height}px`);
      }
    }).catch(() => undefined);
    return () => {
      viewport?.removeEventListener('resize', updateHeight);
      window.removeEventListener('resize', updateHeight);
    };
  }, []);

  useEffect(() => {
    const onResume = () => {
      if (document.visibilityState !== 'visible') return;
      void refresh(true);
      if (page.type === 'channel') void loadChannel(page.id, true);
    };
    document.addEventListener('visibilitychange', onResume);
    window.addEventListener('focus', onResume);
    return () => {
      document.removeEventListener('visibilitychange', onResume);
      window.removeEventListener('focus', onResume);
    };
  }, [page, refresh]);

  useEffect(() => {
    const previous = lastPageKey.current;
    scrollRef.current[previous] = window.scrollY;
    const target = scrollRef.current[pageKey] ?? 0;
    requestAnimationFrame(() => window.scrollTo({ top: target, behavior: 'instant' }));
    lastPageKey.current = pageKey;
  }, [pageKey]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>('[data-auto-focus="true"]')
        ?? document.querySelector<HTMLElement>('[data-route-heading]');
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [pageKey, wizard.step]);

  useEffect(() => {
    if (!Object.values(fieldErrors).some(Boolean)) return undefined;
    const frame = requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>('[aria-invalid="true"] [role="radio"]')
        ?? document.querySelector<HTMLElement>('[aria-invalid="true"]');
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [fieldErrors, pageKey]);

  useEffect(() => {
    if (page.type === 'create' && hasPendingWizard) window.WebApp?.enableClosingConfirmation?.();
    else window.WebApp?.disableClosingConfirmation?.();
    return () => window.WebApp?.disableClosingConfirmation?.();
  }, [page.type, hasPendingWizard]);

  useEffect(() => {
    if (page.type !== 'channel') return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadChannel(page.id, true);
    }, 20_000);
    return () => {
      window.clearInterval(timer);
      requests.current.invalidate(`channel:${page.id}`);
    };
  }, [page]);

  useEffect(() => {
    if (page.type !== 'channel') return undefined;
    const proposal = channelDetails[page.id]?.proposal;
    const expiry = proposal ? Date.parse(proposal.expires_at) : Number.NaN;
    if (!Number.isFinite(expiry) || expiry <= Date.now()) return undefined;
    const timer = window.setTimeout(() => setExpiryPulse((pulse) => pulse + 1), expiry - Date.now() + 10);
    return () => window.clearTimeout(timer);
  }, [page, channelDetails]);

  useEffect(() => {
    try {
      sessionStorage.setItem(wizardKey, JSON.stringify({ version: WIZARD_VERSION, wizard }));
    } catch { /* optional */ }
  }, [wizard, wizardKey]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(''), 2800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function loadChannel(id: number, quiet = false) {
    setChannelStates((current) => ({
      ...current,
      [id]: { status: channelDetails[id] ? 'ready' : 'loading', refreshing: quiet || Boolean(channelDetails[id]), error: '' },
    }));
    try {
      await requests.current.run(`channel:${id}`, (signal) => getChannel(id, signal), {
        onSuccess: (detail) => {
          setChannelDetails((current) => ({ ...current, [id]: detail }));
          updateChannelSummary(detail);
          setChannelStates((current) => ({ ...current, [id]: { status: 'ready', refreshing: false, error: '' } }));
        },
        onError: (cause) => {
          const message = apiMessage(cause, 'Не удалось обновить канал.');
          const missing = cause instanceof ApiError && cause.status === 404;
          if (missing) {
            setChannelDetails((current) => {
              const next = { ...current };
              delete next[id];
              return next;
            });
            setBootstrap((current) => current ? {
              ...current,
              channels: current.channels.filter((item) => item.chat_id !== id),
            } : current);
          }
          setChannelStates((current) => ({
            ...current,
            [id]: { status: missing ? 'missing' : channelDetails[id] ? 'ready' : 'error', refreshing: false, error: message },
          }));
          if (cause instanceof ApiError && cause.status === 401) setAuthExpired(true);
        },
        onSettled: () => setChannelStates((current) => current[id]
          ? { ...current, [id]: { ...current[id], refreshing: false } }
          : current),
      });
    } catch { /* state carries the channel-scoped error */ }
  }

  async function recoverMaxBridge() {
    const bridge = window as Window & { ensureMaxBridge?: () => Promise<boolean> };
    const previousInitData = initData();
    setAuthChecking(true);
    setAuthError('');
    try {
      const restored = await bridge.ensureMaxBridge?.();
      if (!restored) {
        setAuthError('MAX пока не подтвердил подключение. Проверьте сеть и попробуйте ещё раз.');
        return;
      }
      const currentInitData = initData();
      if (!currentInitData) {
        setAuthError('Подключение MAX восстановлено, но подписанные данные входа не получены. Закройте и заново откройте мини-приложение в MAX.');
        return;
      }
      if (authExpired && currentInitData === previousInitData) {
        setAuthError('Данные входа истекли. Подключение MAX восстановлено, но оно не обновляет подписанные данные. Закройте и заново откройте мини-приложение в MAX.');
        return;
      }
      setAuthExpired(false);
      await refresh(false);
      await restorePendingRecovery();
    } catch {
      setAuthError('Не удалось восстановить подключение к MAX. Попробуйте ещё раз.');
    } finally {
      setAuthChecking(false);
    }
  }

  function openPage(next: MiniPage) {
    scrollRef.current[pageKey] = window.scrollY;
    setError('');
    setFieldErrors({});
    setPage(next);
    if (next.type === 'channel') void loadChannel(next.id);
    if (next.type === 'ad') void loadAds(true);
  }

  function goBack() {
    if (page.type === 'tabs') return;
    if (page.type === 'create') {
      if (wizard.step === 'preview') {
        setWizard((current) => ({ ...current, step: 7 }));
        return;
      }
      if (typeof wizard.step === 'number' && wizard.step > 1) {
        setWizard((current) => ({ ...current, step: (current.step as number) - 1 }));
        return;
      }
      setTab('ads');
      setPage({ type: 'tabs' });
      return;
    }
    if (page.type === 'channel') {
      setTab('channels');
      setPage({ type: 'tabs' });
    } else if (page.type === 'ad') {
      setTab('ads');
      setPage({ type: 'tabs' });
    } else if (page.type === 'edit') {
      const ad = adsData?.ads.find((item) => item.id === page.id);
      if (ad) {
        const currentValue = page.field === 'price' ? normalizedMoney(editValue) : editValue.trim();
        const savedValue = page.field === 'price' ? normalizedMoney(ad.price) : String(ad[page.field]).trim();
        if (currentValue !== savedValue && !window.confirm('Отменить несохранённые изменения?')) return;
      }
      setPage({ type: 'ad', id: page.id });
    } else if (page.type === 'topup') {
      setPage({ type: 'ad', id: page.id });
    } else {
      setTab('ads');
      setPage({ type: 'tabs' });
    }
  }

  function changeTab(next: Tab) {
    scrollRef.current[pageKey] = window.scrollY;
    setTab(next);
    setPage({ type: 'tabs' });
    setError('');
    if (next === 'ads') void loadAds(true);
    if (next === 'stats') void loadStats(true);
  }

  async function runMutation<T>(
    path: string,
    body: unknown,
    onSuccess: (data: T, stillOnOrigin: () => boolean) => void,
    successText: string,
    method: MutationMethod = 'POST',
    retry = false,
    operation: BusyOperation = { kind: 'recovery', action: 'retry' },
    invalidateKeys: string[] = [],
  ) {
    if (busyRef.current || (recoveryRef.current && !retry)) return;
    busyRef.current = true;
    const originPage = currentPageKey.current;
    const stillOnOrigin = () => currentPageKey.current === originPage;
    setBusy(true);
    setBusyOperation(operation);
    setError('');
    setFieldErrors({});
    let committed = false;
    try {
      const data = await mutate<T>(path, body, method);
      committed = true;
      recoveryRef.current = null;
      setRecovery(null);
      invalidateKeys.forEach((key) => {
        requests.current.invalidate(key);
        if (key === 'bootstrap') setBootstrapState((state) => ({ ...state, status: bootstrap ? 'ready' : state.status, refreshing: false }));
        if (key === 'ads') setAdsState((state) => ({ ...state, status: adsData ? 'ready' : state.status, refreshing: false }));
        if (key === 'stats') setStatsState((state) => ({ ...state, status: statsData ? 'ready' : state.status, refreshing: false }));
      });
      await onSuccess(data, stillOnOrigin);
      if (successText && stillOnOrigin()) setNotice(successText);
    } catch (cause) {
      if (committed) {
        setError('Действие выполнено, но не удалось обновить экран. Обновите данные.');
        return;
      }
      const apiError = cause instanceof ApiError ? cause : new ApiError('Не удалось выполнить действие.', 0);
      if (apiError.field) {
        setFieldErrors({ [apiError.field]: apiError.message });
        if (page.type === 'create' && FIELD_TO_STEP[apiError.field]) {
          setWizard((current) => ({ ...current, step: FIELD_TO_STEP[apiError.field!] }));
        }
      }
      if (apiError.status === 401) setAuthExpired(true);
      if (isUncertainWrite(apiError)) {
        const retryAction = () => void runMutation<T>(path, body, onSuccess, successText, method, true, operation, invalidateKeys);
        const recover: Recovery = { message: 'Результат пока не подтверждён. Проверьте это же действие ещё раз.', retry: retryAction };
        recoveryRef.current = recover;
        setRecovery(recover);
      } else {
        if (retry) {
          recoveryRef.current = null;
          setRecovery(null);
        }
        if (!apiError.field) setError(apiError.message);
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
      setBusyOperation(null);
    }
  }

  async function restorePendingRecovery() {
    const pending = await getPendingWrite();
    if (!pending || recoveryRef.current) return;
    const retry = () => void runMutation<unknown>(
      pending.path,
      pending.body,
      (data) => {
        const result = data as { action_result?: string; chat_id?: number } | null;
        if (typeof result?.chat_id === 'number') void loadChannel(result.chat_id, true);
        void refresh(true);
        void loadAds(true);
        void loadStats(true);
        setNotice(result?.action_result
          ? proposalActionMessage(result.action_result)
          : 'Действие подтверждено. Данные обновляются.');
      },
      '',
      pending.method,
      true,
      { kind: 'recovery', action: 'retry' },
      ['bootstrap', 'ads', 'stats', ...(page.type === 'channel' ? [`channel:${page.id}`] : [])],
    );
    const restored: Recovery = {
      message: 'Предыдущее действие ещё не подтверждено. Повторите проверку, чтобы получить его результат.',
      retry,
    };
    recoveryRef.current = restored;
    setRecovery(restored);
  }

  function updateChannelSummary(data: ChannelDetail) {
    setBootstrap((current) => current ? {
      ...current,
      channels: current.channels.map((item) => item.chat_id === data.chat_id ? {
        ...item,
        title: data.title,
        status: data.status,
        subscribers: data.subscribers,
        proposal: data.proposal,
      } : item),
    } : current);
  }

  function applyChannel(data: ChannelDetail) {
    setChannelDetails((current) => ({ ...current, [data.chat_id]: data }));
    updateChannelSummary(data);
    setChannelStates((current) => ({ ...current, [data.chat_id]: { status: 'ready', refreshing: false, error: '' } }));
  }

  function applyAd(data: Ad) {
    setAdsData((current) => current ? {
      ...current,
      ads: current.ads.map((item) => item.id === data.id ? data : item),
    } : current);
    setStatsData((current) => current?.advertiser ? {
      ...current,
      advertiser: {
        ...current.advertiser,
        ads: current.advertiser.ads.map((item) => item.id === data.id ? data : item),
      },
    } : current);
  }

  const activeChannel = page.type === 'channel' ? channelDetails[page.id] ?? null : null;
  const activeAd = page.type === 'ad' ? adsData?.ads.find((ad) => ad.id === page.id) ?? null : null;
  const companyName = adsData?.advertiser?.name ?? bootstrap?.advertiser?.name ?? '';

  function returnToAds() {
    setTab('ads');
    setPage({ type: 'tabs' });
  }

  function adRouteFallback(title: string): React.ReactNode {
    if (!adsData && (adsState.status === 'loading' || adsState.status === 'idle')) return <Skeleton />;
    if (!adsData && adsState.error) {
      return <><PageHeader title={title} onBack={returnToAds} /><ResourceMessage title="Не удалось загрузить объявление" error={adsState.error} loading={adsState.refreshing} onRetry={() => void loadAds(true)} /><Button variant="secondary" size="large" stretched onClick={returnToAds}>К объявлениям</Button></>;
    }
    return (
      <>
        <PageHeader title={title} onBack={returnToAds} />
        <section className="empty-state compact-empty">
          <h2>Объявление недоступно</h2>
          <p>В текущем списке его нет. Вернитесь к объявлениям и обновите список.</p>
          <Button variant="secondary" size="large" stretched onClick={returnToAds}>К объявлениям</Button>
        </section>
      </>
    );
  }

  async function acceptConsent() {
    await runMutation<{ accepted: boolean }>(
      '/api/miniapp/consent', { accepted: true },
      () => { void refresh(true); },
      'Условия приняты',
      'POST', false, { kind: 'consent' }, ['bootstrap'],
    );
  }

  function saveWizardField(key: keyof Draft, value: string) {
    setWizard((current) => ({ ...current, values: { ...current.values, [key]: value } }));
    setFieldErrors((current) => ({ ...current, [key]: '' }));
  }

  function wizardNext() {
    if (typeof wizard.step !== 'number') return;
    const keyByStep: Record<number, keyof Draft> = {
      1: 'title', 2: 'body', 3: 'url', 4: 'category', 5: 'pricing_model', 6: 'price', 7: 'budget',
    };
    const field = keyByStep[wizard.step];
    const message = bootstrap ? validateDraftField(field, wizard.values, bootstrap) : 'Данные сервиса ещё не загружены.';
    if (message) {
      setFieldErrors({ [field]: message });
      return;
    }
    if (wizard.step === 7) setWizard((current) => ({ ...current, step: 'preview' }));
    else setWizard((current) => ({ ...current, step: (current.step as number) + 1 }));
  }

  function cancelWizard() {
    if (hasPendingWizard && !window.confirm('Отменить создание объявления и очистить введённые данные?')) return;
    setWizard({ step: 1, values: { ...EMPTY_DRAFT } });
    setFieldErrors({});
    try { sessionStorage.removeItem(wizardKey); } catch { /* optional */ }
    setTab('ads');
    setPage({ type: 'tabs' });
  }

  function createAd() {
    if (!bootstrap) return;
    const errors: Record<string, string> = {};
    (Object.keys(wizard.values) as Array<keyof Draft>).forEach((field) => {
      const message = validateDraftField(field, wizard.values, bootstrap);
      if (message) errors[field] = message;
    });
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      const firstInvalid = (Object.keys(wizard.values) as Array<keyof Draft>).find((field) => errors[field]);
      if (firstInvalid) setWizard((current) => ({ ...current, step: FIELD_TO_STEP[firstInvalid] }));
      return;
    }
    const price = normalizedMoney(wizard.values.price);
    const budget = normalizedMoney(wizard.values.budget);
    if (!price || !budget) return;
    runMutation<Ad>('/api/miniapp/ads', { ...wizard.values, price, budget }, (created, stillOnOrigin) => {
      setAdsData((current) => current ? {
        ...current,
        ads: [created, ...current.ads.filter((ad) => ad.id !== created.id)],
      } : { advertiser: null, ads: [created] });
      setBootstrap((current) => current ? {
        ...current,
        advertiser: current.advertiser ? { ...current.advertiser, ad_count: current.advertiser.ad_count + 1 } : current.advertiser,
      } : current);
      setWizard({ step: 1, values: { ...EMPTY_DRAFT } });
      if (stillOnOrigin()) {
        setPage({ type: 'ad', id: created.id });
        setTab('ads');
      }
    }, 'Объявление запущено и участвует в подборе', 'POST', false, { kind: 'ad-create' }, ['ads', 'stats', 'bootstrap']);
  }

  const recoveryBanner = recovery ? (
    <div className="recovery-banner" role="status">
      <p>{recovery.message}</p>
      <div className="recovery-actions">
        <Button variant="secondary" size="medium" loading={busy} disabled={busy} onClick={recovery.retry}>
          {busy ? `${busyOperationCopy(busyOperation)}…` : 'Проверить результат'}
        </Button>
      </div>
    </div>
  ) : null;

  if (!initData()) {
    return (
      <MaxUI>
        <div className="max-app auth-screen">
          <div className="auth-content">
            <h1>Откройте в MAX</h1>
            <p>Вход в сервис подтверждается MAX при запуске мини-приложения.</p>
            {authError ? <p role="alert">{authError}</p> : null}
            <Button variant="secondary" size="large" stretched loading={authChecking} disabled={authChecking} onClick={() => void recoverMaxBridge()}>
              Проверить подключение
            </Button>
          </div>
        </div>
      </MaxUI>
    );
  }

  if (bootstrap === null && bootstrapState.status === 'loading') {
    return <MaxUI><div className="max-app"><Skeleton /></div></MaxUI>;
  }

  if (bootstrap === null) {
    return (
      <MaxUI>
        <div className="max-app auth-screen">
          <div className="auth-content">
            <h1>Не удалось загрузить данные</h1>
            <p role="alert">{bootstrapState.error || 'Проверьте подключение и повторите запрос.'}</p>
            {authError ? <p role="alert">{authError}</p> : null}
            <Button variant="primary" size="large" stretched loading={bootstrapState.refreshing || authChecking} disabled={bootstrapState.refreshing || authChecking} onClick={() => void refresh(false)}>
              Повторить загрузку
            </Button>
            <Button variant="secondary" size="large" stretched loading={authChecking} disabled={authChecking} onClick={() => void recoverMaxBridge()}>
              Проверить подключение MAX
            </Button>
          </div>
        </div>
      </MaxUI>
    );
  }

  if (authExpired) {
    return (
      <MaxUI>
        <div className="max-app auth-screen">
          <div className="auth-content">
            <h1>Сессия завершилась</h1>
            <p>Данные входа истекли. Подключение MAX само по себе их не обновляет. Закройте и заново откройте мини-приложение в MAX.</p>
            {authError ? <p role="alert">{authError}</p> : null}
            <Button variant="primary" size="large" stretched loading={authChecking} disabled={authChecking} onClick={() => void recoverMaxBridge()}>
              Проверить подключение
            </Button>
          </div>
        </div>
      </MaxUI>
    );
  }

  if (bootstrap.consented === false) {
    return (
      <MaxUI>
        <div className="max-app consent-screen">
          <header className="page-heading"><h1>Добро пожаловать</h1></header>
          <p className="intro-copy">Перед началом примите условия сервиса.</p>
          <section className="surface consent-copy"><p>{bootstrap.consent_text || 'Примите условия сервиса, чтобы продолжить.'}</p></section>
          {error ? <div className="inline-error" role="alert">{error}</div> : null}
          {recoveryBanner}
          <div className="screen-action">
            <Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery)} onClick={() => void acceptConsent()}>
              {busyOperation?.kind === 'consent' ? 'Принимаем…' : 'Принимаю условия'}
            </Button>
          </div>
          {notice ? <div className="toast" role="status">{notice}</div> : null}
        </div>
      </MaxUI>
    );
  }

  const renderTabs = () => {
    if (tab === 'channels') {
      const channels = bootstrap?.channels ?? [];
      return (
        <>
          <PageHeading title="Каналы" subtitle="Предложения и настройки размещения" />
          {channels.length === 0 ? (
            <section className="empty-state">
              <div className="empty-mark" aria-hidden="true"><Icon name="channels" /></div>
              <h2>Подключите канал</h2>
              <p>Добавьте бота администратором канала с правами читать все сообщения и публиковать посты. Канал появится здесь после проверки.</p>
              <p className="muted-copy">По желанию включите право удалять сообщения: тогда реклама будет удаляться через 48 часов.</p>
              <p className="empty-help">В MAX откройте настройки канала → «Администраторы» и добавьте туда бота.</p>
            </section>
          ) : (
            <section className="list-group" aria-label="Мои каналы">
              {channels.map((channel) => {
                const state = channelStatus(channel.status);
                return (
                  <button className="channel-row" key={channel.chat_id} onClick={() => openPage({ type: 'channel', id: channel.chat_id })}>
                    <div className="row-main">
                      <div className="row-title-line"><strong>{channel.title}</strong><Status {...state} /></div>
                      <span className="row-secondary">{count(channel.subscribers)} подписчиков</span>
                      {channel.proposal ? (
                        <span className="row-proposal">
                          {channel.proposal.status === 'pending'
                            ? `Предложение: ${channel.proposal.ad_title} · доход ${money(channel.proposal.expected_income)}`
                            : proposalStatus(channel.proposal.status).label}
                        </span>
                      ) : <span className="row-secondary">Сейчас нет предложений</span>}
                    </div>
                    <Icon name="chevron" />
                  </button>
                );
              })}
            </section>
          )}
        </>
      );
    }

    if (tab === 'ads') {
      const advertiser = adsData?.advertiser;
      const ads = adsData?.ads ?? [];
      if (!adsData) {
        return (
          <>
            <PageHeading title="Объявления" subtitle="Кабинет рекламодателя" />
            <ResourceMessage
              title={adsState.status === 'loading' ? 'Загружаем объявления' : 'Не удалось загрузить объявления'}
              error={adsState.error}
              loading={adsState.status === 'loading' || adsState.refreshing}
              onRetry={() => void loadAds(false)}
            />
          </>
        );
      }
      if (!advertiser && !bootstrap?.advertiser.exists) {
        return (
          <>
            <PageHeading title="Объявления" subtitle="Кабинет рекламодателя" />
            <form className="surface form-surface" onSubmit={(event) => {
              event.preventDefault();
              const name = brandName.trim();
              if (!name) { setFieldErrors({ name: 'Укажите название компании или бренда.' }); return; }
              const maxName = bootstrap?.limits.company_name ?? 100;
              if (name.length > maxName) { setFieldErrors({ name: `Не больше ${maxName} символов.` }); return; }
              void runMutation<{ name: string }>('/api/miniapp/advertiser', { name }, async (data) => {
                setAdsData({ advertiser: { name: data.name }, ads: [] });
                setBootstrap((current) => current ? { ...current, advertiser: { exists: true, name: data.name, ad_count: 0 } } : current);
                setBrandName('');
              }, 'Название сохранено', 'POST', false, { kind: 'advertiser' }, ['ads', 'stats', 'bootstrap']);
            }}>
              <h2>Название компании или бренда</h2>
              <p className="muted-copy">Это название появится в маркировке рекламы.</p>
              <Field label="Компания или бренд" value={brandName} onChange={(value) => { setBrandName(value); setFieldErrors((current) => ({ ...current, name: '' })); }} maxLength={bootstrap?.limits.company_name} error={fieldErrors.name} autoComplete="organization" disabled={busy || Boolean(recovery)} />
              <Button type="submit" variant="primary" size="large" stretched loading={busyOperation?.kind === 'advertiser'} disabled={busy || Boolean(recovery)}>{busyOperation?.kind === 'advertiser' ? 'Создаём кабинет…' : 'Продолжить'}</Button>
            </form>
          </>
        );
      }
      return (
        <>
          <PageHeading title="Объявления" subtitle={advertiser?.name ?? bootstrap?.advertiser.name ?? 'Кабинет рекламодателя'} />
          {adsState.error ? <div className="inline-error" role="alert">{adsState.error} <button className="plain-action" onClick={() => void loadAds(true)}>Повторить</button></div> : null}
          {ads.length === 0 ? (
            <section className="empty-state compact-empty">
              <h2>Объявлений пока нет</h2>
              <p>Создайте объявление, чтобы бот мог предлагать его администраторам каналов.</p>
              <Button variant="primary" size="large" stretched disabled={Boolean(recovery)} onClick={() => openPage({ type: 'create' })}>Создать объявление</Button>
            </section>
          ) : (
            <>
              <div className="section-line"><span>{ads.length} {ads.length === 1 ? 'объявление' : 'объявления'}</span><span>Остаток: {money(ads.reduce((sum, ad) => sum + Number(ad.budget_left), 0))}</span></div>
              <section className="list-group" aria-label="Мои объявления">
                {ads.map((ad) => {
                  const state = adStatus(ad.status);
                  return (
                    <button className="ad-row" key={ad.id} onClick={() => openPage({ type: 'ad', id: ad.id })}>
                      <div className="row-main">
                        <div className="row-title-line"><strong>{ad.title}</strong><Status {...state} /></div>
                        <span className="row-secondary">Остаток бюджета · {money(ad.budget_left)}</span>
                        <span className="row-secondary">{ad.category_label} · {ad.pricing_label} {money(ad.price)}</span>
                      </div>
                      <Icon name="chevron" />
                    </button>
                  );
                })}
              </section>
            </>
          )}
          {ads.length > 0 ? <div className="screen-action"><Button variant="primary" size="large" stretched disabled={Boolean(recovery)} onClick={() => openPage({ type: 'create' })}>Создать объявление</Button></div> : null}
          <p className="footnote">Пополнение бюджета демонстрационное. Реальной оплаты и модерации нет; ERID демонстрационный.</p>
        </>
      );
    }

    return <StatsScreen data={statsData} state={statsState} onRetry={() => void loadStats(false)} />;
  };

  const renderChannelPage = () => {
    const id = page.type === 'channel' ? page.id : 0;
    const channel = activeChannel;
    const channelState = channelStates[id] ?? initialResource;
    if (!channel) {
      if (channelState.status === 'missing') {
        return (
          <>
            <PageHeader title="Канал недоступен" onBack={goBack} />
            <section className="empty-state compact-empty">
              <h2>Канал больше не доступен</h2>
              <p>Он мог быть отключён или удалён из кабинета. Обновите данные или вернитесь к списку каналов.</p>
              <div className="screen-action">
                <Button variant="secondary" size="large" stretched loading={channelState.refreshing} disabled={channelState.refreshing} onClick={() => void loadChannel(id)}>Повторить загрузку</Button>
                <Button variant="secondary" size="large" stretched onClick={goBack}>К каналам</Button>
              </div>
            </section>
          </>
        );
      }
      return channelState.status === 'error' ? (
        <>
          <PageHeader title="Канал" onBack={goBack} />
          <ResourceMessage title="Не удалось загрузить канал" error={channelState.error} loading={false} onRetry={() => void loadChannel(id)} />
        </>
      ) : <Skeleton />;
    }
    const cstate = channelStatus(channel.status);
    const proposal = channel.proposal;
    const proposalActionable = proposal ? proposalIsActionable(proposal) : false;
    const latestProposalState = proposal
      ? (proposal.status === 'pending' && !proposalActionable
        ? { label: Number.isFinite(Date.parse(proposal.expires_at)) && Date.parse(proposal.expires_at) <= Date.now() ? 'Срок истёк' : 'Недоступно', tone: 'muted' }
        : proposalStatus(proposal.status))
      : null;
    const nextExhausted = proposal?.id === nextExhaustedId;
    return (
      <>
        <PageHeader title={channel.title} onBack={goBack} refreshing={channelState.refreshing} onRefresh={() => void loadChannel(id, true)} />
        {channelState.error ? <div className="inline-error" role="alert">Не удалось обновить канал: {channelState.error} <button className="plain-action" onClick={() => void loadChannel(id, true)}>Повторить</button></div> : null}
        <div className="detail-meta"><Status {...cstate} /><span>{count(channel.subscribers)} подписчиков</span></div>
        {channel.status === 'insufficient_rights' ? (
          <section className="notice-panel warning-panel">
            <h2>Проверьте права бота</h2>
            <p>В настройках канала у бота должны быть права читать все сообщения и публиковать посты. После изменения MAX проверит их автоматически.</p>
            <p className="muted-copy">Доступно боту: {permissionSummary(channel.permissions)}.</p>
          </section>
        ) : null}
        {channel.status === 'pending_consent' ? (
          <section className="notice-panel warning-panel"><h2>Нужно принять условия</h2><p>После согласия в разделе «Каналы» бот проверит подключение и права.</p></section>
        ) : null}
        {channel.profile_summary ? <p className="channel-profile">{channel.profile_summary}</p> : null}
        {proposal ? (
          <section className="proposal-section" aria-label="Предложение рекламы">
            <div className="proposal-heading">
              <div><h2>{proposal.status === 'pending' ? 'Предложение рекламы' : 'Последнее предложение'}</h2><span>{channel.title}</span></div>
              {latestProposalState ? <Status {...latestProposalState} /> : null}
            </div>
            {proposalActionable ? (
              <ProposalCard
                proposal={proposal}
                busy={busy}
                busyOperation={busyOperation}
                recovering={Boolean(recovery)}
                nextDisabled={!proposal.next_available || nextExhausted}
                nextUnavailableReason={nextExhausted
                  ? 'Подходящих альтернатив больше нет.'
                  : !proposal.next_available ? 'Сейчас других вариантов нет.' : ''}
                onAction={(action) => {
                  runMutation<ChannelDetail>(`/api/miniapp/proposals/${proposal.id}/action`, { action }, (data) => {
                    if (data.action_result === 'no_more') setNextExhaustedId(proposal.id);
                    if (data.proposal?.id !== proposal.id) setNextExhaustedId(null);
                    applyChannel(data);
                    if (currentPageKey.current === `channel:${data.chat_id}`) setNotice(proposalActionMessage(data.action_result));
                  }, '', 'POST', false, { kind: 'proposal', entityId: proposal.id, action }, [`channel:${id}`, 'bootstrap']);
                }}
              />
            ) : (
              <div className="state-copy">
                {proposal.status === 'approved' ? <p>Реклама пока не опубликована. Плановое время: {localTime(proposal.publish_at)}.</p> : null}
                {proposal.status !== 'approved' ? <p>{proposalStateText(proposal)}</p> : null}
              </div>
            )}
          </section>
        ) : (
          <section className="notice-panel quiet-panel"><h2>Предложений сейчас нет</h2><p>Когда в канале появится подходящий пост, бот пришлёт предложение. Пока есть одно неразобранное предложение, новые не отправляются.</p></section>
        )}

        <section className="settings-section" aria-label="Настройки канала">
          <div className="section-heading"><h2>Настройки канала</h2><p>Категории, пауза и автоудаление</p></div>
          <div className="category-list">
            {channel.categories.map((category) => (
              <button
                className="category-row"
                key={category.code}
                role="switch"
                aria-checked={category.allowed}
                disabled={busy || Boolean(recovery)}
                onClick={() => runMutation<ChannelDetail>(`/api/miniapp/channels/${channel.chat_id}/category`, {
                  category: category.code, allowed: !category.allowed,
                }, applyChannel, `${category.label}: ${category.allowed ? 'отключена' : 'разрешена'}`, 'POST', false, { kind: 'category', entityId: channel.chat_id, category: category.code }, [`channel:${channel.chat_id}`, 'bootstrap'])}
              >
                <span className="category-name">{category.label}{category.regulated ? <span className="regulated-mark" aria-label="Регулируемая категория"> ⚖</span> : null}</span>
                <span className={`switch ${category.allowed ? 'switch-on' : ''}`}><span /></span>
              </button>
            ))}
          </div>
          {busyOperation?.kind === 'category' && busyOperation.entityId === channel.chat_id ? <p className="save-state" role="status">Сохраняем настройку…</p> : null}
          <p className="settings-hint">Регулируемые категории выключены, пока вы их не разрешите.</p>
          <div className="retention-line">
            <strong>Автоудаление рекламы</strong>
            <span>{channel.can_delete ? `Через ${channel.ad_ttl_hours} ч` : 'Недоступно: боту не выдано право удалять сообщения'}</span>
          </div>
          {channel.status === 'active' || channel.status === 'paused' ? (
            <Button
              variant="secondary"
              size="large"
              stretched
              loading={busyOperation?.kind === 'channel-pause' && busyOperation.entityId === channel.chat_id}
              disabled={busy || Boolean(recovery)}
              onClick={() => runMutation<ChannelDetail>(`/api/miniapp/channels/${channel.chat_id}/pause`, {
                paused: channel.status === 'active',
              }, applyChannel, channel.status === 'active' ? 'Канал приостановлен' : 'Работа канала возобновлена', 'POST', false, { kind: 'channel-pause', entityId: channel.chat_id, action: channel.status === 'active' ? 'pause' : 'resume' }, [`channel:${channel.chat_id}`, 'bootstrap'])}
            >{busyOperation?.kind === 'channel-pause' && busyOperation.entityId === channel.chat_id ? 'Сохраняем…' : channel.status === 'active' ? 'Приостановить канал' : 'Возобновить канал'}</Button>
          ) : null}
        </section>
      </>
    );
  };

  const renderAdPage = () => {
    const ad = activeAd;
    if (!ad) {
      return adRouteFallback('Объявление');
    }
    const status = adStatus(ad.status);
    return (
      <>
        <PageHeader title={ad.title} onBack={goBack} refreshing={adsState.refreshing} onRefresh={() => void loadAds(true)} />
        {adsState.error ? <div className="inline-error" role="alert">Не удалось обновить объявление: {adsState.error} <button className="plain-action" onClick={() => void loadAds(true)}>Повторить</button></div> : null}
        <div className="detail-meta"><Status {...status} /><span>Остаток {money(ad.budget_left)}</span></div>
        <section className="ad-detail-section">
          <div className="editable-row"><div><span className="detail-label">Заголовок</span><h2>{ad.title}</h2></div><Button aria-label="Изменить заголовок" variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.title); openPage({ type: 'edit', id: ad.id, field: 'title' }); }}>Изменить</Button></div>
          <div className="editable-row align-start"><div><span className="detail-label">Текст</span><p className="ad-body">{ad.body}</p></div><Button aria-label="Изменить текст объявления" variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.body); openPage({ type: 'edit', id: ad.id, field: 'body' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Ссылка</span><button className="text-link" onClick={() => openExternal(ad.url)}>{ad.url}</button></div><Button aria-label="Изменить ссылку" variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.url); openPage({ type: 'edit', id: ad.id, field: 'url' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Категория</span><strong>{ad.category_label}</strong></div><Button aria-label="Изменить категорию" variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.category); openPage({ type: 'edit', id: ad.id, field: 'category' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Цена</span><strong>{ad.pricing_label} · {money(ad.price)}</strong><span className="row-secondary">{ad.pricing_unit}</span></div><Button aria-label="Изменить цену" variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.price); openPage({ type: 'edit', id: ad.id, field: 'price' }); }}>Изменить</Button></div>
        </section>
        <section className="ad-budget-section">
          <div className="section-heading"><h2>Бюджет</h2><p>Демо-пополнение · без списания денег</p></div>
          <div className="budget-numbers"><strong>{money(ad.budget_left)}</strong><span>из {money(ad.budget_total)}</span></div>
          <div className="budget-track"><span style={{ width: `${Math.min(100, Math.max(0, Number(ad.budget_total) > 0 ? (Number(ad.budget_left) / Number(ad.budget_total)) * 100 : 0))}%` }} /></div>
          <div className="ad-actions-row">
            <Button variant="secondary" size="medium" disabled={busy || Boolean(recovery)} onClick={() => openPage({ type: 'topup', id: ad.id })}>Пополнить демо</Button>
            {ad.status !== 'exhausted' ? <Button variant="secondary" size="medium" loading={busyOperation?.kind === 'ad-pause' && busyOperation.entityId === ad.id} disabled={busy || Boolean(recovery)} onClick={() => runMutation<Ad>(`/api/miniapp/ads/${ad.id}/pause`, {
              paused: ad.status === 'active',
            }, (updated) => { applyAd(updated); }, ad.status === 'active' ? 'Объявление приостановлено' : 'Объявление возобновлено', 'POST', false, { kind: 'ad-pause', entityId: ad.id, action: ad.status === 'active' ? 'pause' : 'resume' }, ['ads', 'stats', 'bootstrap'])}>{busyOperation?.kind === 'ad-pause' && busyOperation.entityId === ad.id ? 'Сохраняем…' : ad.status === 'active' ? 'Приостановить' : 'Возобновить'}</Button> : null}
          </div>
        </section>
        <section className="stats-strip" aria-label="Статистика объявления">
          {hasAdActivity(ad) ? <>
            <Stat label="Размещений" value={count(ad.placements)} />
            <Stat label="Просмотров" value={count(ad.views)} />
            <Stat label="Кликов" value={count(ad.clicks)} />
            <Stat label="Потрачено" value={money(ad.spent)} />
          </> : <p className="no-stats">Пока нет статистики по этому объявлению.</p>}
        </section>
        <p className="footnote">Объявление сразу участвует в подборе. Модерации нет; ERID демонстрационный.</p>
      </>
    );
  };

  const renderCreate = () => {
    const step = wizard.step;
    const steps: Record<number, { title: string; field: keyof Draft; label: string; help: string }> = {
      1: { title: 'Заголовок', field: 'title', label: 'Заголовок объявления', help: `До ${bootstrap?.limits.title ?? 100} символов` },
      2: { title: 'Текст', field: 'body', label: 'Текст объявления', help: `До ${bootstrap?.limits.body ?? 1000} символов` },
      3: { title: 'Ссылка', field: 'url', label: 'Куда вести читателей', help: 'Ссылка должна начинаться с http:// или https://' },
      6: { title: 'Цена', field: 'price', label: 'Цена в рублях', help: 'Например, 250 или 1500,50' },
      7: { title: 'Бюджет', field: 'budget', label: 'Бюджет в рублях', help: 'Не меньше цены объявления' },
    };
    const preview = step === 'preview';
    const current = typeof step === 'number' ? steps[step] : undefined;
    const categoryLabel = bootstrap?.categories.find((category) => category.code === wizard.values.category)?.label ?? '';
    const priceModel: PricingModel | undefined = bootstrap?.pricing_models.find((model) => model.code === wizard.values.pricing_model);
    const company = companyName || bootstrap?.advertiser.name || '';
    return (
      <>
        <PageHeader title={preview ? 'Проверьте объявление' : 'Новое объявление'} onBack={goBack} />
        <form onSubmit={(event) => { event.preventDefault(); if (preview) createAd(); else wizardNext(); }}>
        {!preview ? (
          <>
            <div className="wizard-progress"><span>Шаг {step} из 7</span><div className="progress-track"><span style={{ width: `${(Number(step) / 7) * 100}%` }} /></div></div>
            <h2 className="wizard-step-title">{step === 4 ? 'Выберите категорию' : step === 5 ? 'Модель оплаты' : current?.title}</h2>
            {current ? (
              <div className="wizard-field-group">
                <Field
                  label={current.label}
                  value={wizard.values[current.field]}
                  onChange={(value) => saveWizardField(current.field, value)}
                  error={fieldErrors[current.field]}
                  maxLength={current.field === 'title' ? bootstrap?.limits.title : current.field === 'body' ? bootstrap?.limits.body : current.field === 'url' ? bootstrap?.limits.url : undefined}
                  placeholder={current.field === 'url' ? 'https://example.com' : undefined}
                  multiline={current.field === 'body'}
                  inputMode={current.field === 'url' ? 'url' : current.field === 'price' || current.field === 'budget' ? 'decimal' : 'text'}
                  type="text"
                  autoFocus
                  disabled={busy || Boolean(recovery)}
                />
                <p className="field-help">{current.help}</p>
              </div>
            ) : null}
            {step === 4 ? (
              <>
                <RadioChoices
                  label="Категории объявлений"
                  value={wizard.values.category}
                  error={fieldErrors.category}
                  onChange={(value) => saveWizardField('category', value)}
                  autoFocus
                  choices={(bootstrap?.categories ?? []).map((category) => ({
                    value: category.code,
                    content: <span>{category.label}{category.regulated ? <span className="regulated-mark"> ⚖</span> : null}</span>,
                  }))}
                />
              </>
            ) : null}
            {step === 5 ? (
              <>
                <RadioChoices
                  label="Модель оплаты"
                  value={wizard.values.pricing_model}
                  error={fieldErrors.pricing_model}
                  onChange={(value) => saveWizardField('pricing_model', value)}
                  autoFocus
                  choices={(bootstrap?.pricing_models ?? []).map((model) => ({
                    value: model.code,
                    content: <span><strong>{model.label}</strong><small>{model.unit}</small></span>,
                  }))}
                />
              </>
            ) : null}
            <div className="wizard-actions">
              <span aria-hidden="true" />
              <Button type="submit" variant="primary" size="large" loading={busyOperation?.kind === 'ad-create'} disabled={busy || Boolean(recovery)}>{busyOperation?.kind === 'ad-create' ? 'Запускаем…' : 'Продолжить'}</Button>
            </div>
            <button type="button" className="cancel-flow" onClick={cancelWizard} disabled={busy}>Отменить создание</button>
          </>
        ) : (
          <>
            <div className="preview-post">
              <div className="preview-brand">{company || 'Рекламодатель'}</div>
              <h2 data-auto-focus="true" tabIndex={-1}>{wizard.values.title}</h2>
              <p className="preview-body">{wizard.values.body}</p>
              <p className="preview-marking">#Реклама. {company}. erid: присвоим при запуске</p>
              <button type="button" className="preview-link" onClick={() => openExternal(wizard.values.url)}>{wizard.values.url}</button>
            </div>
            <div className="preview-details">
              <div className="detail-pair"><span>Категория</span><strong>{categoryLabel}</strong></div>
              <div className="detail-pair"><span>Модель оплаты</span><strong>{priceModel?.label} · {money(wizard.values.price)} {priceModel?.unit}</strong></div>
              <div className="detail-pair"><span>Бюджет</span><strong>{money(wizard.values.budget)}</strong></div>
            </div>
            <div className="demo-note">Запуск добавит объявление в подбор. Платёжной интеграции и модерации нет; ERID демонстрационный.</div>
            {error ? <div className="inline-error" role="alert">{error}</div> : null}
            {Object.entries(fieldErrors).map(([key, message]) => message ? <p className="field-error" role="alert" key={key}>{message}</p> : null)}
            <div className="wizard-actions preview-actions">
              <span aria-hidden="true" />
              <Button type="submit" variant="primary" size="large" loading={busyOperation?.kind === 'ad-create'} disabled={busy || Boolean(recovery)}>{busyOperation?.kind === 'ad-create' ? 'Запускаем…' : 'Запустить объявление'}</Button>
            </div>
            <button type="button" className="cancel-flow" onClick={cancelWizard} disabled={busy}>Отменить создание</button>
          </>
        )}
        </form>
      </>
    );
  };

  const renderEdit = () => {
    if (page.type !== 'edit') return null;
    const ad = adsData?.ads.find((item) => item.id === page.id);
    if (!ad) return adRouteFallback('Редактирование');
    const labels = { title: 'Заголовок', body: 'Текст объявления', url: 'Ссылка', price: 'Цена', category: 'Категория' };
    const categoryField = page.field === 'category';
    const saveEdit = () => {
      if (!bootstrap) return;
      const message = validateAdEditField(page.field, editValue, bootstrap);
      if (message) { setFieldErrors({ [page.field]: message }); return; }
      const value = page.field === 'price' ? normalizedMoney(editValue) : editValue.trim();
      if (!value) { setFieldErrors({ price: 'Укажите сумму от 0,01 до 100 000 000 ₽.' }); return; }
      const serverValue = page.field === 'price' ? normalizedMoney(ad.price) : String(ad[page.field]).trim();
      if (value === serverValue) {
        setFieldErrors({});
        setPage({ type: 'ad', id: ad.id });
        return;
      }
      runMutation<Ad>(`/api/miniapp/ads/${ad.id}/field`, { field: page.field, value }, (updated, stillOnOrigin) => {
        applyAd(updated);
        if (stillOnOrigin()) setPage({ type: 'ad', id: ad.id });
      }, 'Изменения сохранены', 'PUT', false, { kind: 'ad-edit', entityId: ad.id, field: page.field }, ['ads', 'stats', 'bootstrap']);
    };
    return (
      <>
        <PageHeader title={labels[page.field]} onBack={goBack} />
        <form onSubmit={(event) => { event.preventDefault(); saveEdit(); }}>
        {categoryField ? (
          <RadioChoices
            label="Новая категория"
            value={editValue}
            error={fieldErrors.category}
            onChange={(value) => { setEditValue(value); setFieldErrors((current) => ({ ...current, category: '' })); }}
            autoFocus
            choices={(bootstrap?.categories ?? []).map((category) => ({
              value: category.code,
              content: <span>{category.label}{category.regulated ? <span className="regulated-mark"> ⚖</span> : null}</span>,
            }))}
          />
        ) : (
          <Field label={labels[page.field]} value={editValue} onChange={(value) => { setEditValue(value); setFieldErrors((current) => ({ ...current, [page.field]: '' })); }} error={fieldErrors[page.field]} maxLength={page.field === 'title' ? bootstrap?.limits.title : page.field === 'body' ? bootstrap?.limits.body : page.field === 'url' ? bootstrap?.limits.url : undefined} multiline={page.field === 'body'} inputMode={page.field === 'url' ? 'url' : page.field === 'price' ? 'decimal' : 'text'} type="text" autoFocus disabled={busy || Boolean(recovery)} />
        )}
        {error ? <div className="inline-error" role="alert">{error}</div> : null}
        <div className="screen-action"><Button type="submit" variant="primary" size="large" stretched loading={busyOperation?.kind === 'ad-edit' && busyOperation.entityId === ad.id} disabled={busy || Boolean(recovery)}>{busyOperation?.kind === 'ad-edit' && busyOperation.entityId === ad.id ? 'Сохраняем…' : 'Сохранить'}</Button></div>
        </form>
      </>
    );
  };

  const renderTopup = () => {
    if (page.type !== 'topup') return null;
    const ad = adsData?.ads.find((item) => item.id === page.id);
    if (!ad) return adRouteFallback('Пополнение бюджета');
    const submitTopup = () => {
      const amount = normalizedMoney(topupValue);
      if (!amount) { setFieldErrors({ amount: 'Укажите сумму от 0,01 до 100 000 000 ₽.' }); return; }
      runMutation<Ad>(`/api/miniapp/ads/${page.id}/topup`, { amount }, (updated, stillOnOrigin) => {
        applyAd(updated);
        setTopupValue('');
        if (stillOnOrigin()) setPage({ type: 'ad', id: updated.id });
      }, `Бюджет пополнен на ${money(amount)} (демо)`, 'POST', false, { kind: 'ad-topup', entityId: page.id }, ['ads', 'stats', 'bootstrap']);
    };
    return (
      <>
        <PageHeader title="Пополнение бюджета" onBack={goBack} />
        <form className="surface form-surface" onSubmit={(event) => { event.preventDefault(); submitTopup(); }}>
          <h2>{ad?.title}</h2>
          <div className="demo-note">Демонстрационное пополнение: сумма изменится в кабинете, реальные деньги не списываются.</div>
          <Field label="Сумма, ₽" value={topupValue} onChange={(value) => { setTopupValue(value); setFieldErrors((current) => ({ ...current, amount: '' })); }} error={fieldErrors.amount} inputMode="decimal" placeholder="Например, 500" autoFocus disabled={busy || Boolean(recovery)} />
          {error ? <div className="inline-error" role="alert">{error}</div> : null}
          <Button type="submit" variant="primary" size="large" stretched loading={busyOperation?.kind === 'ad-topup' && busyOperation.entityId === page.id} disabled={busy || Boolean(recovery)}>{busyOperation?.kind === 'ad-topup' && busyOperation.entityId === page.id ? 'Пополняем…' : 'Пополнить демо'}</Button>
        </form>
      </>
    );
  };

  return (
    <MaxUI>
      <div className="max-app">
        {error && page.type !== 'create' && page.type !== 'edit' && page.type !== 'topup' ? <div className="global-error" role="alert">{error}<button onClick={() => { setError(''); if (tab === 'ads') void loadAds(true); else if (tab === 'stats') void loadStats(true); else if (page.type === 'channel') void loadChannel(page.id, true); else void refresh(true); }}>Обновить</button></div> : null}
        {recoveryBanner}
        {page.type === 'tabs' ? <main className="page-content">
          {bootstrapState.refreshing ? <p className="muted-copy" role="status">Обновляем данные сервиса…</p> : null}
          {bootstrapState.error ? <div className="inline-error" role="alert">Не удалось обновить данные сервиса: {bootstrapState.error} <button className="plain-action" onClick={() => void refresh(true)}>Повторить</button></div> : null}
          {renderTabs()}
        </main> : (
          <main className="page-content subpage">
            {page.type === 'channel' ? renderChannelPage() : null}
            {page.type === 'ad' ? renderAdPage() : null}
            {page.type === 'create' ? renderCreate() : null}
            {page.type === 'edit' ? renderEdit() : null}
            {page.type === 'topup' ? renderTopup() : null}
          </main>
        )}
        {page.type === 'tabs' ? <TabBar active={tab} onChange={changeTab} /> : null}
        {notice ? <div className="toast" role="status">{notice}</div> : null}
      </div>
    </MaxUI>
  );

}

function PageHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return <header className="page-heading"><h1 data-route-heading tabIndex={-1}>{title}</h1>{subtitle ? <p>{subtitle}</p> : null}</header>;
}

function PageHeader({ title, onBack, refreshing, onRefresh }: { title: string; onBack: () => void; refreshing?: boolean; onRefresh?: () => void }) {
  return (
    <header className="subpage-heading">
      <button className="back-control" onClick={onBack} aria-label="Назад"><Icon name="back" /><span>Назад</span></button>
      <h1 data-route-heading tabIndex={-1}>{title}</h1>
      {onRefresh ? <button className="refresh-control" onClick={onRefresh} disabled={refreshing}>{refreshing ? 'Обновляем…' : 'Обновить'}</button> : <span className="heading-spacer" />}
    </header>
  );
}

function TabBar({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  const tabs: Array<{ id: Tab; label: string; icon: 'channels' | 'ads' | 'stats' }> = [
    { id: 'channels', label: 'Каналы', icon: 'channels' },
    { id: 'ads', label: 'Объявления', icon: 'ads' },
    { id: 'stats', label: 'Статистика', icon: 'stats' },
  ];
  return (
    <nav className="bottom-nav" aria-label="Разделы">
      {tabs.map((tab) => (
        <button key={tab.id} className={`tab-button ${active === tab.id ? 'tab-active' : ''}`} aria-current={active === tab.id ? 'page' : undefined} onClick={() => onChange(tab.id)}>
          <Icon name={tab.icon} /><span>{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}

function RadioChoices({
  label,
  value,
  choices,
  onChange,
  error,
  autoFocus = false,
}: {
  label: string;
  value: string;
  choices: Array<{ value: string; content: React.ReactNode }>;
  onChange: (value: string) => void;
  error?: string;
  autoFocus?: boolean;
}) {
  const errorId = `choice-error-${label.toLowerCase().replace(/[^a-zа-я0-9]+/gi, '-')}`;
  function moveFocus(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const delta = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1
      : event.key === 'ArrowUp' || event.key === 'ArrowLeft' ? -1
        : event.key === 'Home' ? -choices.length
          : event.key === 'End' ? choices.length
            : 0;
    if (!delta || !choices.length) return;
    event.preventDefault();
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 : (index + delta + choices.length) % choices.length;
    onChange(choices[nextIndex].value);
    (event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [])[nextIndex]?.focus();
  }

  return (
    <>
      <div className="choice-list" role="radiogroup" aria-label={label} aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined}>
      {choices.map((choice, index) => {
        const selected = value === choice.value;
        const hasSelection = choices.some((item) => item.value === value);
        const firstTabStop = !hasSelection && index === 0;
        return (
          <button
            type="button"
            key={choice.value}
            className={`choice-row ${selected ? 'choice-selected' : ''}`}
            role="radio"
            aria-checked={selected}
            tabIndex={selected || firstTabStop ? 0 : -1}
            data-auto-focus={autoFocus && (selected || firstTabStop) ? 'true' : undefined}
            onKeyDown={(event) => moveFocus(event, index)}
            onClick={() => onChange(choice.value)}
          >
            {choice.content}<span className="radio-mark" aria-hidden="true" />
          </button>
        );
      })}
      </div>
      {error ? <p className="field-error" id={errorId} role="alert">{error}</p> : null}
    </>
  );
}

function ProposalCard({
  proposal,
  busy,
  busyOperation,
  recovering,
  nextDisabled,
  nextUnavailableReason,
  onAction,
}: {
  proposal: NonNullable<ChannelDetail['proposal']>;
  busy: boolean;
  busyOperation: BusyOperation | null;
  recovering: boolean;
  nextDisabled: boolean;
  nextUnavailableReason: string;
  onAction: (action: 'approve' | 'reject' | 'next' | 'block_cat') => void;
}) {
  const proposalBusy = busyOperation?.kind === 'proposal' && busyOperation.entityId === proposal.id;
  const actionBusy = (action: 'approve' | 'reject' | 'next' | 'block_cat') => proposalBusy && busyOperation.action === action;
  return (
    <div className="proposal-card">
      <div className="offer-main">
        <h3>{proposal.ad_title}</h3>
        <p className="offer-reason">{proposal.reason}</p>
        <div className="offer-income"><span>Ожидаемый доход канала</span><strong>{money(proposal.expected_income)}</strong></div>
        <div className="offer-payment"><span>{proposal.pricing_label} · {money(proposal.price)} {proposal.pricing_unit}</span><span className="offer-expiry">До {localTime(proposal.expires_at)}</span></div>
      </div>
      <details className="source-context">
        <summary>Исходный пост</summary>
        <p>{proposal.post_text}</p>
      </details>
      <div className="proposal-actions">
        <div className="primary-actions">
          <Button variant="primary" size="large" stretched loading={actionBusy('approve')} disabled={busy || recovering} onClick={() => onAction('approve')}>{actionBusy('approve') ? 'Одобряем…' : 'Одобрить'}</Button>
          <Button variant="secondary" size="large" stretched loading={actionBusy('next')} disabled={busy || recovering || nextDisabled} onClick={() => onAction('next')}>{actionBusy('next') ? 'Ищем вариант…' : 'Другой вариант'}</Button>
        </div>
        {nextDisabled && nextUnavailableReason ? <p className="muted-copy" role="status">{nextUnavailableReason}</p> : null}
        <Button variant="destructive" size="large" stretched loading={actionBusy('reject')} disabled={busy || recovering} onClick={() => onAction('reject')}>{actionBusy('reject') ? 'Отклоняем…' : 'Отклонить это объявление'}</Button>
        <div className="category-block-action">
          <span>Больше не показывать рекламу из категории «{proposal.category_label}» в этом канале</span>
          <Button variant="destructive" size="large" stretched loading={actionBusy('block_cat')} disabled={busy || recovering} onClick={() => onAction('block_cat')}>{actionBusy('block_cat') ? 'Сохраняем запрет…' : 'Не предлагать категорию'}</Button>
        </div>
      </div>
    </div>
  );
}

function ResourceMessage({ title, error, loading, onRetry }: { title: string; error: string; loading: boolean; onRetry: () => void }) {
  return (
    <section className="empty-state compact-empty" aria-busy={loading}>
      <h2>{title}</h2>
      {error ? <p role="alert">{error}</p> : null}
      <Button variant="secondary" size="large" stretched loading={loading} disabled={loading} onClick={onRetry}>Повторить загрузку</Button>
    </section>
  );
}

function StatsScreen({ data, state, onRetry }: { data: StatsData | null; state: ResourceState; onRetry: () => void }) {
  if (!data) {
    return (
      <>
        <PageHeading title="Статистика" subtitle="Данные обновляются после сбора просмотров и кликов" />
        <ResourceMessage
          title={state.status === 'loading' ? 'Загружаем статистику' : 'Не удалось загрузить статистику'}
          error={state.error}
          loading={state.status === 'loading' || state.refreshing}
          onRetry={onRetry}
        />
      </>
    );
  }
  if (data.channels.length === 0 && !data.advertiser) {
    return (
      <>
        <PageHeading title="Статистика" subtitle="Данные обновляются после сбора просмотров и кликов" />
        {state.error ? <div className="inline-error" role="alert">Не удалось обновить статистику: {state.error} <button className="plain-action" onClick={onRetry}>Повторить</button></div> : null}
        <section className="empty-state compact-empty"><h2>Пока нет статистики</h2><p>Здесь появятся показатели каналов и ваших объявлений, когда начнутся размещения.</p></section>
      </>
    );
  }
  return (
    <>
      <PageHeading title="Статистика" subtitle="Доход канала и расход рекламодателя показаны отдельно" />
      {state.error ? <div className="inline-error" role="alert">Не удалось обновить статистику: {state.error} <button className="plain-action" onClick={onRetry}>Повторить</button></div> : null}
      {state.refreshing ? <p className="muted-copy" role="status">Обновляем статистику…</p> : null}
      {data.channels.length ? (
        <section className="stats-section">
          <h2>Доход каналов</h2>
          {data.channels.map((channel) => (
            <div className="stats-block" key={channel.chat_id}>
              <h3>{channel.title}</h3>
              {(['7', '30'] as const).map((period) => <PeriodStats key={period} days={period} stats={channel.periods[period]} />)}
            </div>
          ))}
        </section>
      ) : <section className="quiet-line"><h2>Каналы</h2><p>Нет подключённых каналов.</p></section>}
      {data.advertiser ? (
        <section className="stats-section advertiser-stats">
          <h2>Расход рекламодателя</h2>
          <p className="muted-copy">«{data.advertiser.name}» · за всё время</p>
          {data.advertiser.summary.placements || data.advertiser.summary.views || data.advertiser.summary.clicks || Number(data.advertiser.summary.spent) > 0 ? (
            <div className="stats-grid">
              <Stat label="Размещений" value={count(data.advertiser.summary.placements)} />
              <Stat label="Просмотров" value={count(data.advertiser.summary.views)} />
              <Stat label="Кликов" value={count(data.advertiser.summary.clicks)} />
              <Stat label="Потрачено" value={money(data.advertiser.summary.spent)} />
            </div>
          ) : <p className="no-stats">Пока нет статистики по объявлениям.</p>}
          <div className="per-ad-stats">
            {data.advertiser.ads.map((ad) => (
              <div className="per-ad-row" key={ad.id}>
                <strong>{ad.title}</strong>
                {hasAdActivity(ad) ? <>
                  <span>{count(ad.placements)} размещений · {count(ad.views)} просмотров · {count(ad.clicks)} кликов</span>
                  <span>Потрачено {money(ad.spent)} · остаток {money(ad.budget_left)}</span>
                </> : <span>Пока нет статистики · остаток бюджета {money(ad.budget_left)}</span>}
              </div>
            ))}
            {!data.advertiser.ads.length ? <p className="muted-copy">Пока нет объявлений.</p> : null}
          </div>
        </section>
      ) : <section className="quiet-line"><h2>Рекламодатель</h2><p>Кабинет рекламодателя ещё не создан.</p></section>}
    </>
  );
}

function PeriodStats({ days, stats }: { days: '7' | '30'; stats: ChannelStats }) {
  return (
    <div className="period-block">
      <div className="period-heading">За {days} дней</div>
      {hasChannelActivity(stats) ? <div className="stats-grid">
        <Stat label="Размещений" value={count(stats.placements)} />
        <Stat label="Просмотров" value={count(stats.views)} />
        <Stat label="Кликов" value={count(stats.clicks)} />
        <Stat label="Заработано" value={money(stats.earned)} />
      </div> : <p className="no-stats">За этот период данных пока нет.</p>}
    </div>
  );
}

function openExternal(url: string) {
  try {
    const destination = new URL(url);
    if (destination.protocol !== 'http:' && destination.protocol !== 'https:') return;
    if (window.WebApp?.openLink) window.WebApp.openLink(destination.toString());
    else window.open(destination.toString(), '_blank', 'noopener,noreferrer');
  } catch {
    // Invalid or relative addresses are not opened outside the app.
  }
}

export default function App() {
  return <AppContent />;
}
