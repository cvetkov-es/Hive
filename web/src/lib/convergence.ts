// Кривая «время счёта -> результат».
//
// План дня считается заранее, по 10 минут на набор данных, а диспетчер столько
// не ждёт: событие перепланируется за 10 с, кнопка «Пересчитать» — за 20 с.
// Естественный вопрос — что теряется при коротком счёте. Ответ — замер
// одного прогона (tools/convergence.py): решатель сообщает о каждом плане, и
// значение в момент t — лучший план, найденный к этому моменту.
//
// Время растёт на порядки (доли секунды — 10 минут), поэтому шкала
// логарифмическая: на линейной первые 20 с сжались бы в полтора процента ширины.

import { count, plural } from '../text.ts';

export type Step = { seconds: number; engineers: number; km: number; assigned: number };

/** Сколько ждёт интерфейс и откуда это число. */
export const MOMENTS: { seconds: number; what: string }[] = [
  { seconds: 10, what: 'столько считается событие дня' },
  { seconds: 20, what: 'кнопка «Пересчитать»' },
  { seconds: 600, what: 'столько считали план дня' },
];

/** Лучший план к моменту t; null — к t решения ещё не было. */
export function bestAt(steps: Step[], t: number): Step | null {
  let best: Step | null = null;
  for (const s of steps) {
    if (s.seconds > t) break;
    best = s;
  }
  return best;
}

/** Когда впервые закрыты все заявки; null — не закрыты и к концу счёта. */
export function allClosedAt(steps: Step[], jobs: number): number | null {
  return steps.find(s => s.assigned >= jobs)?.seconds ?? null;
}

/** 0.07 -> «0.07 с», 4.3 -> «4.3 с», 18.1 -> «18 с», 150 -> «2 мин 30 с». */
export function secondsLabel(s: number): string {
  if (s < 1) return `${Number(s.toFixed(2))} с`;
  if (s < 10) return `${Number(s.toFixed(1))} с`;
  if (s < 60) return `${Math.round(s)} с`;
  const m = Math.floor(s / 60), rest = Math.round(s % 60);
  return rest ? `${m} мин ${rest} с` : `${m} мин`;
}

/** Позиция на логарифмической шкале [min, max] длиной size; за краем — край. */
export function logScale(v: number, min: number, max: number, size: number): number {
  const x = (Math.log(v / min) / Math.log(max / min)) * size;
  return Math.min(size, Math.max(0, x));
}

const TICKS = [0.1, 1, 10, 60, 600];

/** Деления оси времени, попавшие в шкалу. */
export function timeTicks(min: number, max: number): { seconds: number; label: string }[] {
  return TICKS.filter(t => t >= min && t <= max).map(t => ({ seconds: t, label: secondsLabel(t) }));
}

/** Точки ступенчатой линии: значение держится до следующего плана, а
 *  последнее — до конца счёта. */
export function stepLine(steps: Step[], valueOf: (s: Step) => number,
                         horizon: number): [number, number][] {
  const pts: [number, number][] = [];
  steps.forEach((s, i) => {
    if (i) pts.push([s.seconds, valueOf(steps[i - 1])]);
    pts.push([s.seconds, valueOf(s)]);
  });
  if (steps.length) pts.push([horizon, valueOf(steps[steps.length - 1])]);
  return pts;
}

function state(s: Step | null, jobs: number): string {
  if (!s) return 'плана ещё нет';
  const closed = s.assigned < jobs ? `закрыто ${s.assigned} из ${jobs}, ` : '';
  return `${closed}${count(s.engineers, 'бригада', 'бригады', 'бригад')}, ${s.km.toFixed(1)} км`;
}

/** Вывод над графиком — из данных: когда закрыты все заявки и каким план
 *  успевает стать к каждому ожиданию интерфейса. */
export function conclusion(steps: Step[], jobs: number, horizon: number): string {
  const closedAt = allClosedAt(steps, jobs);
  const last = bestAt(steps, horizon);
  const first = closedAt !== null
    ? `Все ${jobs} ${plural(jobs, 'заявка', 'заявки', 'заявок')} закрыты через ${secondsLabel(closedAt)}`
    : `За ${secondsLabel(horizon)} закрыто заявок: ${last?.assigned ?? 0} из ${jobs}`;

  const moments = MOMENTS.filter(m => m.seconds <= horizon);
  if (!moments.some(m => m.seconds === horizon)) moments.push({ seconds: horizon, what: '' });

  let prev: Step | null | undefined;
  const parts = moments.map(m => {
    const s = bestAt(steps, m.seconds);
    const when = `за ${secondsLabel(m.seconds)}${m.what ? ` (${m.what})` : ''}`;
    const same = prev !== undefined && prev !== null && s !== null
      && prev.engineers === s.engineers && prev.assigned === s.assigned
      && Math.abs(prev.km - s.km) < 0.05;
    prev = s;
    return `${when} — ${same ? 'без изменений' : state(s, jobs)}`;
  });
  const rest = parts.join('; ');
  return `${first}. ${rest.charAt(0).toUpperCase()}${rest.slice(1)}.`;
}
