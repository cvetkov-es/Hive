// Слой доступа к API. Типы повторяют контракт ТЗ 2.4.2 один в один: те же
// имена полей, что в ответе бэкенда и в выгрузке, чтобы не заводить третий
// словарь понятий между экраном и документом.

export type Stop = {
  job_id: string; seq: number; address: string; window: string;
  arrive: string; start: string; end: string;
  wait_min: number; late_min: number; km: number; travel_min: number;
  lat: number; lon: number; skill: string; priority: number; estimated: boolean;
};

export type Route = {
  engineer_id: string; engineer_name: string; transport: string; cluster: string;
  depart: string; km: number; jobs: number; travel_min: number;
  work_min: number; span_min: number; stops: Stop[];
};

export type Unassigned = {
  job_id: string; code: string; reason: string; detail: string;
  /** Первое «что можно сделать» одной фразой — та же строка, что в карточке. */
  remedy?: string;
  address: string; window: string; lat: number; lon: number;
};

/** Бригада без заявок. В `routes` её нет, а объяснить резерв можно, только
 *  если её видно. */
export type ReserveBrigade = {
  engineer_id: string; engineer_name: string; transport: string;
  cluster: string; skills: string[];
};

export type Depot = {
  key: string; name: string; lat: number; lon: number;
  cluster: string; is_office: boolean;
};

/** Замороженный событием визит: начат до события или бригада уже едет. */
export type FrozenMark = {
  engineer_id: string; start_min: number; end_min: number;
  state: 'started' | 'en_route';
};

export type PlanMeta = Record<string, unknown> & {
  at_min?: number; event?: string; mode?: 'insert' | 'replan';
  frozen?: Record<string, FrozenMark>; events?: string[];
  fleet_kept?: number; fleet_size?: number; cancelled?: string[];
  blocked_engineers?: string[];
};

export type Plan = {
  plan_id: string; region: string; algo: string;
  used_engineers: number; total_km: number; assigned: number; total_jobs: number;
  late_jobs: number; travel_min: number;
  routes: Route[]; unassigned: Unassigned[]; reserve?: ReserveBrigade[];
  depots: Depot[];
  source: string; warning: string; meta: PlanMeta;
};

export type LiveDay = {
  engineers: number; assigned: number; late: number; note: string;
  title?: string; total?: number; cancelled?: number; not_sent?: number;
  breakdown?: string;
};

export type RegionInfo = {
  name: string; jobs: number; engineers: number;
  depots: Depot[]; clusters: Record<string, number>; live_day: LiveDay;
};

export type HealthCheck = { name: string; ok: boolean; detail: string };

export type Health = {
  ok: boolean; headline: string; passed: number; total: number;
  checks: HealthCheck[];
};

export type Rejection = { engineer_id: string; code: string; reason: string };

/** Строка полной таблицы проверок. null — проверку не выполняли: бригада
 *  отсеялась раньше, и «нет» в такой клетке было бы неправдой. */
export type CheckRow = {
  engineer_id: string; transport: string; is_current: boolean;
  idle?: boolean; blocked?: boolean;
  skill_ok: boolean; transport_ok: boolean; cluster_ok: boolean;
  window_ok: boolean | null; equipment_ok: boolean; time_ok: boolean | null;
  added_km: number | null; verdict: string;
};

export type Remedy = {
  kind: string; action: string; effect: string;
  engineer_id: string | null; window: [string, string] | null;
  /** То же одной связной фразой, с подлежащим. */
  text?: string;
};

/** Что это за заявка и какие обещания клиенту она несёт. */
export type Passport = {
  job_id: string; work_kind: string; bk: string; hd: string; skill: string;
  window: string; window_start: string; window_end: string;
  window_all_day: boolean; window_text: string;
  duration_min: number; norm_min: number | null; duration_text: string;
  priority: string; priority_level: string; priority_rank: number;
  priority_text: string;
  equipment: { item: string; name: string; qty: number }[];
  equipment_text: string;
  transport_required: string | null; transport_text: string;
  address: string; zone: string; district: string; summary: string;
};

export type CheckedLine = { key: string; label: string; ok: boolean; text: string };

export type Schedule = {
  seq: number; of: number; arrive: string; start: string; end: string;
  wait_min: number; wait_text: string; late_min: number; window: string;
  travel_min: number; leg_km: number;
};

export type AssignedExplanation = {
  status: 'назначена'; job_id: string; engineer_id: string; headline: string;
  reasons: string[]; rejected: Rejection[]; rejected_summary?: string;
  travel_by_mode: Record<string, number>; used_mode: string;
  caveat: string; checks: CheckRow[];
  passport?: Passport; checked?: CheckedLine[]; schedule?: Schedule;
  added_km?: number; position?: string; frozen?: boolean;
};

