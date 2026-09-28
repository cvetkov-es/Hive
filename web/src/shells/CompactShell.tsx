// Раскладка B: ноутбук и всё, что меньше 1440×820.
//
// Отдельного списка бригад нет: строка ленты смены и есть строка бригады —
// код, транспорт, часы, заявки и километры. Так карта получает ширину левой
// колонки, а на экране 1366×768 остаётся и карта, и вся лента. Резерв — в
// «Сводке смены» справа.

import { useMemo } from 'react';
import type { Controller } from '../controller';
import type { Board } from '../store';
import { ErrorBoundary } from '../panels/ErrorBoundary';
import { Actions, Brand, RegionPicker, ScreenTabs, StatusChips } from '../panels/Header';
import { Inspector } from '../panels/Inspector';
import { Kpi } from '../panels/Kpi';
import { MapView, type Inset } from '../panels/MapView';
import { Search } from '../panels/Search';
import { Timeline } from '../panels/Timeline';
import { ZoneFilter } from '../panels/ZoneFilter';
import { Analytics, fleetOf, highlightOf, nowOf } from './common';

const MAP_INSET: Inset = { top: 12, right: 12, bottom: 12, left: 12 };

export function CompactShell({ b, c }: { b: Board; c: Controller }) {
  const { s } = b;
  const board = s.screen === 'board';
  const region = s.regions.find(r => r.name === s.region);
  const sel = s.selection;
  const selectedJob = sel.kind === 'job' ? sel.jobId : null;
  const highlight = useMemo(() => highlightOf(b), [s.diff]);

  return (
    <div className={`shell compact ${board ? '' : 'analytics'}`}>
      <header className="topbar">
        <ErrorBoundary label="Шапку" resetKey={`${s.region}:${s.screen}`}>
          <Brand />
          <RegionPicker b={b} />
          <ScreenTabs b={b} look="line" />
          <div className="spacer" />
          {board && (
            <div className="top-search">
              <Search plan={s.plan} onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
            </div>
          )}
          <Actions b={b} c={c} short />
        </ErrorBoundary>
      </header>

      {board ? (
        <div className="cbody">
          <div className="cmain">
            <div className="kpistrip">
              <ErrorBoundary label="Метрики" resetKey={s.plan?.plan_id}>
                <Kpi plan={s.plan} live={region?.live_day} fleet={fleetOf(b)} look="strip"
                     onUnassigned={c.showSummary} />
                <div className="spacer" />
                <div className="strip-side">
                  <StatusChips b={b} c={c} short />
                </div>
              </ErrorBoundary>
            </div>
            <div className="cmap">
              <ErrorBoundary label="Карту" float resetKey={s.plan?.plan_id}>
                <MapView plan={s.plan} previous={s.previous} marks={s.marks} geometry={s.geometry}
                         previousGeometry={s.previousGeometry} selectedEngineer={b.selectedEngineer}
                         selectedJob={selectedJob} highlightJobs={highlight} fitSeq={s.fitSeq}
                         inset={MAP_INSET} zone={c.zone} onShowAll={c.showAll} compactLegend
                         onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
              </ErrorBoundary>
            </div>
            <section className="ctl">
              <ErrorBoundary label="Ленту" resetKey={s.plan?.plan_id}>
                <Timeline plan={s.plan} previous={s.previous} diff={s.diff} marks={s.marks}
                          selectedEngineer={b.selectedEngineer} selectedJob={selectedJob}
                          nowMin={nowOf(b)} zone={c.zone} risk={c.risk} delay={c.delay}
                          labels="full" title="Бригады и лента смены"
                          extra={<ZoneFilter clusters={region?.clusters} zone={c.zone} onZone={c.setZone} />}
                          onResetDelay={() => c.setDelay(0)}
                          onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
              </ErrorBoundary>
            </section>
          </div>
          <aside className="cright">
            <Inspector b={b} c={c} showReserve fleetSize={region?.engineers ?? 0} />
          </aside>
        </div>
      ) : (
        <main className="page">
          <Analytics b={b} />
        </main>
      )}
    </div>
  );
}
