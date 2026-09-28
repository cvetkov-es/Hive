// Состояние пульта.
//
// Одно правило держит всё остальное: планы ИММУТАБЕЛЬНЫ и адресуются
// идентификатором. Событие не меняет план, а порождает новый, и предыдущий
// остаётся — без этого разницу «до и после» показывать не с чем.
//
// Сравнение и объяснения тоже идут за планом по его идентификатору, а не
// пересчитываются: пересчёт того же региона даёт другой план, и разница
// превратилась бы в разницу между двумя случайностями.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api, type AuditReport, type Diff, type EventBody, type Explanation, type Geocode,
  type Geometry, type Health, type Plan, type RegionInfo, type RouteExplanation,
} from './api';
import { markOf, type Mark } from './colors';
import { count } from './text';

export type Selection =
  | { kind: 'none' }
  | { kind: 'job'; jobId: string }
  | { kind: 'engineer'; engineerId: string };

export type Screen = 'board' | 'compare' | 'curve' | 'sensitivity';

export type BoardState = {
  regions: RegionInfo[];
  region: string;
  plan: Plan | null;
  previous: Plan | null;       // план до события — для разницы «до и после»
  diff: Diff | null;
  geocode: Geocode | null;     // куда встала точка новой заявки
  audit: AuditReport | null;
  geometry: Geometry | null;
  geometryNote: string;
  previousGeometry: Geometry | null;   // форма маршрутов ДО события — для призрака
  marks: Record<string, Mark>;
  selection: Selection;
  screen: Screen;
  busy: string;                 // что сейчас считается, пустая строка — ничего
  busyLimit: number;            // сколько секунд дано планировщику; 0 — быстрый запрос
  busySince: number;
  error: string;
  health: Health | null;        // самопроверка окружения: предупреждение, не отказ
  keep: number;                 // урезанный парк; 0 — весь
  fitSeq: number;               // сменилось — карта заново показывает весь регион
};

const NONE: Selection = { kind: 'none' };

/** Метки бригад фиксируются на регион: цвет следует за бригадой, а не за её
 *  местом в очередном плане. Иначе урезание парка или событие перекрашивали
 *  бы выживших, и легенда переставала бы совпадать с тем, что видел диспетчер
 *  минуту назад. Резерв тоже получает метку: он выбирается в списке. */
function marksFor(plan: Plan, prev: Record<string, Mark> | null): Record<string, Mark> {
  const ids = [
    ...plan.routes.map(r => r.engineer_id),
    ...(plan.reserve ?? []).map(r => r.engineer_id),
  ].sort((a, b) => a.localeCompare(b, 'ru', { numeric: true }));
  const out: Record<string, Mark> = { ...(prev ?? {}) };
  let next = Object.keys(out).length;
  for (const id of ids) if (!out[id]) out[id] = markOf(next++);
  return out;
}

