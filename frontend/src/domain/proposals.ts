import type { Proposal } from '../types';

export type ProposalActionResult =
  | 'approved'
  | 'rejected'
  | 'category_blocked'
  | 'next'
  | 'no_more'
  | 'stale'
  | 'expired';

export function proposalActionMessage(result: string | undefined): string {
  switch (result) {
    case 'approved': return 'Одобрено. Публикация запланирована, но ещё не состоялась.';
    case 'rejected': return 'Объявление отклонено.';
    case 'category_blocked': return 'Категория больше не предлагается в этом канале.';
    case 'next': return 'Показан другой вариант.';
    case 'no_more': return 'Подходящих альтернатив больше нет. Можно решить, что делать с текущим предложением.';
    case 'stale': return 'Предложение уже изменилось или было обработано. Карточка обновлена.';
    case 'expired': return 'Срок предложения истёк. Карточка обновлена.';
    default: return 'Ответ сервиса получен. Состояние предложения обновлено.';
  }
}

export function proposalIsActionable(proposal: Proposal, now = Date.now()): boolean {
  const expiry = Date.parse(proposal.expires_at);
  return proposal.actionable && proposal.status === 'pending' && Number.isFinite(expiry) && expiry > now;
}

export function proposalStateText(proposal: Proposal, now = Date.now()): string {
  if (proposal.status === 'pending' && !proposalIsActionable(proposal, now)) {
    const expiry = Date.parse(proposal.expires_at);
    return Number.isFinite(expiry) && expiry <= now
      ? 'Срок предложения истёк. Обновите экран, чтобы проверить, появились ли новые предложения.'
      : 'Предложение сейчас недоступно. Обновите экран, чтобы проверить его состояние.';
  }
  switch (proposal.status) {
    case 'approved': return proposal.publish_at
      ? `Одобрено. Публикация ожидается ${proposal.publish_at}.`
      : 'Одобрено. Публикация ожидает выполнения.';
    case 'published': return 'Реклама опубликована в канале.';
    case 'rejected': return 'Это объявление отклонено.';
    case 'expired': return 'Предложение больше нельзя выполнить. Новые предложения появятся после публикации подходящего поста.';
    case 'cancelled': return 'Предложение отменено, например если канал отключили.';
    case 'superseded': return 'Это предложение заменено другим вариантом. Откройте актуальную карточку, чтобы принять решение.';
    default: return 'Предложение больше нельзя выполнить. Обновите экран, чтобы проверить его состояние.';
  }
}
