// Время на экране — всегда ЧЧ:ММ, внутри расчётов — минуты от полуночи.
// Отдельно от store.ts: чистые функции из lib/ проверяются тестами под Node,
// а store тянет за собой React.

/** «09:30» -> 570. */
export function toMin(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + (m || 0);
}

/** 570 -> «09:30». */
export function hhmm(min: number): string {
  const h = Math.floor(min / 60) % 24;
  return `${String(h).padStart(2, '0')}:${String(Math.round(min) % 60).padStart(2, '0')}`;
}

/** Окно клиента «10:00-12:00» (дефис или тире) -> [600, 720]. */
export function windowOf(win: string): [number, number] {
  const [a, b] = win.split(/[-–]/).map(s => s.trim());
  return [toMin(a), toMin(b)];
}
