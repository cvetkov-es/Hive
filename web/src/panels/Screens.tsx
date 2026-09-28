// Экраны, отвечающие на вопросы эксперта: с чем сравниваем, откуда число бригад и
// сколько стоит каждое ограничение. «Что будет, если работа затянется» —
// в «Сводке смены» и на ленте (DaySummary, lib/delay.ts).
//
// Все три читают заранее посчитанные расчёты и НЕ строят маршруты заново.
// Вопрос «откуда взялось число бригад» нельзя закрывать лотереей: результат
// зависит от того, сколько процессорного времени досталось прогону, и
// одинаковые числа на экране и на слайдах важнее свежести расчёта.

import { useEffect, useState } from 'react';
import { api, type Compare, type CompareColumn, type Convergence, type CurvePoint,
         type Sensitivity, type Summary } from '../api';
import { MOMENTS, conclusion, secondsLabel } from '../lib/convergence';
import { count, dur, plural, signed } from '../text';

// Наш план — акцент (янтарь пульта), остальные колонки — фон: сравнение про
// одну колонку. Синий акцент совпал бы с цветом первой бригады на карте и
// читался бы как «это про BR-01».
const ACCENT = '#95600a';
const CONTEXT = '#c9d2d9';

// --- сравнение планов --------------------------------------------------------

type RowKey = 'used_engineers' | 'assigned' | 'unassigned' | 'cancelled' | 'total_km' | 'late_jobs';

const ROWS: { key: RowKey; title: string; unit?: string; digits?: number;
              moreIsBetter: boolean; diff: (d: number, pct: number | null) => string }[] = [
  { key: 'used_engineers', title: 'Бригад в работе', moreIsBetter: false,
    diff: (d, pct) => `${signed(d)} ${plural(Math.abs(d), 'бригада', 'бригады', 'бригад')}`
      + (pct !== null && d !== 0 ? ` (${signed(pct)}%)` : '') },
  { key: 'assigned', title: 'Заявок распределено', moreIsBetter: true,
    diff: d => `${signed(d)} ${plural(Math.abs(d), 'закрытая заявка', 'закрытые заявки', 'закрытых заявок')}` },
  { key: 'unassigned', title: 'Не распределено', moreIsBetter: false,
    diff: d => signed(d) },
  { key: 'cancelled', title: 'Отменено клиентом', moreIsBetter: false,
    diff: d => signed(d) },
  { key: 'total_km', title: 'Суммарный пробег', unit: ' км', digits: 1, moreIsBetter: false,
    diff: (d, pct) => `${signed(d, 1)} км` + (pct !== null && d !== 0 ? ` (${signed(pct)}%)` : '') },
  { key: 'late_jobs', title: 'Просрочек', moreIsBetter: false,
    diff: d => signed(d) },
];

/** Горизонтальные полоски по одной обязательной метрике. Одна шкала от нуля
 *  на все колонки; колонка без значения не рисуется полоской нулевой длины, а
 *  подписывается словами — ноль здесь был бы неправдой. */