export function useBoard() {
  const [s, setS] = useState<BoardState>({
    regions: [], region: '', plan: null, previous: null, diff: null, geocode: null,
    audit: null, geometry: null, geometryNote: '', previousGeometry: null,
    marks: {}, selection: NONE, screen: 'board', busy: 'Загружаем регионы',
    busyLimit: 0, busySince: 0, error: '', health: null, keep: 0, fitSeq: 0,
  });
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [explainError, setExplainError] = useState('');
  const [routeExplanation, setRouteExplanation] = useState<RouteExplanation | null>(null);
  const explainFor = useRef('');
  const routeFor = useRef('');

  const patch = useCallback((p: Partial<BoardState>) => setS(v => ({ ...v, ...p })), []);

  // Аудит и геометрия догружаются вслед за планом. План показывается сразу:
  // плашка аудита появится через мгновение, но карта не должна её ждать.
  const adoptPlan = useCallback(async (
    plan: Plan,
    extra: { keep?: number; region?: string; diff?: Diff | null; previous?: Plan | null;
             geocode?: Geocode | null; refit?: boolean; select?: Selection } = {},
  ) => {
    setS(v => ({
      ...v, plan, busy: '', busyLimit: 0, error: '',
      region: extra.region ?? v.region,
      keep: extra.keep ?? v.keep,
      diff: extra.diff ?? null,
      previous: extra.previous ?? null,
      geocode: extra.geocode ?? null,
      marks: marksFor(plan, v.plan?.region === plan.region ? v.marks : null),
      selection: extra.select ?? v.selection,
      audit: null, geometry: null, geometryNote: '', previousGeometry: null,
      fitSeq: extra.refit ? v.fitSeq + 1 : v.fitSeq,
    }));
    // Проверка плана и форма дорог приходят независимо: живой запрос формы
    // идёт секунды, и плашка проверки его не ждёт.
    api.audit(plan.plan_id).then(
      audit => setS(v => (v.plan?.plan_id === plan.plan_id ? { ...v, audit } : v)),
      () => {});
    const [geo, prevGeo] = await Promise.allSettled([
      api.geometry(plan.plan_id),
      extra.previous ? api.geometry(extra.previous.plan_id) : Promise.resolve(null),
    ]);
    const g = geo.status === 'fulfilled' ? geo.value : null;
    setS(v => {
      if (v.plan?.plan_id !== plan.plan_id) return v;   // пришёл уже другой план
      const pg = prevGeo.status === 'fulfilled' ? prevGeo.value : null;
      return {
        ...v,
        geometry: g ? g.byEngineer : null,
        geometryNote: g?.note ?? '',
        previousGeometry: pg ? pg.byEngineer : null,
      };
    });
    // Участков нового плана нет в сохранённых картах — форму дороги для них
    // добираем у картографического сервиса. Сохранённая уже на карте, эти
    // до ответа рисуются пунктиром; без сети пунктир и остаётся.
    if (g && g.estimated > 0) {
      const live = await api.geometry(plan.plan_id, true).catch(() => null);
      if (live) {
        setS(v => (v.plan?.plan_id === plan.plan_id
          ? { ...v, geometry: live.byEngineer, geometryNote: live.note } : v));
      }
    }
  }, []);

  // --- загрузка плана ------------------------------------------------------
  //
  // Живой расчёт занимает ровно столько, сколько дано планировщику, — это
  // известно заранее и показывается полосой (busyLimit). Эталон из архива
  // приходит мгновенно, и затемнять ради него экран незачем.
  const loadPlan = useCallback(async (
    region: string,
    opts: { keep?: number; recompute?: boolean; algo?: string; seconds?: number } = {},
  ) => {
    const keep = opts.keep ?? 0;
    const limit = opts.recompute ? (opts.seconds ?? 20) : keep ? 10 : 0;
    patch({
      busy: opts.recompute ? 'Считаем план заново'
        : keep ? `Считаем план для ${count(keep, 'бригады', 'бригад', 'бригад')}`
        : 'Загружаем план',
      busyLimit: limit, busySince: Date.now(),
      error: '', selection: NONE, diff: null, previous: null, geocode: null,
    });
    setExplanation(null);
    try {
      const plan = await api.plan(region, {
        algo: opts.algo, recompute: opts.recompute, keep, time_limit_s: limit || 10,
      });
      await adoptPlan(plan, { keep, region, refit: true, select: NONE });
    } catch (e) {
      patch({ busy: '', busyLimit: 0, error: String((e as Error).message) });
    }
  }, []);

  /** Событие дня. Ошибку возвращает строкой, а не кладёт в общую ошибку
   *  пульта: её показывает сама форма события, рядом со своими полями. */
  const runEvent = useCallback(async (plan: Plan, body: EventBody,
                                      progress = true): Promise<string> => {
    const limit = body.time_limit_s ?? 10;
    patch({
      busy: progress ? 'Перепланируем остаток дня' : 'Ищем свободный интервал',
      busyLimit: progress ? limit : 0, busySince: Date.now(), error: '',
    });
    try {
      const r = await api.event({ plan_id: plan.plan_id, ...body, time_limit_s: limit });
      await adoptPlan(r.plan, {
        diff: r.diff, previous: plan, geocode: r.geocode, refit: true, select: NONE,
      });
      return '';
    } catch (e) {
      patch({ busy: '', busyLimit: 0 });
      return String((e as Error).message);
    }
  }, []);

  // --- первый запуск -------------------------------------------------------
  //
  // Самопроверка идёт отдельной ветвью и её отказ ничего не останавливает:
  // она существует, чтобы предупреждать, и предупреждение, роняющее старт,
  // хуже отсутствия предупреждения.
  useEffect(() => {
    api.health().then(health => setS(v => ({ ...v, health }))).catch(() => {});
    (async () => {
      try {
        const regions = await api.regions();
        const first = regions[0]?.name ?? '';
        setS(v => ({ ...v, regions, region: first }));
        if (first) await loadPlan(first);
      } catch (e) {
        patch({ busy: '', error: `Бэкенд не отвечает: ${(e as Error).message}` });
      }
    })();
  }, []);

  // --- объяснение выбранной заявки ----------------------------------------
  useEffect(() => {
    const plan = s.plan;
    if (!plan || s.selection.kind !== 'job') {
      explainFor.current = '';
      setExplanation(null); setExplainError('');
      return;
    }
    const key = `${plan.plan_id}:${s.selection.jobId}`;
    if (explainFor.current === key) return;
    explainFor.current = key;
    setExplanation(null); setExplainError('');
    api.explain(plan.plan_id, s.selection.jobId)
      .then(e => { if (explainFor.current === key) setExplanation(e); })
      .catch(e => { if (explainFor.current === key) setExplainError(String((e as Error).message)); });
  }, [s.plan?.plan_id, s.selection]);

  // --- объяснение маршрута выбранной бригады -------------------------------
  useEffect(() => {
    const plan = s.plan;
    if (!plan || s.selection.kind !== 'engineer') {
      routeFor.current = '';
      setRouteExplanation(null);
      return;
    }
    const key = `${plan.plan_id}:${s.selection.engineerId}`;
    if (routeFor.current === key) return;
    routeFor.current = key;
    setRouteExplanation(null); setExplainError('');
    api.explainRoute(plan.plan_id, s.selection.engineerId)
      .then(e => { if (routeFor.current === key) setRouteExplanation(e); })
      .catch(e => { if (routeFor.current === key) setExplainError(String((e as Error).message)); });
  }, [s.plan?.plan_id, s.selection]);

  // --- производные ---------------------------------------------------------
  const jobOwner = useMemo(() => {
    const m: Record<string, { engineerId: string; seq: number }> = {};
    for (const r of s.plan?.routes ?? []) {
      for (const st of r.stops) m[st.job_id] = { engineerId: r.engineer_id, seq: st.seq };
    }
    return m;
  }, [s.plan]);

  const selectedEngineer = useMemo(() => {
    if (s.selection.kind === 'engineer') return s.selection.engineerId;
    if (s.selection.kind === 'job') return jobOwner[s.selection.jobId]?.engineerId ?? null;
    return null;
  }, [s.selection, jobOwner]);

  return {
    s, patch, explanation, explainError, routeExplanation, jobOwner, selectedEngineer,
    loadPlan, adoptPlan, runEvent,
    select: (sel: Selection) => patch({ selection: sel }),
    setScreen: (screen: Screen) => patch({ screen }),
  };
}

export type Board = ReturnType<typeof useBoard>;

/** «09:30» -> 570. Время наружу всегда HH:MM, внутри — минуты от полуночи. */
export function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + (m || 0);
}

export function hhmm(min: number): string {
  const h = Math.floor(min / 60) % 24;
  return `${String(h).padStart(2, '0')}:${String(Math.round(min) % 60).padStart(2, '0')}`;
}

export function hours(min: number): string {
  return `${Math.floor(min / 60)} ч ${String(min % 60).padStart(2, '0')} мин`;
}
