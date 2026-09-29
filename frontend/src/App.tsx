import { Button, MaxUI } from '@maxhub/max-ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, getAds, getBootstrap, getChannel, getStats, initData, mutate } from './api';
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
import './styles.css';

type Draft = {
  title: string;
  body: string;
  url: string;
  category: string;
  pricing_model: string;
  price: string;
  budget: string;
};

type WizardState = { step: number | 'preview'; values: Draft };
type Recovery = { message: string; retry: () => void };
type MutationMethod = 'POST' | 'PUT';

const EMPTY_DRAFT: Draft = {
  title: '',
  body: '',
  url: '',
  category: '',
  pricing_model: '',
  price: '',
  budget: '',
};

const WIZARD_STORAGE = 'ctxads.miniapp.creation.v1';
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
    const saved = sessionStorage.getItem(WIZARD_STORAGE);
    if (saved) return JSON.parse(saved) as WizardState;
  } catch {
    // The creation flow still works if web storage is unavailable.
  }
  return { step: 1, values: { ...EMPTY_DRAFT } };
}

function money(value: string | number | null | undefined): string {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return '—';
  return `${new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: 2,
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
  }).format(amount)} ₽`;
}

function localTime(value: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(date);
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

function AppContent() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [adsData, setAdsData] = useState<AdsData | null>(null);
  const [statsData, setStatsData] = useState<StatsData | null>(null);
  const [channelDetail, setChannelDetail] = useState<ChannelDetail | null>(null);
  const [page, setPage] = useState<MiniPage>({ type: 'tabs' });
  const [tab, setTab] = useState<Tab>('channels');
  const [wizard, setWizard] = useState<WizardState>(readWizard);
  const hasPendingWizard = Object.values(wizard.values).some((value) => value.trim().length > 0);
  const [brandName, setBrandName] = useState('');
  const [editValue, setEditValue] = useState('');
  const [topupValue, setTopupValue] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');
  const [authExpired, setAuthExpired] = useState(false);
  const [notice, setNotice] = useState('');
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [nextExhaustedId, setNextExhaustedId] = useState<number | null>(null);
  const busyRef = useRef(false);
  const recoveryRef = useRef<Recovery | null>(null);
  const scrollRef = useRef<Record<string, number>>({});
  const lastPageKey = useRef('tabs');

  const pageKey = useMemo(() => {
    if (page.type === 'tabs') return `tabs:${tab}`;
    if (page.type === 'channel' || page.type === 'ad') return `${page.type}:${page.id}`;
    if (page.type === 'edit' || page.type === 'topup') return `${page.type}:${page.id}`;
    return page.type;
  }, [page, tab]);

  const refresh = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    try {
      const data = await getBootstrap();
      setBootstrap(data);
      setError('');
      setAuthExpired(false);
      if (data.consented) {
        const [adList, stats] = await Promise.all([getAds(), getStats()]);
        setAdsData(adList);
        setStatsData(stats);
      } else {
        setAdsData(null);
        setStatsData(null);
      }
    } catch (cause) {
      const message = cause instanceof ApiError ? cause.message : 'Не удалось загрузить данные.';
      setError(message);
      if (cause instanceof ApiError && cause.status === 401) setAuthExpired(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void refresh(false); }, [refresh]);

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
        document.documentElement.style.setProperty('--max-viewport-height', `${height}px`);
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
    if (page.type === 'create' && hasPendingWizard) window.WebApp?.enableClosingConfirmation?.();
    else window.WebApp?.disableClosingConfirmation?.();
    return () => window.WebApp?.disableClosingConfirmation?.();
  }, [page.type, hasPendingWizard]);

  useEffect(() => {
    if (page.type !== 'channel') return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadChannel(page.id, true);
    }, 20_000);
    return () => window.clearInterval(timer);
  }, [page]);

  useEffect(() => {
    try { sessionStorage.setItem(WIZARD_STORAGE, JSON.stringify(wizard)); } catch { /* optional */ }
  }, [wizard]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(''), 2800);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function loadChannel(id: number, quiet = false) {
    if (!quiet) setRefreshing(true);
    try {
      const detail = await getChannel(id);
      setChannelDetail(detail);
      setBootstrap((current) => current ? {
        ...current,
        channels: current.channels.map((item) => item.chat_id === id ? {
          ...item,
          title: detail.title,
          status: detail.status,
          subscribers: detail.subscribers,
          proposal: detail.proposal,
        } : item),
      } : current);
      setError('');
    } catch (cause) {
      if (!quiet) setError(cause instanceof ApiError ? cause.message : 'Не удалось обновить канал.');
    } finally {
      if (!quiet) setRefreshing(false);
    }
  }

  function openPage(next: MiniPage) {
    scrollRef.current[pageKey] = window.scrollY;
    setError('');
    setFieldErrors({});
    setPage(next);
    if (next.type === 'channel') void loadChannel(next.id);
    if (next.type === 'ad') void getAds().then(setAdsData).catch(() => undefined);
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
    } else if (page.type === 'edit' || page.type === 'topup') {
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
    if (next === 'ads') void getAds().then(setAdsData).catch(() => undefined);
    if (next === 'stats') void getStats().then(setStatsData).catch(() => undefined);
  }

  async function runMutation<T>(
    path: string,
    body: unknown,
    onSuccess: (data: T) => void,
    successText: string,
    method: MutationMethod = 'POST',
    retry = false,
    actionLabel = 'Сохраняем',
  ) {
    if (busyRef.current || (recoveryRef.current && !retry)) return;
    busyRef.current = true;
    setBusy(true);
    setBusyAction(actionLabel);
    setError('');
    setFieldErrors({});
    let committed = false;
    try {
      const data = await mutate<T>(path, body, method);
      committed = true;
      recoveryRef.current = null;
      setRecovery(null);
      await onSuccess(data);
      if (successText) setNotice(successText);
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
      if (apiError.status === 0 || apiError.status >= 500 || apiError.code === 'mutation_in_progress') {
        const retryAction = () => void runMutation<T>(path, body, onSuccess, successText, method, true, actionLabel);
        const recover: Recovery = { message: 'Результат пока не подтверждён. Проверьте это же действие ещё раз.', retry: retryAction };
        recoveryRef.current = recover;
        setRecovery(recover);
      } else {
        setError(apiError.message);
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
      setBusyAction('');
    }
  }

  function dismissRecovery() {
    recoveryRef.current = null;
    setRecovery(null);
  }

  function applyChannel(data: ChannelDetail) {
    setChannelDetail(data);
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

  const activeChannel = page.type === 'channel' && channelDetail?.chat_id === page.id ? channelDetail : null;
  const activeAd = page.type === 'ad' ? adsData?.ads.find((ad) => ad.id === page.id) ?? null : null;
  const companyName = adsData?.advertiser?.name ?? bootstrap?.advertiser?.name ?? '';
  async function acceptConsent() {
    await runMutation<{ accepted: boolean }>(
      '/api/miniapp/consent', { accepted: true },
      () => { void refresh(true); },
      'Условия приняты',
      'POST', false, 'Принимаем',
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
    if (!wizard.values[field].trim()) {
      setFieldErrors({ [field]: 'Заполните это поле, чтобы продолжить.' });
      return;
    }
    if (wizard.step === 7) setWizard((current) => ({ ...current, step: 'preview' }));
    else setWizard((current) => ({ ...current, step: (current.step as number) + 1 }));
  }

  function cancelWizard() {
    if (hasPendingWizard && !window.confirm('Отменить создание объявления и очистить введённые данные?')) return;
    setWizard({ step: 1, values: { ...EMPTY_DRAFT } });
    setFieldErrors({});
    try { sessionStorage.removeItem(WIZARD_STORAGE); } catch { /* optional */ }
    setTab('ads');
    setPage({ type: 'tabs' });
  }

  function createAd() {
    runMutation<Ad>('/api/miniapp/ads', wizard.values, (created) => {
      try { sessionStorage.removeItem(WIZARD_STORAGE); } catch { /* optional */ }
      setWizard({ step: 1, values: { ...EMPTY_DRAFT } });
      setAdsData((current) => current ? {
        ...current,
        ads: [created, ...current.ads.filter((ad) => ad.id !== created.id)],
      } : { advertiser: null, ads: [created] });
      setBootstrap((current) => current ? {
        ...current,
        advertiser: current.advertiser ? { ...current.advertiser, ad_count: current.advertiser.ad_count + 1 } : current.advertiser,
      } : current);
      setPage({ type: 'ad', id: created.id });
      setTab('ads');
    }, 'Объявление запущено и участвует в подборе', 'POST', false, 'Запускаем');
  }

  const recoveryBanner = recovery ? (
    <div className="recovery-banner" role="status">
      <p>{recovery.message}</p>
      <div className="recovery-actions">
        <Button variant="secondary" size="medium" loading={busy} disabled={busy} onClick={recovery.retry}>
          {busy ? 'Проверяем…' : 'Проверить результат'}
        </Button>
        <button className="plain-action" onClick={dismissRecovery} disabled={busy}>Позже</button>
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
            <Button variant="secondary" size="large" stretched onClick={() => window.location.reload()}>
              Проверить подключение
            </Button>
          </div>
        </div>
      </MaxUI>
    );
  }

  if (loading && !bootstrap) {
    return <MaxUI><div className="max-app"><Skeleton /></div></MaxUI>;
  }

  if (authExpired) {
    return (
      <MaxUI>
        <div className="max-app auth-screen">
          <div className="auth-content">
            <h1>Сессия завершилась</h1>
            <p>Данные MAX устарели. Закройте это окно и откройте мини-приложение ещё раз из чата с ботом.</p>
            <Button variant="primary" size="large" stretched onClick={() => window.location.reload()}>
              Обновить
            </Button>
          </div>
        </div>
      </MaxUI>
    );
  }

  if (!bootstrap?.consented) {
    return (
      <MaxUI>
        <div className="max-app consent-screen">
          <header className="page-heading"><h1>Добро пожаловать</h1></header>
          <p className="intro-copy">Перед началом примите условия сервиса.</p>
          <section className="surface consent-copy"><p>{bootstrap?.consent_text}</p></section>
          {error ? <div className="inline-error" role="alert">{error}</div> : null}
          {recoveryBanner}
          <div className="screen-action">
            <Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery)} onClick={() => void acceptConsent()}>
              Принимаю условия
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
              <div className="empty-help"><span>Настройки канала</span><Icon name="chevron" /></div>
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
      if (!advertiser && !bootstrap?.advertiser.exists) {
        return (
          <>
            <PageHeading title="Объявления" subtitle="Кабинет рекламодателя" />
            <section className="surface form-surface">
              <h2>Название компании или бренда</h2>
              <p className="muted-copy">Это название появится в маркировке рекламы.</p>
              <Field label="Компания или бренд" value={brandName} onChange={setBrandName} maxLength={bootstrap?.limits.company_name} error={fieldErrors.name} autoComplete="organization" disabled={busy || Boolean(recovery)} />
              {error ? <div className="inline-error" role="alert">{error}</div> : null}
              <Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery)} onClick={() => {
                if (!brandName.trim()) { setFieldErrors({ name: 'Укажите название компании или бренда.' }); return; }
                runMutation<{ name: string }>('/api/miniapp/advertiser', { name: brandName }, async (data) => {
                  setAdsData({ advertiser: { name: data.name }, ads: [] });
                  setBootstrap((current) => current ? { ...current, advertiser: { exists: true, name: data.name, ad_count: 0 } } : current);
                  setBrandName('');
                  setPage({ type: 'tabs' });
                }, 'Название сохранено', 'POST', false, 'Сохраняем');
              }}>Продолжить</Button>
            </section>
          </>
        );
      }
      return (
        <>
          <PageHeading title="Объявления" subtitle={advertiser?.name ?? bootstrap?.advertiser.name ?? 'Кабинет рекламодателя'} />
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

    return <StatsScreen data={statsData} />;
  };

  const renderChannelPage = () => {
    const id = page.type === 'channel' ? page.id : 0;
    const channel = activeChannel;
    if (!channel) return <Skeleton />;
    const cstate = channelStatus(channel.status);
    const proposal = channel.proposal;
    const latestProposalState = proposal ? proposalStatus(proposal.status) : null;
    const nextExhausted = proposal?.id === nextExhaustedId;
    return (
      <>
        <PageHeader title={channel.title} onBack={goBack} refreshing={refreshing} onRefresh={() => void loadChannel(id)} />
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
            {proposal.status === 'pending' ? (
              <ProposalCard
                proposal={proposal}
                busy={busy}
                busyAction={busyAction}
                recovering={Boolean(recovery)}
                nextDisabled={!proposal.next_available || nextExhausted}
                onAction={(action) => {
                  runMutation<ChannelDetail>(`/api/miniapp/proposals/${proposal.id}/action`, { action }, (data) => {
                    if (data.action_result === 'no_more') setNextExhaustedId(proposal.id);
                    if (data.proposal?.id !== proposal.id) setNextExhaustedId(null);
                    applyChannel(data);
                  }, action === 'approve' ? 'Одобрено. Публикация запланирована.' : action === 'next' ? 'Предложение обновлено.' : action === 'block_cat' ? 'Категория больше не предлагается.' : 'Предложение отклонено.', 'POST', false, action === 'next' ? 'Ищем вариант' : 'Сохраняем');
                }}
              />
            ) : (
              <div className="state-copy">
                {proposal.status === 'approved' ? <p>Реклама пока не опубликована. Плановое время: {localTime(proposal.publish_at)}.</p> : null}
                {proposal.status === 'published' ? <p>Реклама опубликована в канале.</p> : null}
                {proposal.status === 'expired' ? <p>Предложение больше нельзя выполнить. Новые предложения появятся здесь после публикации подходящего поста.</p> : null}
                {proposal.status === 'rejected' ? <p>Это объявление отклонено.</p> : null}
                {proposal.status === 'cancelled' ? <p>Предложение отменено, например если канал отключили.</p> : null}
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
                }, applyChannel, `${category.label}: ${category.allowed ? 'отключена' : 'разрешена'}`, 'POST', false, 'Сохраняем')}
              >
                <span className="category-name">{category.label}{category.regulated ? <span className="regulated-mark" aria-label="Регулируемая категория"> ⚖</span> : null}</span>
                <span className={`switch ${category.allowed ? 'switch-on' : ''}`}><span /></span>
              </button>
            ))}
          </div>
          {busy && busyAction === 'Сохраняем' ? <p className="save-state" role="status">Сохраняем настройку…</p> : null}
          <p className="settings-hint">Регулируемые категории выключены, пока вы их не разрешите.</p>
          <div className="retention-line">
            <strong>Автоудаление рекламы</strong>
            <span>{channel.can_delete ? `Через ${channel.ad_ttl_hours} ч` : 'Недоступно: боту не выдано право удалять сообщения'}</span>
          </div>
          {channel.status === 'active' || channel.status === 'paused' ? (
            <Button
              variant={channel.status === 'paused' ? 'secondary' : 'destructive'}
              size="large"
              stretched
              loading={busyAction.includes('Приостаниваем') || busyAction.includes('Возобновляем')}
              disabled={busy || Boolean(recovery)}
              onClick={() => runMutation<ChannelDetail>(`/api/miniapp/channels/${channel.chat_id}/pause`, {
                paused: channel.status === 'active',
              }, applyChannel, channel.status === 'active' ? 'Канал приостановлен' : 'Работа канала возобновлена', 'POST', false, channel.status === 'active' ? 'Приостаниваем' : 'Возобновляем')}
            >{channel.status === 'active' ? 'Приостановить канал' : 'Возобновить канал'}</Button>
          ) : null}
        </section>
      </>
    );
  };

  const renderAdPage = () => {
    const ad = activeAd;
    if (!ad) return <Skeleton />;
    const status = adStatus(ad.status);
    return (
      <>
        <PageHeader title={ad.title} onBack={goBack} refreshing={refreshing} onRefresh={() => void getAds().then(setAdsData).catch(() => setError('Не удалось обновить объявление.'))} />
        <div className="detail-meta"><Status {...status} /><span>Остаток {money(ad.budget_left)}</span></div>
        <section className="ad-detail-section">
          <div className="editable-row"><div><span className="detail-label">Заголовок</span><h2>{ad.title}</h2></div><Button variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.title); openPage({ type: 'edit', id: ad.id, field: 'title' }); }}>Изменить</Button></div>
          <div className="editable-row align-start"><div><span className="detail-label">Текст</span><p className="ad-body">{ad.body}</p></div><Button variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.body); openPage({ type: 'edit', id: ad.id, field: 'body' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Ссылка</span><button className="text-link" onClick={() => openExternal(ad.url)}>{ad.url}</button></div><Button variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.url); openPage({ type: 'edit', id: ad.id, field: 'url' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Категория</span><strong>{ad.category_label}</strong></div><Button variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.category); openPage({ type: 'edit', id: ad.id, field: 'category' }); }}>Изменить</Button></div>
          <div className="editable-row"><div><span className="detail-label">Модель и цена</span><strong>{ad.pricing_label} · {money(ad.price)}</strong><span className="row-secondary">{ad.pricing_unit}</span></div><Button variant="ghost" size="small" disabled={Boolean(recovery)} onClick={() => { setEditValue(ad.price); openPage({ type: 'edit', id: ad.id, field: 'price' }); }}>Изменить</Button></div>
        </section>
        <section className="ad-budget-section">
          <div className="section-heading"><h2>Бюджет</h2><p>Пополнение показывается как демо и не списывает деньги</p></div>
          <div className="budget-numbers"><strong>{money(ad.budget_left)}</strong><span>из {money(ad.budget_total)}</span></div>
          <div className="budget-track"><span style={{ width: `${Math.min(100, Math.max(0, Number(ad.budget_total) > 0 ? (Number(ad.budget_left) / Number(ad.budget_total)) * 100 : 0))}%` }} /></div>
          <div className="ad-actions-row">
            <Button variant="secondary" size="medium" disabled={busy || Boolean(recovery)} onClick={() => { setTopupValue(''); openPage({ type: 'topup', id: ad.id }); }}>Пополнить демо</Button>
            {ad.status !== 'exhausted' ? <Button variant={ad.status === 'paused' ? 'secondary' : 'destructive'} size="medium" loading={busyAction === 'Приостанавливаем' || busyAction === 'Возобновляем'} disabled={busy || Boolean(recovery)} onClick={() => runMutation<Ad>(`/api/miniapp/ads/${ad.id}/pause`, {
              paused: ad.status === 'active',
            }, (updated) => { applyAd(updated); }, ad.status === 'active' ? 'Объявление приостановлено' : 'Объявление возобновлено', 'POST', false, ad.status === 'active' ? 'Приостанавливаем' : 'Возобновляем')}>{ad.status === 'active' ? 'Приостановить' : 'Возобновить'}</Button> : null}
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
        <p className="footnote">Объявление сразу участвует в подборе. Реальной оплаты и модерации нет; ERID демонстрационный.</p>
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
                  type={current.field === 'url' ? 'url' : 'text'}
                  autoFocus
                  disabled={busy || Boolean(recovery)}
                />
                <p className="field-help">{current.help}</p>
              </div>
            ) : null}
            {step === 4 ? (
              <div className="choice-list" role="group" aria-label="Категории объявлений">
                {bootstrap?.categories.map((category) => (
                  <button className={`choice-row ${wizard.values.category === category.code ? 'choice-selected' : ''}`} key={category.code} aria-pressed={wizard.values.category === category.code} onClick={() => saveWizardField('category', category.code)}>
                    <span>{category.label}{category.regulated ? <span className="regulated-mark"> ⚖</span> : null}</span>
                    <span className="radio-mark" aria-hidden="true" />
                  </button>
                ))}
                {fieldErrors.category ? <p className="field-error" role="alert">{fieldErrors.category}</p> : null}
              </div>
            ) : null}
            {step === 5 ? (
              <div className="choice-list" role="radiogroup" aria-label="Модель оплаты">
                {bootstrap?.pricing_models.map((model) => (
                  <button className={`choice-row ${wizard.values.pricing_model === model.code ? 'choice-selected' : ''}`} key={model.code} role="radio" aria-checked={wizard.values.pricing_model === model.code} onClick={() => saveWizardField('pricing_model', model.code)}>
                    <span><strong>{model.label}</strong><small>{model.unit}</small></span><span className="radio-mark" aria-hidden="true" />
                  </button>
                ))}
                {fieldErrors.pricing_model ? <p className="field-error" role="alert">{fieldErrors.pricing_model}</p> : null}
              </div>
            ) : null}
            <div className="wizard-actions">
              <Button variant="secondary" size="large" onClick={goBack}>Назад</Button>
              <Button variant="primary" size="large" loading={busy} disabled={busy || Boolean(recovery)} onClick={wizardNext}>Продолжить</Button>
            </div>
            <button className="cancel-flow" onClick={cancelWizard} disabled={busy}>Отменить создание</button>
          </>
        ) : (
          <>
            <div className="preview-post">
              <div className="preview-brand">{company || 'Рекламодатель'}</div>
              <h2>{wizard.values.title}</h2>
              <p className="preview-body">{wizard.values.body}</p>
              <p className="preview-marking">#Реклама. {company}. erid: присвоим при запуске</p>
              <button className="preview-link" onClick={() => openExternal(wizard.values.url)}>{wizard.values.url}</button>
            </div>
            <div className="preview-details">
              <div className="detail-pair"><span>Категория</span><strong>{categoryLabel}</strong></div>
              <div className="detail-pair"><span>Модель оплаты</span><strong>{priceModel?.label} · {money(wizard.values.price)} {priceModel?.unit}</strong></div>
              <div className="detail-pair"><span>Бюджет</span><strong>{money(wizard.values.budget)}</strong></div>
            </div>
            <div className="demo-note">Пополнение бюджета демонстрационное. Реальной оплаты и модерации нет; ERID демонстрационный.</div>
            {error ? <div className="inline-error" role="alert">{error}</div> : null}
            {Object.entries(fieldErrors).map(([key, message]) => message ? <p className="field-error" role="alert" key={key}>{message}</p> : null)}
            <div className="wizard-actions preview-actions">
              <Button variant="secondary" size="large" onClick={goBack} disabled={busy}>Назад</Button>
              <Button variant="primary" size="large" loading={busy} disabled={busy || Boolean(recovery)} onClick={createAd}>Запустить объявление</Button>
            </div>
            <button className="cancel-flow" onClick={cancelWizard} disabled={busy}>Отменить создание</button>
          </>
        )}
      </>
    );
  };

  const renderEdit = () => {
    if (page.type !== 'edit') return null;
    const ad = adsData?.ads.find((item) => item.id === page.id);
    if (!ad) return <Skeleton />;
    const labels = { title: 'Заголовок', body: 'Текст объявления', url: 'Ссылка', price: 'Цена', category: 'Категория' };
    const categoryField = page.field === 'category';
    return (
      <>
        <PageHeader title={labels[page.field]} onBack={goBack} />
        {categoryField ? (
          <div className="choice-list" role="radiogroup" aria-label="Новая категория">
            {bootstrap?.categories.map((category) => (
              <button key={category.code} className={`choice-row ${editValue === category.code ? 'choice-selected' : ''}`} role="radio" aria-checked={editValue === category.code} onClick={() => setEditValue(category.code)}><span>{category.label}{category.regulated ? <span className="regulated-mark"> ⚖</span> : null}</span><span className="radio-mark" /></button>
            ))}
          </div>
        ) : (
          <Field label={labels[page.field]} value={editValue} onChange={setEditValue} error={fieldErrors[page.field]} maxLength={page.field === 'title' ? bootstrap?.limits.title : page.field === 'body' ? bootstrap?.limits.body : page.field === 'url' ? bootstrap?.limits.url : undefined} multiline={page.field === 'body'} inputMode={page.field === 'url' ? 'url' : page.field === 'price' ? 'decimal' : 'text'} type={page.field === 'url' ? 'url' : 'text'} autoFocus disabled={busy || Boolean(recovery)} />
        )}
        {error ? <div className="inline-error" role="alert">{error}</div> : null}
        <div className="screen-action"><Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery) || !editValue.trim()} onClick={() => runMutation<Ad>(`/api/miniapp/ads/${ad.id}/field`, {
          field: page.field, value: editValue,
        }, (updated) => { applyAd(updated); setPage({ type: 'ad', id: ad.id }); }, 'Изменения сохранены', 'PUT', false, 'Сохраняем')}>Сохранить</Button></div>
      </>
    );
  };

  const renderTopup = () => {
    if (page.type !== 'topup') return null;
    const ad = adsData?.ads.find((item) => item.id === page.id);
    return (
      <>
        <PageHeader title="Пополнение бюджета" onBack={goBack} />
        <section className="surface form-surface">
          <h2>{ad?.title}</h2>
          <div className="demo-note">Демонстрационное пополнение: сумма изменится в кабинете, реальные деньги не списываются.</div>
          <Field label="Сумма, ₽" value={topupValue} onChange={setTopupValue} error={fieldErrors.amount} inputMode="decimal" placeholder="Например, 500" autoFocus disabled={busy || Boolean(recovery)} />
          {error ? <div className="inline-error" role="alert">{error}</div> : null}
          <Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery) || !topupValue.trim()} onClick={() => runMutation<Ad>(`/api/miniapp/ads/${page.id}/topup`, { amount: topupValue }, (updated) => {
            applyAd(updated);
            setPage({ type: 'ad', id: updated.id });
          }, `Бюджет пополнен на ${money(topupValue)} (демо)`, 'POST', false, 'Пополняем')}>Пополнить демо</Button>
        </section>
      </>
    );
  };

  const renderBrandPage = () => (
    <>
      <PageHeader title="Кабинет рекламодателя" onBack={goBack} />
      <section className="surface form-surface">
        <h2>Название компании или бренда</h2>
        <p className="muted-copy">Это название появится в маркировке рекламы.</p>
        <Field label="Компания или бренд" value={brandName} onChange={setBrandName} maxLength={bootstrap?.limits.company_name} error={fieldErrors.name} autoComplete="organization" disabled={busy || Boolean(recovery)} />
        {error ? <div className="inline-error" role="alert">{error}</div> : null}
        <Button variant="primary" size="large" stretched loading={busy} disabled={busy || Boolean(recovery) || !brandName.trim()} onClick={() => runMutation<{ name: string }>('/api/miniapp/advertiser', { name: brandName }, async (data) => {
          setAdsData({ advertiser: { name: data.name }, ads: [] });
          setBootstrap((current) => current ? { ...current, advertiser: { exists: true, name: data.name, ad_count: 0 } } : current);
          setBrandName('');
          setPage({ type: 'tabs' });
          setTab('ads');
        }, 'Название сохранено', 'POST', false, 'Сохраняем')}>Продолжить</Button>
      </section>
    </>
  );

  return (
    <MaxUI>
      <div className="max-app">
        {error && page.type !== 'create' && page.type !== 'edit' && page.type !== 'topup' && page.type !== 'brand' ? <div className="global-error" role="alert">{error}<button onClick={() => void refresh(true)}>Обновить</button></div> : null}
        {recoveryBanner}
        {page.type === 'tabs' ? <main className="page-content">{renderTabs()}</main> : (
          <main className="page-content subpage">
            {page.type === 'channel' ? renderChannelPage() : null}
            {page.type === 'ad' ? renderAdPage() : null}
            {page.type === 'create' ? renderCreate() : null}
            {page.type === 'edit' ? renderEdit() : null}
            {page.type === 'topup' ? renderTopup() : null}
            {page.type === 'brand' ? renderBrandPage() : null}
          </main>
        )}
        {page.type === 'tabs' ? <TabBar active={tab} onChange={changeTab} /> : null}
        {notice ? <div className="toast" role="status">{notice}</div> : null}
      </div>
    </MaxUI>
  );

}

function PageHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return <header className="page-heading"><h1>{title}</h1>{subtitle ? <p>{subtitle}</p> : null}</header>;
}

function PageHeader({ title, onBack, refreshing, onRefresh }: { title: string; onBack: () => void; refreshing?: boolean; onRefresh?: () => void }) {
  return (
    <header className="subpage-heading">
      <button className="back-control" onClick={onBack} aria-label="Назад"><Icon name="back" /><span>Назад</span></button>
      <h1>{title}</h1>
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

function ProposalCard({
  proposal,
  busy,
  busyAction,
  recovering,
  nextDisabled,
  onAction,
}: {
  proposal: NonNullable<ChannelDetail['proposal']>;
  busy: boolean;
  busyAction: string;
  recovering: boolean;
  nextDisabled: boolean;
  onAction: (action: 'approve' | 'reject' | 'next' | 'block_cat') => void;
}) {
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
          <Button variant="primary" size="large" stretched loading={busy && busyAction === 'Сохраняем'} disabled={busy || recovering} onClick={() => onAction('approve')}>Одобрить</Button>
          <Button variant="secondary" size="large" stretched loading={busy && busyAction === 'Ищем вариант'} disabled={busy || recovering || nextDisabled} onClick={() => onAction('next')}>Другой вариант</Button>
        </div>
        <Button variant="destructive" size="large" stretched disabled={busy || recovering} onClick={() => onAction('reject')}>Отклонить это объявление</Button>
        <div className="category-block-action">
          <span>Больше не показывать рекламу из категории «{proposal.category_label}» в этом канале</span>
          <Button variant="destructive" size="large" stretched disabled={busy || recovering} onClick={() => onAction('block_cat')}>Не предлагать категорию</Button>
        </div>
      </div>
    </div>
  );
}

function StatsScreen({ data }: { data: StatsData | null }) {
  if (!data || (data.channels.length === 0 && !data.advertiser)) {
    return (
      <>
        <PageHeading title="Статистика" subtitle="Данные обновляются после сбора просмотров и кликов" />
        <section className="empty-state compact-empty"><h2>Пока нет статистики</h2><p>Здесь появятся показатели каналов и ваших объявлений, когда начнутся размещения.</p></section>
      </>
    );
  }
  return (
    <>
      <PageHeading title="Статистика" subtitle="Доход канала и расход рекламодателя показаны отдельно" />
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
  if (window.WebApp?.openLink) window.WebApp.openLink(url);
  else window.open(url, '_blank', 'noopener,noreferrer');
}

export default function App() {
  return <AppContent />;
}
