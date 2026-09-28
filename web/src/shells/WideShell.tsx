// Раскладка A: карта во весь экран, панели плавают поверх.
//
// Слева — набор данных, плашки, метрики, поиск и бригады; справа — правая
// панель; снизу между ними — лента смены. Карта получает свободную область
// между панелями: подгонка масштаба, кнопки и легенда ставятся туда, иначе
// маршруты уходили бы под список бригад.

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Controller } from '../controller';
import type { Board } from '../store';
import { ErrorBoundary } from '../panels/ErrorBoundary';
import { FleetList } from '../panels/FleetList';
import { Actions, Brand, RegionPicker, ScreenTabs, StatusChips } from '../panels/Header';
import { Inspector } from '../panels/Inspector';
import { Kpi } from '../panels/Kpi';
import { MapView, type Inset } from '../panels/MapView';
import { Search } from '../panels/Search';
import { Timeline } from '../panels/Timeline';
import { ZoneFilter } from '../panels/ZoneFilter';
import { Analytics, fleetOf, highlightOf, nowOf } from './common';

const GAP = 12;

/** Свободная от плавающих панелей часть экрана. Меряется, а не задаётся
 *  числами: лента сворачивается, панели меняют высоту по содержимому. */
function useFreeArea(refs: { top: React.RefObject<HTMLElement>; left: React.RefObject<HTMLElement>;
                             right: React.RefObject<HTMLElement>; bottom: React.RefObject<HTMLElement> },
                     deps: unknown[]): Inset {
  const [inset, setInset] = useState<Inset>({ top: 84, right: 408, bottom: 320, left: 368 });
  useLayoutEffect(() => {
    const calc = () => {
      const W = window.innerWidth, H = window.innerHeight;
      const t = refs.top.current?.getBoundingClientRect();
      const l = refs.left.current?.getBoundingClientRect();
      const r = refs.right.current?.getBoundingClientRect();
      const b = refs.bottom.current?.getBoundingClientRect();
      const next = {
        top: Math.round(t ? t.bottom + GAP : GAP),
        left: Math.round(l ? l.right + GAP : GAP),
        right: Math.round(r ? W - r.left + GAP : GAP),
        bottom: Math.round(b ? H - b.top + GAP : GAP),
      };
      setInset(v => (v.top === next.top && v.left === next.left && v.right === next.right
        && v.bottom === next.bottom ? v : next));
    };
    calc();
    const ro = new ResizeObserver(calc);
    for (const x of [refs.top, refs.left, refs.right, refs.bottom]) if (x.current) ro.observe(x.current);
    window.addEventListener('resize', calc);
    return () => { ro.disconnect(); window.removeEventListener('resize', calc); };
  }, deps);
  return inset;
}

export function WideShell({ b, c }: { b: Board; c: Controller }) {
  const { s } = b;
  const board = s.screen === 'board';
  const region = s.regions.find(r => r.name === s.region);
  const sel = s.selection;
  const selectedJob = sel.kind === 'job' ? sel.jobId : null;
  const highlight = useMemo(() => highlightOf(b), [s.diff]);

  const top = useRef<HTMLElement>(null);
  const left = useRef<HTMLElement>(null);
  const right = useRef<HTMLElement>(null);
  const bottom = useRef<HTMLElement>(null);
  const inset = useFreeArea({ top, left, right, bottom }, [board, c.timelineCollapsed]);

  const working = (s.plan?.routes ?? []).filter(r => r.stops.length).length;
  const reserve = s.plan?.reserve?.length ?? 0;

  return (
    <div className={`shell wide ${board ? '' : 'analytics'}`}>
      {board && (
        <div className="map-layer">
          <ErrorBoundary label="Карту" float resetKey={s.plan?.plan_id}>
            <MapView plan={s.plan} previous={s.previous} marks={s.marks} geometry={s.geometry}
                     previousGeometry={s.previousGeometry} selectedEngineer={b.selectedEngineer}
                     selectedJob={selectedJob} highlightJobs={highlight} fitSeq={s.fitSeq}
                     inset={inset} zone={c.zone} onShowAll={c.showAll}
                     onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
          </ErrorBoundary>
        </div>
      )}

      <header className="card top-card" ref={top}>
        <ErrorBoundary label="Шапку" resetKey={`${s.region}:${s.screen}`}>
          <Brand />
          {/* Шапка одна на всех экранах, и табы не сдвигаются. Выбор набора
              данных — в начале содержимого: на плане в левой панели, в
              аналитике над страницей. */}
          <span className="product">маршруты выездных бригад на один рабочий день</span>
          <ScreenTabs b={b} look="seg" />
          <div className="spacer" />
          <Actions b={b} c={c} />
        </ErrorBoundary>
      </header>

      {board ? (
        <>
          <aside className="card left-card" ref={left}>
            <ErrorBoundary label="Левую панель" resetKey={s.plan?.plan_id}>
            <div className="lc-head">
              <RegionPicker b={b} big />
              <StatusChips b={b} c={c} />
            </div>
            <Kpi plan={s.plan} live={region?.live_day} fleet={fleetOf(b)} look="grid"
                 onUnassigned={c.showSummary} />
            <div className="lc-search">
              <Search plan={s.plan} onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
            </div>
            <div className="lc-fleet-head">
              <span className="lc-title">Бригады</span>
              <span className="count">{working} в работе{reserve ? `, ${reserve} в резерве` : ''}</span>
            </div>
            <div className="lc-zones">
              <ZoneFilter clusters={region?.clusters} zone={c.zone} onZone={c.setZone} />
            </div>
            {/* Ключ по региону: новый набор данных открывается со своей первой
                бригады, а не с прокрутки, оставшейся от прошлого. */}
            <div className="lc-scroll" key={s.region}>
              <FleetList plan={s.plan} marks={s.marks} selected={b.selectedEngineer} zone={c.zone}
                         onSelect={id => {
                           // Строка бригады подсвечена и тогда, когда выбрана её заявка.
                           // Щелчок по ней в этом случае открывает маршрут, а не снимает
                           // выбор: иначе объяснение маршрута до этой бригады недостижимо.
                           if (id === null && sel.kind === 'job' && b.selectedEngineer) {
                             c.pickEngineer(b.selectedEngineer);
                           } else {
                             c.pickEngineer(id);
                           }
                         }} />
            </div>
            </ErrorBoundary>
          </aside>

          <aside className="card right-card" ref={right}>
            <Inspector b={b} c={c} showReserve={false} fleetSize={region?.engineers ?? 0} />
          </aside>

          <section className={`card tl-card ${c.timelineCollapsed ? 'collapsed' : ''}`} ref={bottom}>
            <ErrorBoundary label="Ленту" resetKey={s.plan?.plan_id}>
              <Timeline plan={s.plan} previous={s.previous} diff={s.diff} marks={s.marks}
                        selectedEngineer={b.selectedEngineer} selectedJob={selectedJob}
                        nowMin={nowOf(b)} zone={c.zone} risk={c.risk} delay={c.delay}
                        labels="short" title="Лента смены"
                        collapsed={c.timelineCollapsed} onToggle={c.toggleTimeline}
                        onResetDelay={() => c.setDelay(0)}
                        onPickJob={c.pickJob} onPickEngineer={id => c.pickEngineer(id)} />
            </ErrorBoundary>
          </section>
        </>
      ) : (
        <main className="page">
          <div className="page-head">
            <RegionPicker b={b} big />
          </div>
          <Analytics b={b} />
        </main>
      )}

      {board && !s.plan && s.busy && <div className="loading-note">{s.busy}…</div>}
    </div>
  );
}
