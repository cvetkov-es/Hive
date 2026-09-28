// Правая панель — единственное место, которое меняет содержимое: сводка
// смены, объяснение заявки, маршрут бригады, событие, «Что изменилось».
// Всё остальное стоит на месте, чтобы диспетчер узнавал экран, а не изучал
// его заново после каждого действия.
//
// Заголовки «Объяснение», «Маршрут бригады», «Что изменилось» — те же, что
// в README и сценарии показа: на них ссылаются.
//
// Кнопки «Все проверки» и «Переназначить» стоят в подвале и видны всегда: на
// ноутбуке 1366×768 они уходили бы за срез прокрутки, и шаг 4 сценария ТЗ —
// «объясните, почему назначена» — становился бы недоступен.
//
// Незакрытые заявки — «в явном виде» (ТЗ 2.2): список в «Сводке смены», а
// открытая незакрытая заявка несёт навигатор «‹ 3 из 34 ›» — разбирать отказы
// можно подряд, не возвращаясь к списку за каждой.

import type { Controller } from '../controller';
import { backTarget, inspectorView, neighbours, type InspectorView } from '../lib/inspector.ts';
import type { Board } from '../store';
import { ErrorBoundary } from './ErrorBoundary';
import { DaySummary } from './DaySummary';
import { DiffPanel, EventForm } from './EventPanels';
import { ExplainPanel } from './ExplainPanel';
import { RoutePanel } from './RoutePanel';

const TITLE: Record<InspectorView, string> = {
  event: 'Событие в течение дня',
  engineer: 'Маршрут бригады',
  job: 'Объяснение',
  diff: 'Что изменилось',
  summary: 'Сводка смены',
};

export function Inspector({ b, c, showReserve, fleetSize }: {
  b: Board; c: Controller; showReserve: boolean; fleetSize: number;
}) {
  const { s, explanation } = b;
  const sel = s.selection;
  const state = { eventOpen: c.eventOpen, selection: sel.kind, hasDiff: !!s.diff,
                  summaryTab: c.summaryTab };
  const view = inspectorView(state);
  const selectedJob = sel.kind === 'job' ? sel.jobId : null;
  const unassignedIds = (s.plan?.unassigned ?? []).map(u => u.job_id);
  const nav = view === 'job' && selectedJob ? neighbours(unassignedIds, selectedJob) : null;

  // Ключ того, что показывает панель: сменился — граница ошибок снимает
  // прежнюю ошибку сама.
  const selKey = `${s.plan?.plan_id ?? ''}:${view}:${sel.kind === 'job' ? sel.jobId
    : sel.kind === 'engineer' ? sel.engineerId : ''}`;

  const footer = view === 'job' && explanation
    && explanation.job_id === selectedJob && explanation.status !== 'отменена';

  return (
    <div className="inspector">
      <div className="insp-head">
        <span className="insp-title">{TITLE[view]}</span>
        {view === 'event' && <button className="linkish" onClick={c.closeEvent}>Закрыть</button>}
        {(view === 'job' || view === 'engineer') && (
          <button className="linkish" onClick={c.clearSelection}>← {backTarget(state)}</button>
        )}
      </div>

      {(view === 'diff' || view === 'summary') && s.diff && (
        <div className="insp-tabs seg seg-fill" role="tablist" aria-label="Правая панель">
          <button role="tab" aria-selected={c.summaryTab === 'diff'}
                  className={c.summaryTab === 'diff' ? 'on' : ''}
                  onClick={() => c.setSummaryTab('diff')}>Что изменилось</button>
          <button role="tab" aria-selected={c.summaryTab === 'summary'}
                  className={c.summaryTab === 'summary' ? 'on' : ''}
                  onClick={() => c.setSummaryTab('summary')}>Сводка смены</button>
        </div>
      )}

      {nav && (
        <div className="un-nav" role="navigation" aria-label="Незакрытые заявки">
          <button disabled={!nav.prev} aria-label="Предыдущая незакрытая"
                  onClick={() => nav.prev && c.pickJob(nav.prev)}>‹</button>
          <span>Незакрытая <b>{nav.index}</b> из {nav.total}</span>
          <button disabled={!nav.next} aria-label="Следующая незакрытая"
                  onClick={() => nav.next && c.pickJob(nav.next)}>›</button>
          <button className="linkish" onClick={c.showSummary}>весь список</button>
        </div>
      )}

      {c.flash && (
        <div className="flash" role="status">
          <span>{c.flash}</span>
          <button className="linkish" onClick={c.clearFlash}>Скрыть</button>
        </div>
      )}

      <div className="insp-body">
        <ErrorBoundary label="Панель" resetKey={selKey} onReset={c.clearSelection}>
          {s.error && <div className="explain"><div className="un-reason">{s.error}</div></div>}
          {/* Черновик события живёт и спрятанным: ушли к заявке на карте —
              вернулись к недописанной форме. */}
          {c.eventDraft && s.plan && (
            <div hidden={view !== 'event'}>
              <EventForm plan={s.plan} busy={!!s.busy} onClose={c.closeEvent}
                         onApply={c.applyEvent} registerPick={c.registerEventPick} />
            </div>
          )}
          {view === 'engineer' && sel.kind === 'engineer' && (
            <RoutePanel route={b.routeExplanation} mark={s.marks[sel.engineerId]}
                        error={b.explainError} onPickJob={c.pickJob} />
          )}
          {view === 'job' && (
            <ExplainPanel explanation={explanation} error={b.explainError} loading
                          marks={s.marks} busy={!!s.busy} onPickEngineer={id => c.pickEngineer(id)} />
          )}
          {view === 'diff' && s.diff && (
            <DiffPanel diff={s.diff} events={s.plan?.meta?.events ?? []} geocode={s.geocode}
                       busy={!!s.busy} onBack={() => b.loadPlan(s.region, { keep: s.keep })}
                       onPickJob={c.pickJob} />
          )}
          {view === 'summary' && (
            <DaySummary plan={s.plan} marks={s.marks} keep={s.keep} fleetSize={fleetSize}
                        busy={!!s.busy} showReserve={showReserve}
                        delay={c.delay} risk={c.risk} onDelay={c.setDelay}
                        onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)}
                        onKeep={keep => b.loadPlan(s.region, { keep })} />
          )}
        </ErrorBoundary>
      </div>

      {footer && (
        <div className="insp-foot">
          {explanation.status === 'назначена' && (
            <button onClick={() => c.setOverlay({
              kind: 'checks', jobId: explanation.job_id, rows: explanation.checks })}>
              Все проверки
            </button>
          )}
          <button onClick={() => c.setOverlay({ kind: 'move', jobId: explanation.job_id })}>
            {explanation.status === 'назначена' ? 'Переназначить' : 'Назначить вручную'}
          </button>
        </div>
      )}
    </div>
  );
}