export type UnassignedExplanation = {
  status: 'не назначена'; job_id: string; code: string;
  reason: string; detail: string; remedies: Remedy[];
  remedy?: string; passport?: Passport;
  blocked_by?: { engineer_id: string; kind: string; text: string }[];
};

/** Заявка, которую событие дня отменило: её нет ни в маршрутах, ни среди
 *  незакрытых, и полей назначенной у неё нет. Экран, принявший её за
 *  назначенную, упал бы целиком на чтении `travel_by_mode`. */
export type CancelledExplanation = {
  status: 'отменена'; job_id: string; headline: string;
  reason: string; detail: string;
};

export type Explanation = AssignedExplanation | UnassignedExplanation | CancelledExplanation;

/** Почему у бригады такой день (ТЗ 2.4.2: «для выбранного маршрута —
 *  краткое объяснение»). */
export type RouteExplanation = {
  plan_id: string; engineer_id: string;
  status: 'в работе' | 'в резерве' | 'выбыла';
  headline: string; summary: string[]; caveat: string;
  header: {
    engineer_id: string; name: string; transport: string; skills: string[];
    zone: string; shift: string; start_point: string;
    equipment: { item: string; name: string; qty: number }[];
    equipment_text: string;
  };
  day: {
    depart: string; finish: string; span_min: number; span_text: string;
    limit_min: number; limit_text: string; left_min: number; jobs: number;
    km: number; travel_min: number; wait_min: number; work_min: number;
    text: string;
  } | null;
  stops: {
    seq: number; job_id: string; work_kind: string; window: string;
    arrive: string; start: string; end: string; wait_min: number;
    leg_km: number; leg_min: number; frozen: boolean;
  }[];
  order: {
    windows: string; follows_windows: boolean; text: string[];
    waits: { job_id: string; wait_min: number; text: string }[];
    longest_leg: { from_job: string | null; to_job: string; km: number;
                   min: number; text: string } | null;
    swaps: { text: string } | null;
  } | null;
  constraints: { key: string; rank: number; binding: boolean; text: string }[];
  reserve: { can_do: number; taken?: number; unassigned?: string[]; text: string } | null;
  frozen_text?: string;
};

export type CompareColumn = {
  key: string; title: string; used_engineers: number; assigned: number;
  total_jobs: number; unassigned: number; cancelled?: number;
  total_km: number | null;
  late_jobs: number; travel_min: number | null; note: string;
  breakdown?: string; statuses?: Record<string, number>; about?: string;
};

export type Compare = {
  region: string; columns: CompareColumn[];
  deltas: Record<string, { used_engineers: number; assigned: number; total_km: number | null }>;
  plan_ids: Record<string, string>;
  sources: Record<string, string>;
  warnings: string[];
  control_about?: string;
};

/** Откуда взялись координаты введённого адреса и куда встала точка.
 *  Показывается диспетчеру: оценочное расстояние не должно выглядеть как
 *  измеренное, а чужой город — как нужный адрес. */
export type Geocode = {
  address: string; lat: number; lon: number; source: string; note: string;
  name?: string; km_from_depot?: number | null; depot?: string;
  /** Как посчитаны расстояния до точки: дороги от картографического сервиса
   *  или по прямой, если сервис не ответил. Поля нет, если расстояния до
   *  точки не считались. */
  roads?: 'по дорогам' | 'по прямой';
};

export type Diff = {
  event: string; frozen: number; is_quiet: boolean;
  mode?: 'insert' | 'replan'; mode_text?: string; en_route?: number;
  moved: { job_id: string; from: string; to: string }[];
  resequenced: { job_id: string; engineer_id: string; old_seq: number; new_seq: number }[];
  dropped: { job_id: string; from: string; reason: string }[];
  added: { job_id: string; to: string; seq: number }[];
  rejected?: { job_id: string; code: string; reason: string; detail: string }[];
  cancelled?: { job_id: string; from: string | null }[];
  metrics: Record<string, { before: number; after: number; delta: number }>;
};

export type EventBody = {
  kind: string; at: string;
  job_id?: string; engineer_id?: string; job?: Record<string, unknown>;
  address?: string; time_limit_s?: number;
};

export type EventResult = {
  plan: Plan; previous_plan_id: string; diff: Diff; mode?: string;
  geocode: Geocode | null;
};

export type AuditReport = {
  plan_id: string; headline: string; passed: number; total: number; ok: boolean;
  violations: string[];
  checks: { code: string; title: string; ok: boolean; problems: string[] }[];
};

export type MoveCandidate = {
  engineer_id: string; engineer_name: string; transport: string;
  is_current: boolean; idle: boolean; allowed: boolean;
  added_km: number | null; added_min: number | null; position: number | null;
  skill_ok: boolean; transport_ok: boolean; cluster_ok: boolean;
  equipment_ok: boolean; verdict: string;
};