function MetricBars({ title, columns, pick, unit, digits = 0, note }: {
  title: string; columns: CompareColumn[];
  pick: (c: CompareColumn) => number | null; unit: string; digits?: number;
  note: string;
}) {
  const vals = columns.map(pick);
  const max = Math.max(...vals.map(v => v ?? 0), 1);
  return (
    <div className="chart-card bars">
      <div className="chart-title">{title}</div>
      <div className="chart-sub">{note}</div>
      {columns.map((c, i) => {
        const v = vals[i];
        return (
          <div key={c.key} className="bar-row">
            {/* У полосок место узкое: «(п. 2.3 ТЗ)» — в заголовке таблицы ниже. */}
            <span className="bar-label">{c.title.replace(/\s*\(.*\)$/, '')}</span>
            <span className="bar-track">
              {v === null
                ? <span className="bar-na">нет данных: в файле реального дня нет порядка объезда</span>
                : <>
                    <i style={{ width: `${(v / max) * 100}%`, background: c.key === 'solver' ? ACCENT : CONTEXT }} />
                    <b>{v.toFixed(digits)}{unit}</b>
                  </>}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function CompareScreen({ region, keep, fleetSize, events = [] }: {
  region: string; keep: number; fleetSize: number; events?: string[];
}) {
  const [data, setData] = useState<Compare | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setData(null); setError('');
    api.compare(region).then(setData).catch(e => setError(String((e as Error).message)));
  }, [region]);

  if (error) return <div className="screen"><p className="un-reason">{error}</p></div>;
  if (!data) return <div className="screen"><p className="note">Собираем сравнение…</p></div>;

  const ours = data.columns.find(c => c.key === 'solver');
  const base = data.columns.find(c => c.key === 'baseline');
  const control = data.columns.find(c => c.key === 'control');
  const table = [ours, base, control].filter((c): c is CompareColumn => !!c);

  const diffCell = (r: typeof ROWS[number]) => {
    if (!ours || !base) return null;
    const a = ours[r.key] as number | null | undefined, b = base[r.key] as number | null | undefined;
    // Отмен в наших планах не бывает по построению: сравнивать там нечего.
    if (a == null || b == null || r.key === 'cancelled') return <td className="n note">—</td>;
    const d = Number((a - b).toFixed(r.digits ?? 0));
    const pct = b ? Math.round(((a - b) / b) * 100) : null;
    const good = r.moreIsBetter ? d > 0 : d < 0;
    return (
      <td className={`n nowrap diff ${d === 0 ? 'note' : good ? 'yes' : 'no'}`}>
        {d === 0 ? 'поровну' : `${r.diff(d, pct)} · ${good ? 'лучше' : 'хуже'}`}
      </td>
    );
  };

  const kmDelta = ours && base && ours.total_km !== null && base.total_km !== null
    ? ours.total_km - base.total_km : null;
  const engDelta = ours && base ? ours.used_engineers - base.used_engineers : null;

  return (
    <div className="screen">
      <h2>Наш план, базовый вариант и реальный день</h2>
      <p className="lede">
        <b>Базовый вариант</b> — простое распределение из пункта 2.3 технического
        задания: заявки по очереди достаются первой подходящей бригаде, без
        оптимизации. Данные, правила и проверка те же, что у нашего плана,
        поэтому разница между ними — заслуга алгоритма. <b>Реальный день</b> —
        как участок отработал этот день на самом деле, по данным организаторов.
      </p>
      {keep > 0 && (
        <div className="warnbox">
          Сравнение — по полному штату ({count(fleetSize, 'бригада', 'бригады', 'бригад')}):
          план с урезанным штатом ({keep}) в нём не участвует.
        </div>
      )}
      {events.length > 0 && (
        <div className="warnbox">
          Сравнение — по плану дня до событий: базовый вариант событий не
          учитывает, поэтому таблица сравнивает день целиком, а план дня на
          главном экране — уже после {events.length === 1 ? 'события' : 'событий'}: {events.join('; ')}.
        </div>
      )}
      {data.warnings.map((w, i) => <div key={i} className="warnbox">{w}</div>)}

      <div className="bars-grid">
        <MetricBars title="Бригад в работе" columns={table}
                    pick={c => c.used_engineers} unit=""
                    note={engDelta !== null && engDelta !== 0
                      ? `наш план: ${signed(engDelta)} ${plural(Math.abs(engDelta), 'бригада', 'бригады', 'бригад')} к базовому варианту`
                      : 'бригад столько же, сколько у базового варианта'} />
        <MetricBars title="Суммарный пробег" columns={table}
                    pick={c => c.total_km} unit=" км" digits={1}
                    note={kmDelta !== null && base?.total_km
                      ? `наш план: ${signed(kmDelta, 1)} км (${signed(Math.round((kmDelta / base.total_km) * 100))}%) к базовому варианту`
                      : ''} />
      </div>

      <div className="compare-layout">
      <div className="table-scroll">
        <table className="compare">
          <thead>
            <tr>
              <th>Показатель</th>
              {ours && <th className="n">{ours.title}</th>}
              {base && <th className="n">{base.title}</th>}
              {ours && base && <th className="n">Наш план против базового</th>}
              {control && <th className="n">{control.title}</th>}
            </tr>
          </thead>
          <tbody>
            {ROWS.map(r => (
              <tr key={r.key}>
                <td>{r.title}</td>
                {[ours, base].map(c => c && (
                  <td key={c.key} className={`n ${c.key === 'solver' ? 'strong' : ''}`}>
                    {fmt(c[r.key] as number | null | undefined, r, c.key)}
                  </td>
                ))}
                {diffCell(r)}
                {control && (
                  <td className="n">
                    {r.key === 'total_km' ? <span className="note">нет данных</span>
                      : fmt(control[r.key] as number | null | undefined, r, 'control')}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {control && (
        <div className="chart-card control-card">
          <div className="cc-head">
          <div className="chart-title">{control.title}: из чего складывается</div>
          {control.breakdown && <div className="breakdown">{control.breakdown}</div>}
          {control.statuses && (
            <div className="chip-line">
              {Object.entries(control.statuses).map(([k, v]) => (
                <span key={k} className="chip">{STATUS_RU[k] ?? k}: {v}</span>
              ))}
            </div>
          )}
          </div>
          <div className="cc-text">
          <p className="note">{control.about ?? data.control_about}</p>
          <p className="note">
            В реальный день распределено меньше не потому, что заявки не выполнили:
            {' '}{count(control.cancelled ?? 0, 'заявку', 'заявки', 'заявок')} клиенты отменили уже в течение
            дня. Мы планируем весь день заранее, когда об отменах ещё неизвестно.
            Пробег реального дня посчитать нельзя: в файле видно, какая бригада взяла
            заявку, но не порядок объезда.
          </p>
          </div>
        </div>
      )}
      </div>

      <ConvergenceSection region={region} />
    </div>
  );
}

// --- кривая «время счёта -> результат» ---------------------------------------

/** Когда план снимает бригаду, пробег может вырасти: её заявки делят
 *  остальные. Если так вышло в таблице — сказать об этом прямо, иначе
 *  строка «10 минут, а километров больше, чем за минуту» выглядит ошибкой. */
function fewerButLonger(vals: ({ engineers: number; km: number } | null | undefined)[],
                        rows: { seconds: number }[]): string {
  for (let i = vals.length - 1; i > 0; i--) {
    const a = vals[i - 1], b = vals[i];
    if (a && b && b.engineers < a.engineers && b.km > a.km + 0.05) {
      return ` Поэтому к ${secondsLabel(rows[i].seconds)} бригад стало меньше, `
        + `а пробег вырос на ${(b.km - a.km).toFixed(1)} км: заявки снятой бригады `
        + 'взяли остальные, и им дальше ехать.';
    }
  }
  return '';
}

function ConvergenceSection({ region }: { region: string }) {
  const [data, setData] = useState<Convergence | null>(null);

  useEffect(() => {
    api.convergence().then(setData).catch(() => setData(null));
  }, []);

  const r = data?.regions[region];
  if (!data || !r || !r.steps.length) return null;
  const horizon = data.seconds_per_region;
  const rows = data.table.filter(row => region in row.regions);

  return (
    <section className="conv">
      <h2>Сколько считать план</h2>
      <p className="lede">
        План дня искали заранее, 10 минут на участок. Диспетчер столько не ждёт:
        событие пересчитывается за 10 секунд, «Пересчитать» — за 20. В таблице —
        каким план успевает стать к каждому моменту поиска.
      </p>
      <p className="conclusion">{conclusion(r.steps, r.jobs, horizon)}</p>

      <table className="conv-table">
        <thead>
          <tr>
            <th className="n">Сколько искали</th><th className="n">Закрыто</th>
            <th className="n">Бригад</th><th className="n">Пробег</th><th />
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const v = row.regions[region];
            const m = MOMENTS.find(x => x.seconds === row.seconds);
            return (
              <tr key={row.seconds} className={m ? 'moment' : ''}>
                <td className="n">{secondsLabel(row.seconds)}</td>
                {v ? (
                  <>
                    <td className={`n ${v.assigned < r.jobs ? 'no' : ''}`}>{v.assigned} из {r.jobs}</td>
                    <td className="n">{v.engineers}</td>
                    <td className="n">{v.km.toFixed(1)} км</td>
                  </>
                ) : <td className="n note" colSpan={3}>плана ещё нет</td>}
                <td className="note">{m?.what ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="caveat conv-caveat">
        В каждой строке — лучший план, найденный к этому моменту одного и того же
        поиска; на более медленном сервере те же планы находятся позже. План
        сначала закрывает заявки, потом сокращает бригады и только потом
        километры.{fewerButLonger(rows.map(row => row.regions[region]), rows)}
      </div>
    </section>
  );
}

const STATUS_RU: Record<string, string> = {
  'Выполнена': 'выполнено', 'В работе': 'в работе', 'В пути': 'бригада в пути',
  'Отправлена': 'отправлено бригаде', 'Просрочена': 'просрочено',
};

function fmt(v: number | null | undefined, r: typeof ROWS[number], key: string) {
  if (v == null) return '—';
  if (r.key === 'cancelled' && key !== 'control') return '—';
  return `${v.toFixed(r.digits ?? 0)}${r.unit ?? ''}`;
}

// --- кривая «парк -> результат» ---------------------------------------------

type Axis = { min: number; max: number };

function scale(v: number, a: Axis, size: number) {
  if (a.max === a.min) return size / 2;
  return ((v - a.min) / (a.max - a.min)) * size;
}

/** Один график кривой. Две меры на одной картинке дали бы две шкалы Y — это
 *  самая частая ошибка в графиках: линии пересекаются там, где пересечения нет.
 *  Поэтому меры разведены по двум графикам с общей осью X. Точек четыре, и
 *  каждая подписана значением: экран должен отвечать без ведущего. */
function CurveChart({ points, valueOf, label, unit, color, decimals = 0,
                      maxHint, liveFleet }: {
  points: CurvePoint[];
  valueOf: (p: CurvePoint) => number;
  label: string;
  unit: string;
  color: string;
  decimals?: number;
  maxHint?: number;
  liveFleet?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, H = 176, PAD = { l: 46, r: 24, t: 24, b: 30 };
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;

  const xs: Axis = { min: Math.min(...points.map(p => p.fleet)),
                     max: Math.max(...points.map(p => p.fleet)) };
  const vals = points.map(valueOf);
  const ys: Axis = { min: Math.min(0, ...vals),
                     max: Math.max(maxHint ?? 0, ...vals) * (maxHint ? 1 : 1.08) };

  const px = (p: CurvePoint) => PAD.l + scale(p.fleet, xs, iw);
  const py = (v: number) => PAD.t + ih - scale(v, ys, ih);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${px(p).toFixed(1)},${py(valueOf(p)).toFixed(1)}`).join(' ');

  const gridVals = [ys.min, (ys.min + ys.max) / 2, ys.max];
  const lx = liveFleet !== undefined ? PAD.l + scale(liveFleet, xs, iw) : 0;
  // Подпись контрольного дня внизу области графика: там нет точек, а у правого
  // края она разворачивается влево, иначе «живой день» обрезается до «жь».
  const liveAnchorEnd = lx > W - PAD.r - 110;

  const hp = hover !== null ? points[hover] : null;

  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label={`${label} в зависимости от числа бригад в штате`}
         onMouseLeave={() => setHover(null)}>
      {gridVals.map((v, i) => (
        <g key={i}>
          <line x1={PAD.l} x2={W - PAD.r} y1={py(v)} y2={py(v)}
                stroke="var(--rule)" strokeWidth="1" />
          <text x={PAD.l - 6} y={py(v) + 4} textAnchor="end"
                fontSize="11" fill="var(--ink-3)">
            {v.toFixed(decimals)}
          </text>
        </g>
      ))}

      {liveFleet !== undefined && liveFleet >= xs.min && liveFleet <= xs.max && (
        <g>
          <line x1={lx} x2={lx} y1={PAD.t} y2={PAD.t + ih}
                stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="3 3" />
          <text x={liveAnchorEnd ? lx - 5 : lx + 5} y={PAD.t + ih - 6}
                textAnchor={liveAnchorEnd ? 'end' : 'start'}
                fontSize="11" fill="var(--ink-2)">реальный день: {liveFleet}</text>
        </g>
      )}

      <path d={d} fill="none" stroke={color} strokeWidth="2"
            strokeLinejoin="round" strokeLinecap="round" />

      {points.map((p, i) => {
        const on = hover === i;
        const v = valueOf(p);
        return (
          <g key={p.fleet}>
            <circle cx={px(p)} cy={py(v)} r={on ? 6 : 4.5}
                    fill={color} stroke="#fff" strokeWidth="2" />
            {/* Крайние подписи — внутрь графика, иначе первая налезает на шкалу. */}
            <text x={px(p) + (i === 0 ? 5 : i === points.length - 1 ? -5 : 0)} y={py(v) - 10}
                  textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}
                  fontSize="12" fontWeight="700" fill="var(--ink)"
                  stroke="var(--panel)" strokeWidth="4" strokeLinejoin="round" paintOrder="stroke">
              {v.toFixed(decimals)}{unit}
            </text>
            <text x={px(p)} y={H - 10} textAnchor="middle"
                  fontSize="11" fill="var(--ink-3)">{p.fleet}</text>
            <rect x={px(p) - 24} y={PAD.t - 20} width="48" height={ih + 20}
                  fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        );
      })}
      {hp && (() => {
        const x = Math.min(Math.max(px(hp) - 95, 4), W - 194);
        return (
          <g pointerEvents="none">
            <rect x={x} y={PAD.t + ih - 58} width="190" height="50" rx="3"
                  fill="var(--panel)" stroke="var(--rule-hard)" />
            <text x={x + 8} y={PAD.t + ih - 41} fontSize="11.5" fill="var(--ink)" fontWeight="700">
              Парк {hp.fleet}: в работе {hp.used}
            </text>
            <text x={x + 8} y={PAD.t + ih - 26} fontSize="11" fill="var(--ink-2)">
              закрыто {hp.assigned}, потеряно {hp.unassigned}
            </text>
            <text x={x + 8} y={PAD.t + ih - 13} fontSize="11" fill="var(--ink-2)">
              {hp.km.toFixed(1)} км · считали {computeTime(hp.source)}
            </text>
          </g>
        );
      })()}
    </svg>
  );
}

/** «180 с» -> «3 мин»; «основной прогон» — это эталонный план региона. */
function computeTime(source: string, mainSeconds?: number): string {
  const m = /^(\d+)\s*с$/.exec(source.trim());
  if (m) return dur(Number(m[1]) / 60);
  if (source === 'основной прогон') {
    return mainSeconds ? `${dur(mainSeconds / 60)} — основной план` : 'основной план';
  }
  return source;
}

/** Вывод над графиком — из данных, а не из головы: где заявки перестают
 *  теряться и сколько бригад план при этом задействует. */
function curveConclusion(points: CurvePoint[], jobs: number, live?: number): string {
  if (!points.length) return '';
  const sorted = [...points].sort((a, b) => a.fleet - b.fleet);
  const full = sorted.filter(p => p.unassigned === 0);
  const maxFleet = sorted[sorted.length - 1].fleet;
  const parts: string[] = [];
  if (full.length) {
    const f = full[0];
    const all = `Все ${jobs} ${plural(jobs, 'заявка закрывается', 'заявки закрываются', 'заявок закрываются')}`;
    parts.push(f.fleet === maxFleet
      ? `${all} только при полном штате — ${count(f.fleet, 'бригада', 'бригады', 'бригад')}, из них в работе ${f.used}`
      : `${all} начиная с ${count(f.fleet, 'бригады', 'бригад', 'бригад')} в штате; в работе при этом ${f.used}`
        + (Math.max(...full.map(p => p.used)) <= f.used ? ' — больше план не задействует, лишние остаются в резерве' : ''));
  } else {
    parts.push(`Даже при ${count(maxFleet, 'бригаде', 'бригадах', 'бригадах')} часть заявок остаётся без исполнителя`);
  }
  const lost = sorted.filter(p => p.unassigned > 0).sort((a, b) => b.fleet - a.fleet);
  if (lost.length) {
    const [first, ...rest] = lost;
    parts.push(`При ${count(first.fleet, 'бригаде', 'бригадах', 'бригадах')} `
      + `${plural(first.unassigned, 'теряется', 'теряются', 'теряются')} `
      + `${count(first.unassigned, 'заявка', 'заявки', 'заявок')}`
      + rest.map(p => `, при ${p.fleet} — ${p.unassigned}`).join(''));
  }
  if (live) parts.push(`В реальный день работало ${count(live, 'бригада', 'бригады', 'бригад')}`);
  return parts.join('. ') + '.';
}

export function CurveScreen({ region, live }: {
  region: string; live?: { engineers: number; assigned: number; title?: string };
}) {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.summary().then(setData).catch(e => setError(String((e as Error).message)));
  }, []);

  if (error) return <div className="screen"><p className="un-reason">{error}</p></div>;
  if (!data) return <div className="screen"><p className="note">Загружаем расчёты…</p></div>;

  const points = data.curves[region] ?? [];
  const ref = data.regions.find(r => r.region === region);
  const jobs = ref?.jobs ?? Math.max(0, ...points.map(p => p.assigned + p.unassigned));

  return (
    <div className="screen">
      <h2>Сколько нужно людей</h2>
      <p className="lede">
        Один и тот же день посчитан при разном числе бригад в штате. Видно две
        границы: сколько бригад нужно, чтобы не терять заявки, и после какого
        числа добавлять людей бесполезно — лишние остаются в резерве.
      </p>
      <p className="conclusion">{curveConclusion(points, jobs, live?.engineers)}</p>

      <div className="charts two">
        <div className="chart-card">
          <div className="chart-title">Заявок закрыто</div>
          <div className="chart-sub">
            из {jobs} за день; по горизонтали — сколько бригад в штате
          </div>
          <CurveChart points={points} valueOf={p => p.assigned}
                      label="Заявок закрыто" unit="" color={ACCENT}
                      maxHint={jobs} liveFleet={live?.engineers} />
        </div>

        <div className="chart-card">
          <div className="chart-title">Бригад в работе</div>
          <div className="chart-sub">
            сколько из штата план выводит на маршруты; остальные — резерв
          </div>
          <CurveChart points={points} valueOf={p => p.used}
                      label="Бригад в работе" unit="" color={ACCENT}
                      maxHint={Math.max(...points.map(p => p.fleet), live?.engineers ?? 0)}
                      liveFleet={live?.engineers} />
        </div>
      </div>

      <table style={{ maxWidth: 760, marginTop: 14 }}>
        <thead>
          <tr>
            <th className="n">Бригад в штате</th><th className="n">В работе</th>
            <th className="n">Закрыто</th><th className="n">Без бригады</th>
            <th className="n">Пробег</th><th>Сколько считали</th>
          </tr>
        </thead>
        <tbody>
          {points.map(p => (
            <tr key={p.fleet}>
              <td className="n">{p.fleet}</td>
              <td className="n">{p.used}</td>
              <td className="n">{p.assigned}</td>
              <td className={`n ${p.unassigned ? 'no' : 'yes'}`}>{p.unassigned}</td>
              <td className="n">{p.km.toFixed(1)} км</td>
              <td>{computeTime(p.source, data.seconds_per_region)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="caveat" style={{ maxWidth: 760 }}>
        Пробег с ростом штата не падает: план сначала сокращает число бригад и
        только потом километры, поэтому лишние бригады не выходят и маршруты не
        укорачиваются. При маленьком штате пробег ниже потому, что закрыто
        меньше заявок. {data.caveat}
        {live && (
          <>
            {' '}Пунктир — реальный день: работало {count(live.engineers, 'бригада', 'бригады', 'бригад')},
            {' '}распределено {count(live.assigned, 'заявка', 'заявки', 'заявок')}.
          </>
        )}
      </div>
    </div>
  );
}

// --- цена ограничений --------------------------------------------------------

/** Слова для строк сценариев. Названия приходят из заранее посчитанного
 *  файла и местами написаны как рабочие заметки («визит = норматив минус 20»);
 *  на экране — обычными словами. Сценарий без замены показывается как есть. */
const SCENARIO_WORDS: Record<string, { title?: string; note?: string }> = {
  'Без надбавки на подход': {
    title: 'Без 10 минут на парковку и подход',
    note: 'на заявку заложено только время работы; 10 минут на парковку и подход к подъезду не добавляются',
  },
  'Опоздание до 30 минут': {
    note: 'разрешили приезжать до 30 минут позже окна клиента',
  },
  'Рабочий день 10 часов': { note: 'на два часа короче нормы в 12 часов' },
  'Рабочий день 13 часов': { note: 'на час длиннее нормы — так бывало в реальный день' },
  'Без лимита оборудования': { note: 'утренний запас оборудования у бригады не кончается' },
  'Без квалификаций': { note: 'каждая бригада умеет все три вида работ' },
  // Сценарий снимает не только зоны: все бригады в нём выезжают из офиса в
  // Москве (tools/sensitivity.py, relax_clusters). Без этой оговорки строка
  // приписала бы запрету зон цену отказа от местных баз. Отдельно запрет зон
  // меряет строка «Без запрета зон, базы на месте» (relax_zone_ban).
  'Без зон и без местных баз': {
    note: 'любая бригада едет в любой город региона, и все выезжают из офиса в Москве: '
      + 'строка меряет сразу и запрет зон, и отказ от баз в области',
  },
};

export function SensitivityScreen({ region }: { region: string }) {
  const [data, setData] = useState<Sensitivity | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.sensitivity().then(setData).catch(e => setError(String((e as Error).message)));
  }, []);

  if (error) return <div className="screen"><p className="un-reason">{error}</p></div>;
  if (!data) return <div className="screen"><p className="note">Загружаем расчёты…</p></div>;

  const rows = [...(data.regions[region] ?? [])].sort((a, b) => a.order - b.order);
  const soft = rows.some(r => r.soft_windows);

  return (
    <div className="screen">
      <h2>Цена ограничений</h2>
      <p className="lede">
        Каждая строка — тот же день, посчитанный со снятым правилом или другим
        допущением. Разница с первой строкой показывает, во что обходится правило:
        сколько бригад и километров оно стоит. Не «мы так решили», а «вот цена».
      </p>

      <div className="table-scroll" style={{ maxWidth: 980 }}>
        <table>
          <thead>
            <tr>
              <th>Что меняем</th>
              <th className="n">Бригад</th>
              <th className="n">Пробег</th>
              <th className="n">Заявок</th>
              <th>Подробнее</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const w = SCENARIO_WORDS[r.scenario] ?? {};
              return (
                <tr key={r.scenario} className={r.order === 0 ? 'hl' : ''}>
                  <td>
                    {w.title ?? r.scenario}
                    {r.soft_windows && <span className="no"> *</span>}
                  </td>
                  <td className="n nowrap">
                    {r.engineers}
                    {r.order > 0 && r.d_eng !== 0 && (
                      <span className="note"> ({signed(r.d_eng)})</span>
                    )}
                  </td>
                  <td className="n nowrap">
                    {r.km.toFixed(1)} км
                    {r.order > 0 && Math.abs(r.d_km) >= 0.05 && (
                      <span className="note"> ({signed(r.d_km, 1)})</span>
                    )}
                  </td>
                  <td className={`n nowrap ${r.assigned < r.total ? 'no' : ''}`}>
                    {r.assigned} из {r.total}
                  </td>
                  <td className="note">{w.note ?? r.note}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {soft && (
        <p className="note" style={{ maxWidth: 980 }}>* {data.soft_windows_note}</p>
      )}
      <div className="caveat" style={{ maxWidth: 980 }}>
        {data.caveat} Каждая строка посчитана {dur(data.seconds / 60)}.
      </div>
    </div>
  );
}
