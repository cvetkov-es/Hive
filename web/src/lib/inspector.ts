// Что показывает правая панель и куда ведёт её «назад».
//
// Отдельно от компонента, чтобы правила проверялись тестами: две ошибки этого
// места видно только кликами — «назад» подписан одним, а ведёт в другое, и
// список незакрытых заявок пропадает, как только выбрана одна из них.

export type InspectorState = {
  eventOpen: boolean;
  selection: 'none' | 'job' | 'engineer';
  hasDiff: boolean;
  summaryTab: 'diff' | 'summary';
};

export type InspectorView = 'event' | 'engineer' | 'job' | 'diff' | 'summary';

export function inspectorView(s: InspectorState): InspectorView {
  if (s.eventOpen) return 'event';
  if (s.selection !== 'none') return s.selection;
  return s.hasDiff && s.summaryTab === 'diff' ? 'diff' : 'summary';
}

/** Куда вернёт «назад» из заявки или бригады: туда, где был диспетчер. */
export function backTarget(s: InspectorState): 'Что изменилось' | 'Сводка смены' {
  return s.hasDiff && s.summaryTab === 'diff' ? 'Что изменилось' : 'Сводка смены';
}

/** Место заявки в списке незакрытых: «3 из 34» и соседи для перехода. */
export function neighbours(ids: string[], current: string):
  { index: number; total: number; prev: string | null; next: string | null } | null {
  const i = ids.indexOf(current);
  if (i < 0) return null;
  return { index: i + 1, total: ids.length, prev: ids[i - 1] ?? null, next: ids[i + 1] ?? null };
}
