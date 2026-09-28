// Поиск по плану: номер заявки, адрес, бригада.
//
// Диспетчеру звонит клиент и называет номер или адрес — нужно за секунду
// найти, у кого заявка и когда к нему приедут. Ищем по тому, что уже пришло
// с планом, без запросов к серверу.
//
// Порядок выдачи — по тому, насколько точно совпало: номер заявки целиком,
// затем бригады, затем номера заявок частично, затем фамилии и адреса.
// «Ё» и «е» не различаются: в выгрузке адреса написаны и так, и так.

import { plural } from '../text.ts';

export type SearchHit =
  | { kind: 'engineer'; id: string; title: string; meta: string }
  | { kind: 'job'; id: string; engineerId: string | null; title: string; meta: string; address: string };

type PlanLike = {
  routes: {
    engineer_id: string; engineer_name: string; transport: string; jobs: number;
    stops: { job_id: string; address: string; start: string }[];
  }[];
  reserve?: { engineer_id: string; engineer_name: string; transport: string; cluster: string }[];
  unassigned: { job_id: string; address: string; window: string }[];
};

const norm = (s: string) => s.toLowerCase().replace(/ё/g, 'е').trim();

export function searchPlan(plan: PlanLike, query: string, limit = 8): SearchHit[] {
  const q = norm(query);
  if (q.length < 2) return [];
  const scored: { score: number; order: number; hit: SearchHit }[] = [];
  let order = 0;
  const add = (score: number, hit: SearchHit) => scored.push({ score, order: order++, hit });

  const brigade = (id: string, name: string, meta: string) => {
    const nid = norm(id), short = nid.replace(/^br-/, '');
    const score = nid === q || short === q ? 0
      : nid.includes(q) ? 1
      : norm(name).includes(q) ? 3 : -1;
    if (score >= 0) add(score, { kind: 'engineer', id, title: `${id} · ${name}`, meta });
  };
  const job = (id: string, engineerId: string | null, address: string, meta: string) => {
    const nid = norm(id);
    const score = nid === q ? 0 : nid.includes(q) ? 2 : norm(address).includes(q) ? 4 : -1;
    if (score >= 0) add(score, { kind: 'job', id, engineerId, title: `Заявка ${id}`, meta, address });
  };

  for (const r of plan.routes) {
    brigade(r.engineer_id, r.engineer_name,
            `${r.transport} · ${r.jobs} ${plural(r.jobs, 'заявка', 'заявки', 'заявок')}`);
  }
  for (const r of plan.reserve ?? []) {
    brigade(r.engineer_id, r.engineer_name, `резерв · ${r.transport} · зона «${r.cluster}»`);
  }
  for (const r of plan.routes) {
    for (const s of r.stops) job(s.job_id, r.engineer_id, s.address, `${r.engineer_id} · начало ${s.start}`);
  }
  for (const u of plan.unassigned) {
    job(u.job_id, null, u.address, `не назначена · окно ${u.window.replace('-', '–')}`);
  }

  return scored
    .sort((a, b) => a.score - b.score || a.order - b.order)
    .slice(0, limit)
    .map(x => x.hit);
}
