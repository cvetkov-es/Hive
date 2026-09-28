// «Упор в норматив 12 ч»: бригады, у которых день уже не меньше 11 часов.
//
// Такая бригада выглядит свободной по числу заявок и при этом не возьмёт ни
// одной новой. Именно это чаще географии решает, сколько нужно людей, и
// именно эти бригады первыми выйдут за норматив, если день пойдёт не по плану.

export const NORM_MIN = 12 * 60;
export const NEAR_FROM_MIN = 11 * 60;

export function nearLimit(
  routes: { engineer_id: string; span_min: number; stops: unknown[] }[],
  from = NEAR_FROM_MIN,
): { id: string; span: number; left: number }[] {
  return routes
    .filter(r => r.stops.length > 0 && r.span_min >= from)
    .sort((a, b) => b.span_min - a.span_min)
    .map(r => ({ id: r.engineer_id, span: r.span_min, left: NORM_MIN - r.span_min }));
}
