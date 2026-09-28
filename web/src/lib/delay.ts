// «Если каждая работа затянется на N минут» — пересчёт по готовому плану,
// маршруты заново не строятся.
//
// Задержка идёт каскадом: работа длится на N минут дольше, поэтому бригада
// приезжает на следующий адрес позже — на столько, на сколько позже
// закончила предыдущую работу. Но если бригада и так приезжала раньше окна
// клиента и ждала, ожидание съедает задержку: начать раньше открытия окна
// она всё равно не могла. Простой сдвиг i-й заявки на i×N минут ожидания
// не видит — на ленте блок «проезжал» бы сквозь собственное ожидание.
//
// Зазор между концом одной работы и приездом на следующую берётся из плана
// как есть: в нём уже дорога и надбавка на подход.
//
// После события прошлое не затягивается: работы, начатые до момента события
// (fromMin), остаются как в плане, задержка копится с первой не начатой.

import { toMin, windowOf } from './time.ts';

export type RouteLike = {
  engineer_id: string;
  depart: string;
  span_min: number;
  stops: { job_id: string; arrive: string; start: string; end: string; window: string }[];
};

export type DelayedStop = {
  job_id: string;
  arrive: number;     // новый приезд, минуты от полуночи
  start: number;      // новое начало
  end: number;        // новый конец
  shift: number;      // насколько позже плана начинается работа
  late: boolean;      // начало позже закрытия окна клиента
};

export type DelayedRoute = {
  engineer_id: string;
  stops: DelayedStop[];
  span: number;       // от выезда до конца последней работы
  overtime: boolean;  // день длиннее норматива 12 ч
  overBy: number;     // на сколько минут длиннее
};

export const NORM_MIN = 12 * 60;

export function cascadeRoute(route: RouteLike, delay: number, fromMin = -Infinity): DelayedRoute {
  const depart = toMin(route.depart);
  let prevEndPlan = depart;
  let prevEndNew = depart;
  const stops: DelayedStop[] = route.stops.map(s => {
    const arrive = toMin(s.arrive), start = toMin(s.start), end = toMin(s.end);
    const [, close] = windowOf(s.window);
    if (start < fromMin) {
      prevEndPlan = end;
      prevEndNew = end;
      return { job_id: s.job_id, arrive, start, end, shift: 0, late: start > close };
    }
    const arriveNew = prevEndNew + (arrive - prevEndPlan);
    const startNew = Math.max(arriveNew, start);
    const endNew = startNew + (end - start) + delay;
    prevEndPlan = end;
    prevEndNew = endNew;
    return { job_id: s.job_id, arrive: arriveNew, start: startNew, end: endNew,
             shift: startNew - start, late: startNew > close };
  });
  const span = stops.length ? prevEndNew - depart : 0;
  return {
    engineer_id: route.engineer_id, stops, span,
    overtime: span > NORM_MIN, overBy: Math.max(0, span - NORM_MIN),
  };
}

export type DelayRisk = {
  late: number;                                  // заявок за окном клиента
  overtime: number;                              // бригад сверх 12 ч
  byEngineer: { id: string; late: number }[];    // кто опоздает первым
  routes: Record<string, DelayedRoute>;
};

export function delayRisk(routes: RouteLike[], delay: number, fromMin?: number): DelayRisk {
  let late = 0, overtime = 0;
  const byEngineer: { id: string; late: number }[] = [];
  const out: Record<string, DelayedRoute> = {};
  for (const r of routes) {
    if (!r.stops.length) continue;
    const d = cascadeRoute(r, delay, fromMin);
    out[r.engineer_id] = d;
    const n = d.stops.filter(s => s.late).length;
    late += n;
    if (d.overtime) overtime += 1;
    if (n) byEngineer.push({ id: r.engineer_id, late: n });
  }
  byEngineer.sort((a, b) => b.late - a.late || a.id.localeCompare(b.id, 'ru', { numeric: true }));
  return { late, overtime, byEngineer, routes: out };
}
