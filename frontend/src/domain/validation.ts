import type { Bootstrap } from '../types';

const MONEY_MAX = 100_000_000;

/** Mirrors cabinet.parse_money input conventions and range. */
export function parseMoney(value: string): number | null {
  const cleaned = value.trim()
    .replace(/[\s\u00a0\u202f]/g, '')
    .replace(/(?:₽|руб|РУБ|\.)+$/u, '')
    .replace(',', '.');
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(cleaned)) return null;
  const [integer = '0', fraction = ''] = cleaned.split('.');
  if (fraction.length > 2 && /[^0]/u.test(fraction.slice(2))) return null;
  const amount = Number(`${integer || '0'}.${fraction.slice(0, 2).padEnd(2, '0')}`);
  return Number.isFinite(amount) && amount >= 0.01 && amount <= MONEY_MAX ? amount : null;
}

export function normalizedMoney(value: string): string | null {
  const amount = parseMoney(value);
  return amount === null ? null : amount.toFixed(2);
}

export function formatMoney(value: string | number | null | undefined): string {
  if (value == null || value === '') return '—';
  const normalized = typeof value === 'number'
    ? value
    : Number(value.trim().replace(/[\s\u00a0\u202f]/g, '').replace(/(?:₽|руб|РУБ)+$/u, '').replace(',', '.'));
  if (!Number.isFinite(normalized)) return '—';
  return `${new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: 2,
    minimumFractionDigits: Number.isInteger(normalized) ? 0 : 2,
  }).format(normalized)} ₽`;
}

export type DraftValues = {
  title: string;
  body: string;
  url: string;
  category: string;
  pricing_model: string;
  price: string;
  budget: string;
};

export type DraftField = keyof DraftValues;

export function validateDraftField(
  field: DraftField,
  values: DraftValues,
  bootstrap: Bootstrap,
): string | null {
  const value = values[field].trim();
  if (!value) return 'Заполните это поле, чтобы продолжить.';

  switch (field) {
    case 'title':
      return value.length <= bootstrap.limits.title ? null : `Не больше ${bootstrap.limits.title} символов.`;
    case 'body':
      return value.length <= bootstrap.limits.body ? null : `Не больше ${bootstrap.limits.body} символов.`;
    case 'url': {
      if (value.length > bootstrap.limits.url) return `Не больше ${bootstrap.limits.url} символов.`;
      try {
        const parsed = new URL(value);
        return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.hostname && !/\s/u.test(value)
          ? null
          : 'Введите полную ссылку, начинающуюся с http:// или https://.';
      } catch {
        return 'Введите полную ссылку, начинающуюся с http:// или https://.';
      }
    }
    case 'category':
      return bootstrap.categories.some((item) => item.code === value) ? null : 'Выберите категорию.';
    case 'pricing_model':
      return bootstrap.pricing_models.some((item) => item.code === value) ? null : 'Выберите модель оплаты.';
    case 'price':
    case 'budget':
      if (parseMoney(value) === null) return 'Укажите сумму от 0,01 до 100 000 000 ₽.';
      if (field === 'budget' && parseMoney(values.price) !== null && parseMoney(value)! < parseMoney(values.price)!) {
        return `Бюджет не может быть меньше цены (${formatMoney(values.price)}).`;
      }
      return null;
  }
}

export function validateAdEditField(
  field: 'title' | 'body' | 'url' | 'category' | 'price',
  value: string,
  bootstrap: Bootstrap,
): string | null {
  return validateDraftField(field, {
    title: field === 'title' ? value : 'x',
    body: field === 'body' ? value : 'x',
    url: field === 'url' ? value : 'https://example.com',
    category: field === 'category' ? value : bootstrap.categories[0]?.code ?? '',
    pricing_model: bootstrap.pricing_models[0]?.code ?? '',
    price: field === 'price' ? value : '0.01',
    budget: '0.01',
  }, bootstrap);
}
