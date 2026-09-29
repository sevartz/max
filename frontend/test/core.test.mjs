import test from 'node:test';
import assert from 'node:assert/strict';
import { proposalActionMessage, proposalIsActionable, proposalStateText } from '../src/domain/proposals.ts';
import { parseWizardSnapshot, wizardStorageKey, WIZARD_VERSION } from '../src/domain/draft.ts';
import { formatMoney, normalizedMoney, parseMoney, validateAdEditField, validateDraftField } from '../src/domain/validation.ts';
import { RequestCoordinator } from '../src/domain/request-coordinator.ts';
import { hashIdentityScope, IdempotencyLedger, idempotencyStorageKey, isUncertainWrite, parsePendingWrite } from '../src/domain/idempotency.ts';

const bootstrap = {
  consented: true,
  channels: [],
  advertiser: { exists: false, name: null, ad_count: 0 },
  categories: [{ code: 'food', label: 'Еда', regulated: false }],
  pricing_models: [{ code: 'cpm', label: 'CPM', unit: 'за 1000 показов' }],
  limits: { company_name: 100, title: 100, body: 1000, url: 2048 },
};

const draft = {
  title: 'Тест',
  body: 'Текст объявления',
  url: 'https://example.com/path',
  category: 'food',
  pricing_model: 'cpm',
  price: '250',
  budget: '1 500,50 ₽',
};

const signedLaunch = (id, authDate) => new URLSearchParams({
  user: JSON.stringify({ id, first_name: 'Test' }),
  auth_date: String(authDate),
  hash: `signed-${id}-${authDate}`,
}).toString();

test('proposal outcomes only describe confirmed server results', () => {
  const expected = {
    approved: 'Одобрено. Публикация запланирована, но ещё не состоялась.',
    rejected: 'Объявление отклонено.',
    category_blocked: 'Категория больше не предлагается в этом канале.',
    next: 'Показан другой вариант.',
    no_more: 'Подходящих альтернатив больше нет. Можно решить, что делать с текущим предложением.',
    stale: 'Предложение уже изменилось или было обработано. Карточка обновлена.',
    expired: 'Срок предложения истёк. Карточка обновлена.',
  };
  for (const [result, message] of Object.entries(expected)) assert.equal(proposalActionMessage(result), message);
  assert.match(proposalActionMessage(undefined), /состояние предложения обновлено/iu);
});

test('proposal actions require server actionability, pending status, and a future expiry', () => {
  const proposal = { actionable: true, status: 'pending', expires_at: '2030-01-01T00:00:00Z' };
  assert.equal(proposalIsActionable(proposal, Date.parse('2029-12-31T23:59:59Z')), true);
  assert.equal(proposalIsActionable(proposal, Date.parse('2030-01-01T00:00:00Z')), false);
  assert.equal(proposalIsActionable({ ...proposal, actionable: false }, 0), false);
  assert.equal(proposalIsActionable({ ...proposal, status: 'approved' }, 0), false);
  assert.equal(proposalIsActionable({ ...proposal, expires_at: 'bad' }, 0), false);
  assert.match(proposalStateText({ ...proposal, actionable: false }, 0), /сейчас недоступно/iu);
  assert.match(proposalStateText({ ...proposal, expires_at: '2000-01-01T00:00:00Z' }, Date.now()), /Срок предложения истёк/iu);
});

test('money parsing and draft validation match cabinet entry rules', () => {
  assert.equal(parseMoney('1 500,50 ₽'), 1500.5);
  assert.equal(parseMoney('250 руб.'), 250);
  assert.equal(parseMoney('1,2300'), 1.23);
  assert.equal(normalizedMoney('1 500,5'), '1500.50');
  assert.equal(formatMoney('1 500,5'), '1 500,50 ₽');
  assert.equal(formatMoney('500 ₽'), '500 ₽');
  assert.equal(formatMoney(null), '—');
  assert.equal(parseMoney('0'), null);
  assert.equal(parseMoney('-1'), null);
  assert.equal(parseMoney('1.234'), null);
  assert.equal(parseMoney('100000000.01'), null);
  assert.equal(validateDraftField('url', { ...draft, url: 'javascript:alert(1)' }, bootstrap), 'Введите полную ссылку, начинающуюся с http:// или https://.');
  assert.equal(validateDraftField('category', { ...draft, category: 'unknown' }, bootstrap), 'Выберите категорию.');
  assert.equal(validateAdEditField('category', 'unknown', bootstrap), 'Выберите категорию.');
  assert.match(validateDraftField('budget', { ...draft, budget: '100' }, bootstrap), /не может быть меньше цены/iu);
  assert.equal(validateDraftField('budget', draft, bootstrap), null);
});

