// Лента смены: ось 09:30–23:30, строка на бригаду.
//
// Карта показывает, КУДА бригада едет, лента — КОГДА и почему день не резиновый.
// Здесь видно то, чего на карте нет: работа, дорога, ожидание у закрытой двери
// и упор в двенадцатичасовой норматив — красная черта в каждой строке. Это
// ограничение, а не оформление: бригада с девятью заявками может выглядеть
// свободной, упираясь при этом в предел рабочего дня.
//
// Блок окрашен цветом бригады — тем же, что её маршрут на карте: строку ленты
// и линию на карте глаз связывает без легенды. Авария отмечена тёмной полоской
// снизу, вид работ — в подсказке и в объяснении.
//
// После события лента показывает и прошлое: что заморожено (начато или
// бригада уже едет), где заявка стояла раньше, если её время сдвинулось, и
// откуда она ушла, если досталась другой бригаде, вытеснена или отменена.
//
// «Если каждая работа затянется»: пока задержка больше нуля, блоки стоят там,
// где окажутся по прогнозу (lib/delay.ts), опоздавшие обведены красным, а у
// бригад сверх 12 часов за чертой нормы — красный хвост.

import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Diff, FrozenMark, Plan, Route } from '../api';
import type { Mark } from '../colors';
import type { DelayRisk } from '../lib/delay.ts';
import { inZone } from '../lib/zones.ts';
import { dur, shortTransport } from '../text';
import { hhmm, toMin } from '../store';

const AXIS_START = 9 * 60 + 30;
const AXIS_END = 23 * 60 + 30;
const SPAN = AXIS_END - AXIS_START;
const NORM_MIN = 12 * 60;
const WAIT_LABEL_MIN = 60;     // ожидание, которое подписывается прямо на ленте

const pct = (min: number) => ((Math.min(AXIS_END + 30, min) - AXIS_START) / SPAN) * 100;
const wpct = (a: number, b: number) => Math.max(0.35, pct(b) - pct(a));

function Ruler({ nowMin, full }: { nowMin: number | null; full: boolean }) {
  // Часы рядом с подписью события прячем: «15» налезает на «событие 15:40».
  const ticks: number[] = [];
  for (let m = 10 * 60; m <= AXIS_END; m += 60) {
    if (nowMin === null || Math.abs(m - nowMin) > 45) ticks.push(m);
  }
  return (
    <div className="tl-ruler">
      <div className="tl-label-sp">
        {full && <><span>бригада</span><span>заявок · км</span></>}
      </div>
      <div className="tl-track">
        {ticks.map(m => (
          <span key={m} className="tick" style={{ left: `${pct(m)}%` }}>
            {String(Math.floor(m / 60)).padStart(2, '0')}
          </span>
        ))}
        {nowMin !== null && nowMin >= AXIS_START && nowMin <= AXIS_END && (
          <span className="tick now" style={{ left: `${pct(nowMin)}%` }}>
            событие {hhmm(nowMin)}
          </span>
        )}
      </div>
    </div>
  );
}

/** Где заявка стояла до события, если в этой строке её больше нет. */
type Gone = { job_id: string; start: number; end: number; why: string };