export type CurvePoint = {
  fleet: number; used: number; km: number;
  assigned: number; unassigned: number; source: string;
};

export type Summary = {
  built_at: string; seconds_per_region: number; machine: string; caveat: string;
  regions: {
    region: string; jobs: number; fleet: number;
    plan: { engineers: number; km: number; assigned: number; late: number };
    baseline: { engineers: number; km: number; assigned: number };
  }[];
  curves: Record<string, CurvePoint[]>;
};

export type SensitivityRow = {
  scenario: string; engineers: number; km: number; assigned: number; total: number;
  late: number; audit_ok: boolean; soft_windows: boolean;
  note: string; order: number; d_eng: number; d_km: number;
};

export type Sensitivity = {
  seconds: number; caveat: string; soft_windows_note: string;
  regions: Record<string, SensitivityRow[]>;
};

/** Кривая «время счёта -> результат»: один прогон на набор данных, лучший
 *  план к каждому моменту. Файл считается на машине эталона; пока его нет,
 *  сервер отвечает 503, и экран кривую не показывает. */
export type Convergence = {
  built_at: string; machine: string; cores: number; processes: number;
  seconds_per_region: number; caveat?: string;
  regions: Record<string, {
    jobs: number; fleet: number;
    final: { engineers: number; km: number; assigned: number };
    steps: { seconds: number; engineers: number; km: number; assigned: number }[];
  }>;
  table: {
    seconds: number; mark: string;
    regions: Record<string, { engineers: number; km: number; assigned: number } | null>;
  }[];
};

export type Legs = { job_id: string; seq: number; path: [number, number][]; estimated: boolean }[];
export type Geometry = Record<string, Legs>;

class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
      else if (Array.isArray(body?.detail)) {
        // 422 от схемы запроса: берём человеческую часть сообщения, а не дамп.
        detail = body.detail.map((d: { msg?: string }) => d?.msg ?? '').filter(Boolean).join('; ');
      }
    } catch { /* тело не JSON — оставляем код ответа */ }
    if (!detail) {
      detail = res.status >= 500
        ? `сервер не смог посчитать ответ (ошибка ${res.status}). Попробуйте другое время или пересчитайте план`
        : `${res.status} ${res.statusText}`;
    }
    throw new ApiError(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) });

/** Геометрия приходит списком маршрутов; карте удобнее словарь по бригаде.
 *  live — добрать у картографического сервиса форму участков, которых нет в
 *  сохранённых картах; это секунды, поэтому отдельным вызовом. */
async function geometryOf(planId: string, live = false):
    Promise<{ byEngineer: Geometry; note: string; estimated: number }> {
  const g = await call<{ routes: { engineer_id: string; legs: Legs }[]; note?: string;
                         estimated_legs?: number }>(
    `/api/geometry/${planId}${live ? '?live=1' : ''}`);
  const byEngineer: Geometry = {};
  for (const r of g.routes ?? []) byEngineer[r.engineer_id] = r.legs;
  return { byEngineer, note: g.note ?? '', estimated: g.estimated_legs ?? 0 };
}

export const api = {
  regions: () => call<RegionInfo[]>('/api/regions'),

  plan: (region: string, opts: { algo?: string; keep?: number; recompute?: boolean;
                                 time_limit_s?: number } = {}) =>
    post<Plan>('/api/plan', { region, ...opts }),

  savedPlan: (planId: string) => call<Plan>(`/api/plan/${planId}`),

  compare: (region: string) => call<Compare>(`/api/compare?region=${encodeURIComponent(region)}`),

  explain: (planId: string, jobId: string) =>
    call<Explanation>(`/api/explain/${planId}/${encodeURIComponent(jobId)}`),

  explainRoute: (planId: string, engineerId: string) =>
    call<RouteExplanation>(`/api/explain_route/${planId}/${encodeURIComponent(engineerId)}`),

  audit: (planId: string) => call<AuditReport>(`/api/audit/${planId}`),

  geometry: geometryOf,

  event: (body: EventBody & { plan_id: string }) => post<EventResult>('/api/event', body),

  validateMove: (planId: string, jobId: string) =>
    post<{ job_id: string; locked?: string | null; candidates: MoveCandidate[] }>(
      '/api/validate_move', { plan_id: planId, job_id: jobId }),

  move: (planId: string, jobId: string, engineerId: string) =>
    post<{ plan: Plan; previous_plan_id: string; added_km: number; position: number }>(
      '/api/move', { plan_id: planId, job_id: jobId, engineer_id: engineerId }),

  summary: () => call<Summary>('/api/summary'),
  sensitivity: () => call<Sensitivity>('/api/sensitivity'),
  convergence: () => call<Convergence>('/api/convergence'),
  health: () => call<Health>('/api/health'),

  exportUrl: (planId: string) => `/api/export/${planId}`,
};
