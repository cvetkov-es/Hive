// Части, одинаковые в обеих раскладках: аналитические экраны, окна поверх
// пульта и общие свойства карты и ленты.
//
// На аналитических вкладках нет ни карты, ни боковых колонок: список бригад и
// объяснение относятся к плану дня, а сравнению, кривой и цене правил нужна
// ширина.

import type { Controller } from '../controller';
import type { Board } from '../store';
import { ErrorBoundary } from '../panels/ErrorBoundary';
import { AuditModal, BusyOverlay, ChecksModal, EstimatedModal, HealthModal,
         MoveModal } from '../panels/Modals';
import { CompareScreen, CurveScreen, SensitivityScreen } from '../panels/Screens';

export function Analytics({ b }: { b: Board }) {
  const { s } = b;
  const region = s.regions.find(r => r.name === s.region);
  return (
    <ErrorBoundary label="Экран" resetKey={`${s.screen}:${s.region}`}>
      {s.screen === 'compare' && (
        <CompareScreen region={s.region} keep={s.keep} fleetSize={region?.engineers ?? 0}
                       events={((s.plan?.meta as { events?: string[] } | undefined)?.events) ?? []} />
      )}
      {s.screen === 'curve' && <CurveScreen region={s.region} live={region?.live_day} />}
      {s.screen === 'sensitivity' && <SensitivityScreen region={s.region} />}
    </ErrorBoundary>
  );
}

export function Overlays({ b, c }: { b: Board; c: Controller }) {
  const { s, explanation } = b;
  const o = c.overlay;
  return (
    <>
      {o.kind === 'audit' && s.audit && <AuditModal report={s.audit} onClose={c.close} />}
      {o.kind === 'health' && s.health && <HealthModal health={s.health} onClose={c.close} />}
      {o.kind === 'estimated' && s.plan && (
        <EstimatedModal plan={s.plan} onClose={c.close} onPickJob={c.pickJob} />
      )}
      {o.kind === 'checks' && <ChecksModal jobId={o.jobId} rows={o.rows} onClose={c.close} />}
      {o.kind === 'move' && s.plan && (
        <MoveModal
          planId={s.plan.plan_id}
          jobId={o.jobId}
          currentEngineer={b.jobOwner[o.jobId]?.engineerId ?? null}
          passport={explanation?.job_id === o.jobId && explanation.status !== 'отменена'
            ? explanation.passport : undefined}
          onClose={c.close}
          onMoved={c.afterMove}
        />
      )}
      {s.busy && s.busyLimit > 0 && (
        <BusyOverlay label={s.busy} limit={s.busyLimit} since={s.busySince} />
      )}
    </>
  );
}

/** Точки, которые событие добавило в план или не смогло принять: карта
 *  обводит их, чтобы новая заявка не терялась среди сотни старых. */
export function highlightOf(b: Board): string[] {
  const d = b.s.diff;
  return d ? [...d.added.map(a => a.job_id), ...(d.rejected ?? []).map(r => r.job_id)] : [];
}

export function nowOf(b: Board): number | null {
  const at = b.s.plan?.meta?.at_min;
  return typeof at === 'number' ? at : null;
}

/** «из скольких» для числа исполнителей: урезанный парк — это весь парк сценария. */
export function fleetOf(b: Board): number {
  const region = b.s.regions.find(r => r.name === b.s.region);
  return b.s.keep || region?.engineers || 0;
}