function Row({ route, engineerId, mark, full, dimmed, selectedJob, ghost, gone,
               frozen, cameFrom, risk, onPick, onPickEngineer }: {
  route: Route | null;
  engineerId: string;
  mark: Mark;
  full: boolean;
  dimmed: boolean;
  selectedJob: string | null;
  ghost?: Route;
  gone: Gone[];
  frozen: Record<string, FrozenMark>;
  cameFrom: Record<string, string>;
  risk: DelayRisk | null;
  onPick: (jobId: string) => void;
  onPickEngineer: (engineerId: string) => void;
}) {
  const stops = route?.stops ?? [];
  const depart = route ? toMin(route.depart) : null;
  const limit = depart !== null ? depart + NORM_MIN : null;
  const ghostById: Record<string, number> = {};
  for (const s of ghost?.stops ?? []) ghostById[s.job_id] = toMin(s.start);
  const delayed = risk?.routes[engineerId];
  const last = stops[stops.length - 1];

  return (
    <div className={`tl-row ${dimmed ? 'dim' : ''}`} data-engineer={engineerId}>
      <button className="tl-name" onClick={() => onPickEngineer(engineerId)}
              title={`${route ? `${route.engineer_name}, ${route.transport}, зона «${route.cluster}»` : engineerId}: объяснение маршрута`}>
        <i className={`swatch ${mark.shape}`} style={{ background: mark.color }} />
        <b>{engineerId.replace(/^BR-/, '')}</b>
        {full && route && last && (
          <>
            <span className="tl-meta">{shortTransport(route.transport)} · {route.depart}–{last.end}</span>
            <span className="tl-num">{route.jobs} · {route.km.toFixed(1)} км</span>
          </>
        )}
        {full && !route && <span className="tl-meta">без заявок после события</span>}
      </button>
      <div className="tl-track">
        {limit !== null && limit <= AXIS_END && (
          <span className="tl-limit" style={{ left: `${pct(limit)}%` }}
                title={`предел рабочего дня: выезд ${route?.depart} плюс 12 ч`} />
        )}
        {delayed?.overtime && limit !== null && depart !== null && (
          <span className="tl-over"
                style={{ left: `${pct(limit)}%`, width: `${wpct(limit, depart + delayed.span)}%` }}
                title={`с задержкой день ${dur(delayed.span)} — на ${dur(delayed.overBy)} сверх норматива`} />
        )}

        {gone.map(g => (
          <span key={`gone-${g.job_id}`} className="tl-block gone"
                style={{ left: `${pct(g.start)}%`, width: `${wpct(g.start, g.end)}%` }}
                title={`${g.job_id}: до события стояла здесь, ${hhmm(g.start)}–${hhmm(g.end)}; ${g.why}`}
                onClick={() => onPick(g.job_id)} />
        ))}

        {stops.map((s, i) => {
          const d = delayed?.stops[i];
          const arrive = d ? d.arrive : toMin(s.arrive);
          const start = d ? d.start : toMin(s.start);
          const end = d ? d.end : toMin(s.end);
          const prevEnd = i === 0 ? (depart ?? arrive)
            : (delayed ? delayed.stops[i - 1].end : toMin(stops[i - 1].end));
          const was = ghostById[s.job_id];
          const fz = frozen[s.job_id];
          const from = cameFrom[s.job_id];
          const wait = start - arrive;
          const cls = ['tl-block',
            s.skill === 'Аварийные работы' ? 'em' : '',
            fz?.state === 'started' ? 'started' : '',
            fz?.state === 'en_route' ? 'enroute' : '',
            from ? 'moved-in' : '',
            d?.late ? 'late' : '',
            selectedJob === s.job_id ? 'sel' : ''].filter(Boolean).join(' ');
          return (
            <span key={s.job_id}>
              <span className="tl-travel"
                    style={{ left: `${pct(prevEnd)}%`, width: `${Math.max(0, pct(arrive) - pct(prevEnd))}%` }}
                    title={`в пути ${s.travel_min} мин, ${s.km.toFixed(1)} км`} />
              {wait > 0 && (
                <span className="tl-wait"
                      style={{ left: `${pct(arrive)}%`, width: `${pct(start) - pct(arrive)}%` }}
                      title={`приедет в ${hhmm(arrive)} и будет ждать открытия окна клиента ${s.window}: ${dur(wait)}`}>
                  {wait >= WAIT_LABEL_MIN && <em>ждёт {dur(wait)}</em>}
                </span>
              )}
              {!d && was !== undefined && Math.abs(was - start) > 1 && (
                <span className="tl-block ghost"
                      style={{ left: `${pct(was)}%`, width: `${wpct(was, was + (end - start))}%` }}
                      title={`${s.job_id}: до события начало было в ${hhmm(was)}, теперь в ${s.start}`} />
              )}
              <span
                className={cls}
                style={{ left: `${pct(start)}%`, width: `${wpct(start, end)}%`, backgroundColor: mark.color }}
                title={`${s.job_id} · ${s.skill}\n${s.address}\n`
                  + `окно ${s.window}, начало ${d ? hhmm(start) : s.start}, конец ${d ? hhmm(end) : s.end}`
                  + (d && d.shift > 0 ? `\nс задержкой начнётся на ${dur(d.shift)} позже плана` : '')
                  + (d?.late ? '\nпозже закрытия окна клиента' : '')
                  + (fz?.state === 'started' ? '\nначата до события — заморожена' : '')
                  + (fz?.state === 'en_route' ? '\nбригада уже едет сюда — визит заморожен' : '')
                  + (from ? `\nдо события была у ${from}` : '')}
                onClick={() => onPick(s.job_id)}
              />
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function Timeline({ plan, previous, diff, marks, selectedEngineer, selectedJob,
                           nowMin, zone, risk, delay, labels, title, extra,
                           collapsed, onToggle, onResetDelay,
                           onPickJob, onPickEngineer }: {
  plan: Plan | null;
  previous: Plan | null;
  diff: Diff | null;
  marks: Record<string, Mark>;
  selectedEngineer: string | null;
  selectedJob: string | null;
  nowMin: number | null;
  zone: string;
  risk: DelayRisk | null;
  delay: number;
  /** Подписи строк: коротко — код бригады (раскладка A, рядом есть список);
   *  полно — код, транспорт, часы, заявки и км (раскладка B, где строка ленты
   *  и есть строка бригады). */
  labels: 'short' | 'full';
  title: string;
  /** Что ещё стоит в заголовке: счётчик и фильтр зон в раскладке B. */
  extra?: ReactNode;
  collapsed?: boolean;
  onToggle?: () => void;
  onResetDelay: () => void;
  onPickJob: (jobId: string) => void;
  onPickEngineer: (engineerId: string) => void;
}) {
  // Выбранная бригада может стоять ниже видимой части ленты — прокручиваем к
  // ней, иначе после щелчка по заявке на карте её строка остаётся за краем.
  const scroller = useRef<HTMLDivElement>(null);
  const [legendOpen, setLegendOpen] = useState(false);
  const head = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!legendOpen) return;
    const away = (e: MouseEvent) => { if (!head.current?.contains(e.target as Node)) setLegendOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setLegendOpen(false); };
    window.addEventListener('mousedown', away);
    window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('mousedown', away); window.removeEventListener('keydown', esc); };
  }, [legendOpen]);
  useEffect(() => {
    if (!selectedEngineer || !scroller.current) return;
    const row = [...scroller.current.querySelectorAll<HTMLElement>('.tl-row')]
      .find(el => el.dataset.engineer === selectedEngineer);
    if (!row) return;
    const box = scroller.current;
    const top = row.offsetTop - box.offsetTop;
    if (top < box.scrollTop || top + row.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(0, top - box.clientHeight / 2 + row.offsetHeight / 2) });
    }
  }, [selectedEngineer]);

  if (!plan) return null;
  const routes = plan.routes.filter(r => r.stops.length);
  const ghostByEngineer: Record<string, Route> = {};
  for (const r of previous?.routes ?? []) ghostByEngineer[r.engineer_id] = r;
  const frozen = plan.meta?.frozen ?? {};

  // Заявки, ушедшие из строки бригады: к другой бригаде, в вытесненные или
  // в отмену. Их прежнее место остаётся на ленте полой рамкой — иначе переезд
  // заявки к другому исполнителю не виден ни в старой строке, ни в новой.
  const ownerNow: Record<string, string> = {};
  for (const r of routes) for (const s of r.stops) ownerNow[s.job_id] = r.engineer_id;
  const cameFrom: Record<string, string> = {};
  const goneByEngineer: Record<string, Gone[]> = {};
  const dropped = new Set((diff?.dropped ?? []).map(d => d.job_id));
  const cancelled = new Set((diff?.cancelled ?? []).map(d => d.job_id));
  const clusterOf: Record<string, string> = {};
  for (const r of [...(previous?.routes ?? []), ...routes]) clusterOf[r.engineer_id] = r.cluster;
  for (const r of previous?.routes ?? []) {
    for (const s of r.stops) {
      const now = ownerNow[s.job_id];
      if (now === r.engineer_id) continue;
      if (now) cameFrom[s.job_id] = r.engineer_id;
      const why = now ? `после события её везёт ${now}`
        : cancelled.has(s.job_id) ? 'клиент отменил заявку'
        : dropped.has(s.job_id) ? 'вытеснена — смотрите «Что изменилось»'
        : 'в новом плане её нет';
      (goneByEngineer[r.engineer_id] ??= []).push(
        { job_id: s.job_id, start: toMin(s.start), end: toMin(s.end), why });
    }
  }
  // Строка бригады, у которой после события не осталось ни одной заявки, не
  // пропадает молча: в ней остаются следы того, что у неё забрали.
  const rows: { id: string; route: Route | null }[] = routes.map(r => ({ id: r.engineer_id, route: r }));
  for (const id of Object.keys(goneByEngineer)) {
    if (!routes.some(r => r.engineer_id === id)) rows.push({ id, route: null });
  }
  const shown = rows
    .filter(r => inZone(clusterOf[r.id] ?? '', zone))
    .sort((a, b) => a.id.localeCompare(b.id, 'ru', { numeric: true }));

  const hasFrozen = Object.keys(frozen).length > 0;
  const full = labels === 'full';
  const hasEmergency = routes.some(r => r.stops.some(st => st.skill === 'Аварийные работы'));
  const legend = (
    <>
      <span><i className="lg-work" />работа</span>
      <span><i className="lg-road" />дорога</span>
      <span><i className="lg-wait" />ожидание</span>
      <span><i className="lg-limit" />предел 12 ч</span>
      {hasEmergency && <span><i className="lg-em" />авария</span>}
      {hasFrozen && <span><i className="lg-started" />начато до события</span>}
      {hasFrozen && <span><i className="lg-enroute" />уже едет</span>}
      {previous && <span><i className="lg-moved" />сменила бригаду</span>}
      {previous && <span><i className="lg-gone" />ушла из строки</span>}
      {previous && <span><i className="lg-ghost" />как было</span>}
      {risk && <span><i className="lg-late" />опоздает</span>}
      {risk && risk.overtime > 0 && <span><i className="lg-over" />сверх 12 ч</span>}
    </>
  );

  return (
    <div className={`timeline ${full ? 'full' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <div className="tl-head" ref={head}>
        <div className="tl-head-row">
          <span className="tl-title">{title}</span>
          {extra}
          {delay > 0 && (
            <span className="tl-delay">
              задержка +{delay} мин
              <button aria-label="Убрать задержку" title="Убрать задержку" onClick={onResetDelay}>×</button>
            </span>
          )}
          <span className="spacer" />
          {full && (
            <button className="linkish tl-legend-btn" aria-expanded={legendOpen}
                    onClick={() => setLegendOpen(v => !v)}>
              Обозначения {legendOpen ? '▴' : '▾'}
            </button>
          )}
          {onToggle && (
            <button className="linkish tl-toggle" onClick={onToggle}>
              {collapsed ? 'Развернуть' : 'Свернуть'}
            </button>
          )}
        </div>
        {/* На широком экране легенда — строкой под заголовком; на ноутбуке она
            съедала бы строки бригад и живёт за кнопкой «Обозначения». В
            свёрнутой ленте легенда не нужна вовсе. */}
        {!full && !collapsed && <div className="tl-legend">{legend}</div>}
        {full && legendOpen && (
          <div className="tl-legend tl-legend-pop" role="dialog" aria-label="Обозначения ленты">
            {legend}
          </div>
        )}
      </div>
      {!collapsed && (
        <div className="tl-body">
          <Ruler nowMin={nowMin} full={full} />
          <div className="tl-scroll" ref={scroller}>
            {shown.map(({ id, route }) => (
              <Row
                key={id}
                route={route}
                engineerId={id}
                mark={marks[id] ?? { color: '#55636f', shape: 'ci' }}
                full={full}
                dimmed={!!selectedEngineer && selectedEngineer !== id}
                selectedJob={selectedJob}
                ghost={ghostByEngineer[id]}
                gone={goneByEngineer[id] ?? []}
                frozen={frozen}
                cameFrom={cameFrom}
                risk={risk}
                onPick={onPickJob}
                onPickEngineer={onPickEngineer}
              />
            ))}
            {!shown.length && <div className="empty">В этой зоне бригад нет.</div>}
          </div>
          {nowMin !== null && nowMin >= AXIS_START && nowMin <= AXIS_END && (
            <div className="tl-now-wrap" aria-hidden="true">
              <div className="tl-label-sp" />
              <div className="tl-track"><div className="tl-now" style={{ left: `${pct(nowMin)}%` }} /></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