test('wizard draft data is versioned, validated, and isolated by signed MAX identity', () => {
  const envelope = { version: WIZARD_VERSION, wizard: { step: 'preview', values: draft } };
  assert.deepEqual(parseWizardSnapshot(JSON.stringify(envelope)), envelope.wizard);
  assert.equal(parseWizardSnapshot(JSON.stringify({ ...envelope, version: 1 })), null);
  assert.equal(parseWizardSnapshot(JSON.stringify({ ...envelope, wizard: { step: 9, values: draft } })), null);
  assert.equal(parseWizardSnapshot('not-json'), null);
  assert.notEqual(wizardStorageKey('signed-data-user-a'), wizardStorageKey('signed-data-user-b'));
  assert.equal(wizardStorageKey(signedLaunch(101, 1_700_000_000)), wizardStorageKey(signedLaunch(101, 1_700_000_500)));
  assert.notEqual(wizardStorageKey(signedLaunch(101, 1_700_000_000)), wizardStorageKey(signedLaunch(202, 1_700_000_000)));
});

test('same-key reads coalesce and invalidated late responses cannot commit', async () => {
  const coordinator = new RequestCoordinator();
  let readCount = 0;
  let resolveOld;
  let oldSignal;
  const commits = [];
  const first = coordinator.run('channel:1', (signal) => {
    readCount += 1;
    oldSignal = signal;
    return new Promise((resolve) => { resolveOld = resolve; });
  }, { onSuccess: (value) => commits.push(value), onError: () => assert.fail('old request should not report an invalidated error') });
  const duplicate = coordinator.run('channel:1', async () => {
    readCount += 1;
    return 'duplicate';
  }, { onSuccess: (value) => commits.push(value), onError: () => {} });
  assert.equal(first, duplicate);
  await Promise.resolve();
  coordinator.invalidate('channel:1');
  assert.equal(oldSignal.aborted, true);
  const second = coordinator.run('channel:1', async () => {
    readCount += 1;
    return 'newer';
  }, { onSuccess: (value) => commits.push(value), onError: () => {} });
  resolveOld('older');
  await Promise.all([first, second]);
  assert.equal(readCount, 2);
  assert.deepEqual(commits, ['newer']);
});

test('uncertain writes keep the same idempotency key until explicitly cleared', () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  const ledger = new IdempotencyLedger(storage);
  let generated = 0;
  const create = () => `key-${++generated}`;
  const key = ledger.getOrCreate('write:proposal:7', create);
  assert.equal(isUncertainWrite({ status: 0 }), true);
  assert.equal(isUncertainWrite({ status: 401 }), true, 'auth rejection during a retry does not settle the earlier attempt');
  assert.equal(isUncertainWrite({ status: 502 }), true);
  assert.equal(isUncertainWrite({ status: 409, code: 'mutation_in_progress' }), true);
  assert.equal(isUncertainWrite({ status: 409, code: 'stale' }), false);
  assert.equal(ledger.getOrCreate('write:proposal:7', create), key);
  assert.equal(generated, 1);
  ledger.clear('write:proposal:7');
  assert.notEqual(ledger.getOrCreate('write:proposal:7', create), key);
});

test('idempotency keys are scoped to a one-way signed-user identity hash', async () => {
  const body = { action: 'approve' };
  const firstLaunch = signedLaunch(101, 1_700_000_000);
  const refreshedLaunch = signedLaunch(101, 1_700_000_500);
  const otherUser = signedLaunch(202, 1_700_000_000);
  const firstKey = await idempotencyStorageKey(firstLaunch, '/api/miniapp/proposals/8/action', body);
  const retryKey = await idempotencyStorageKey(refreshedLaunch, '/api/miniapp/proposals/8/action', body);
  const otherKey = await idempotencyStorageKey(otherUser, '/api/miniapp/proposals/8/action', body);
  assert.equal(firstKey, retryKey, 'refreshed launch data for the same user retains the retry key');
  assert.notEqual(firstKey, otherKey, 'different MAX users receive separate operation keys');
  assert.equal(firstKey.includes(firstLaunch), false, 'the operation key does not persist signed initData');
  assert.equal(firstKey.includes('101'), false, 'the operation key contains no plain user id');
  assert.notEqual(await hashIdentityScope(firstLaunch), await hashIdentityScope(otherUser));
});

test('pending write restoration validates account scope and bounded same-origin API paths', () => {
  const write = { scopeHash: 'scope-a', path: '/api/miniapp/ads', method: 'POST', body: { title: 'Тест' } };
  assert.deepEqual(parsePendingWrite(JSON.stringify({ version: 1, ...write }), 'scope-a'), write);
  assert.equal(parsePendingWrite(JSON.stringify({ version: 1, ...write }), 'scope-b'), null);
  assert.equal(parsePendingWrite(JSON.stringify({ version: 1, ...write, path: '//evil.example' }), 'scope-a'), null);
  assert.equal(parsePendingWrite(JSON.stringify({ version: 1, ...write, body: 'text' }), 'scope-a'), null);
});
