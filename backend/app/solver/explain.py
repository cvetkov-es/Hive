# -*- coding: utf-8 -*-
"""Почему заявка досталась этому исполнителю — и что делать с теми, кому
исполнителя не нашлось.

ТЗ 2.1.7 требует объяснить решение. Постановщик отдельно назвал антипаттерном
«портянку»: «довольно большое количество параметров… читать исполнитель не
будет». Поэтому ограничена длина, а не наличие (Р3): заголовок плюс одна-три
причины по умолчанию, полная таблица проверок — отдельным полем, за кнопкой.

Два правила, без которых объяснение вредит больше, чем помогает.

Первое: объяснение не имеет права расходиться с планом. Оно не пересчитывает
решение и не гадает — оно читает готовый маршрут и проверяет его теми же
правилами допустимости (feasibility.py), которыми пользовался планировщик.

Второе: сравнение с альтернативами верно только при неизменных маршрутах
остальных бригад. Мы считаем, во что заявка обошлась бы другой бригаде, НЕ
перестраивая её день. Эта оговорка едет в поле `caveat` и обязана быть на
экране, а не в устном комментарии: без неё диспетчер прочитает «дешевле у
Петрова» как «надо было отдать Петрову», хотя перестройка дня Петрова могла
бы стоить дороже всей экономии.

Когда соперник дешевле по километрам, фраза «ради плана целиком» ничего не
объясняет. Мы считаем, что именно изменится в плане, если заявку отдать ему:
выйдет ли на линию ещё одна бригада, сдвинутся ли аварии. Это те же три
слоя, из которых складывается оценка плана (weights.py), поэтому объяснение
называет настоящую причину, а не правдоподобную.

Цена вопроса для неназначенной заявки — прямое следствие Р5: окно двигать
может только человек, но система обязана показать, что именно это даст.
Каждое средство здесь ПОСЧИТАНО: «сдвиньте окно» без имени того, кто тогда
возьмёт заявку, — это не помощь, а перекладывание работы на диспетчера.

После события день бригады делится на прошлое и будущее (replan.py). Всё,
что здесь пробует вставить заявку в чужой маршрут, работает только с
будущим: бригада стоит там, где закончила последнюю начатую работу, и раньше
события тронуться не может. Иначе объяснение предлагало бы отдать заявку
бригаде «в 10:15», когда на часах 15:40.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace

from ..config import (APPROACH_MIN, EQUIPMENT_RU, HARD_SHIFT_END,
                      MAX_WORKDAY_MIN, NORM_ROAD_MIN, SHIFT_WINDOW, SKILL_RU,
                      TRANSPORT_RU)
from ..io.normatives import classify
from ..io.spec_format import PRIORITY_RU as PRIORITY_TZ, spec_priority
from ..models import Engineer, Unassigned, hhmm
from . import weights as W
from .feasibility import (Code, REASON_RU, Verdict, check_equipment,
                          check_profile, check_static, route_fits)
from .metrics import replay_route

CAVEAT = ("сравнение с альтернативами верно при неизменных маршрутах "
          "остальных бригад")
MAX_REASONS = 3
MAX_REJECTED = 3

# Бригада выбыла по событию «исполнитель недоступен» — код строки «Кто ещё
# рассматривался». Причиной неназначения он не бывает: там это NO_ENGINEER.
UNAVAILABLE = "ENGINEER_UNAVAILABLE"

# Порядок разбора отклонённых: сначала те, кто почти подошёл. «Нет навыка» —
# верный, но бесполезный ответ, таких бригад в регионе половина. Диспетчеру
# нужны близкие промахи: не хватило железа, не хватило времени.
REJECT_ORDER = {Code.TIME: 0, Code.EQUIPMENT: 1, UNAVAILABLE: 2,
                Code.SHIFT_WINDOW: 3, Code.TRANSPORT: 4, Code.SKILL: 5,
                Code.CLUSTER: 6}

EPS_KM = 0.005                     # километры сравниваются с точностью до 5 м

# --- слова -------------------------------------------------------------------

SKILL_ORDER = ("emergency", "connect", "local")
SKILL_SHORT = {"emergency": "аварийные", "connect": "подключения",
               "local": "локальные"}
PRIORITY_LEVEL = {1: "авария", 2: "подключение", 3: "ремонт и дозаказ"}
KIND_BY_SKILL = {"emergency": "Авария", "connect": "Подключение",
                 "local": "Локальная заявка / ремонт"}
TRANSPORT_NEED = {"car": "автомобиль", "bike": "велосипед",
                  "transit": "общественный транспорт", "foot": "пеший ход"}
TRANSPORT_GEN = {"car": "автомобиля", "bike": "велосипеда",
                 "transit": "общественного транспорта", "foot": "пешего хода"}
TRANSPORT_HAS = {"car": "бригада на автомобиле", "bike": "бригада на велосипеде",
                 "transit": "бригада на общественном транспорте",
                 "foot": "бригада ходит пешком"}
TRANSPORT_ON = {"car": "на автомобиле", "bike": "на велосипеде",
                "transit": "на общественном транспорте", "foot": "пешком"}
EQUIPMENT_WITH = {"router": "роутером", "tvbox": "ТВ-приставкой",
                  "speaker": "умной колонкой"}

# Вес аварии в оценке плана, словами: сколько километров пути планировщик
# готов проехать, чтобы начать аварию на 10 минут раньше.
ASAP_KM_PER_10_MIN = W.EMERGENCY_ASAP_PENALTY_PER_MIN * 10 / 1000
ASAP_TEXT = (f"аварии планировщик ставит как можно раньше — 10 минут задержки "
             f"аварии весят для него как {ASAP_KM_PER_10_MIN:g} км пути")


def _plural(n: int, one: str, few: str, many: str) -> str:
    k = abs(int(n)) % 100
    if 11 <= k <= 19:
        return many
    k %= 10
    return one if k == 1 else few if 2 <= k <= 4 else many


def _count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {_plural(n, one, few, many)}"


def _dur(minutes) -> str:
    """259 -> «4 ч 19 мин», 45 -> «45 мин», 120 -> «2 ч»."""
    m = int(round(minutes))
    sign = "минус " if m < 0 else ""
    h, mm = divmod(abs(m), 60)
    if h and mm:
        return f"{sign}{h} ч {mm} мин"
    return f"{sign}{h} ч" if h else f"{sign}{mm} мин"


def _km(x: float) -> str:
    return f"{x:.2f}"


def window_str(job) -> str:
    """Окно клиента «ЧЧ:ММ–ЧЧ:ММ» — с тире, как его пишут люди."""
    return f"{hhmm(job.win_start)}–{hhmm(job.win_end)}"


def window_phrase(job) -> str:
    return window_str(job) + (" (весь день)" if job.floating_window else "")


def _join(items) -> str:
    items = [str(x) for x in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


def _ids(ids, limit: int = 3) -> str:
    """«BR-1, BR-2 и BR-3» либо «BR-1, BR-2, BR-3 и ещё 4»."""
    ids = list(ids)
    if len(ids) <= limit:
        return _join(ids)
    return ", ".join(ids[:limit]) + f" и ещё {len(ids) - limit}"


def _items(names) -> str:
    return _join(f"«{n}»" for n in names)


def _km_delta(added: float) -> str:
    """Прирост пробега словами. Он бывает ОТРИЦАТЕЛЬНЫМ, и это не ошибка:
    дорожная матрица несимметрична и неравенство треугольника в ней не
    выполняется (односторонние улицы, развороты), поэтому заезд по пути иногда
    короче, чем проезд мимо. Печатать такое как «+-0.73 км» нельзя."""
    if added < -EPS_KM:
        return f"сократила маршрут на {_km(abs(added))} км"
    return f"добавила {_km(added)} км к маршруту"


def _cost_phrase(added: float, would: bool = False) -> str:
    """«добавил 0.26 км» / «сократил бы маршрут на 0.73 км»."""
    b = " бы" if would else ""
    if added < -EPS_KM:
        return f"сократил{b} маршрут на {_km(abs(added))} км"
    return f"добавил{b} {_km(added)} км"


# --- структуры ответа ---------------------------------------------------------

@dataclass
class Rejection:
    engineer_id: str
    code: str
    reason: str


@dataclass
class Explanation:
    job_id: str
    engineer_id: str
    headline: str
    reasons: list
    rejected: list
    travel_by_mode: dict
    used_mode: str
    caveat: str = CAVEAT
    checks: list = field(default_factory=list)
    passport: dict = field(default_factory=dict)
    checked: list = field(default_factory=list)
    schedule: dict = field(default_factory=dict)
    added_km: float = 0.0
    position_text: str = ""
    rejected_summary: str = ""
    frozen: bool = False


@dataclass
class Remedy:
    kind: str                     # window | equipment | skill | transport | fleet | none
    action: str
    effect: str
    engineer_id: str | None = None
    window: tuple | None = None
    text: str = ""                # то же одной связной фразой, с подлежащим


@dataclass
class Unassignment:
    job_id: str
    code: str
    reason: str
    detail: str
    remedies: list = field(default_factory=list)
    passport: dict = field(default_factory=dict)
    blocked_by: list = field(default_factory=list)
    remedy: str = ""


# --- паспорт заявки -----------------------------------------------------------

def work_kind(job) -> str:
    """Вид работ словами диспетчера. Из выгрузки — по тем же правилам, по
    которым из неё выведены навык и норматив; для заявки, введённой вручную
    в формате ТЗ, типов BK/HD нет, и остаётся навык."""
    if job.bk or job.hd:
        return classify(job.bk, job.hd, False, job.id)["work_kind"]
    return KIND_BY_SKILL.get(job.skill, SKILL_RU.get(job.skill, job.skill))


def norm_min(job) -> int | None:
    """Норматив из Нормативы.xlsx, если длительность заявки выведена из него.
    Для заявки с длительностью, заданной человеком, норматива мы не знаем и
    не выдумываем."""
    if not (job.bk or job.hd):
        return None
    if classify(job.bk, job.hd, False, job.id)["service_min"] != job.service_min:
        return None
    return job.service_min + NORM_ROAD_MIN - APPROACH_MIN


def passport(job) -> dict:
    """Что это за заявка и какие обещания клиенту она несёт. Без этого
    объяснение не читается вслух: «почему у этой бригады» бессмысленно, пока
    не сказано, что за работа, когда клиент ждёт и сколько она займёт."""
    tz = spec_priority(job.priority)
    level = PRIORITY_LEVEL.get(job.priority, PRIORITY_LEVEL[3])
    norm = norm_min(job)
    kind = work_kind(job)
    eq = [dict(item=k, name=EQUIPMENT_RU.get(k, k), qty=n)
          for k, n in job.equipment.items()]
    eq_text = ", ".join(f"{e['name']} ×{e['qty']}" for e in eq) or "не нужно"
    duration = (f"{job.service_min} мин на месте: норматив {norm} мин минус "
                f"{NORM_ROAD_MIN} мин дороги (дорогу считаем по карте) плюс "
                f"{APPROACH_MIN} мин на парковку и подход"
                if norm is not None else f"{job.service_min} мин на месте")
    need = (TRANSPORT_RU[job.requires_transport] if job.requires_transport
            else None)
    return dict(
        job_id=job.id, work_kind=kind, bk=job.bk, hd=job.hd,
        skill=SKILL_RU[job.skill],
        window=window_str(job), window_start=hhmm(job.win_start),
        window_end=hhmm(job.win_end), window_all_day=job.floating_window,
        window_text=window_phrase(job),
        duration_min=job.service_min, norm_min=norm, duration_text=duration,
        priority=PRIORITY_TZ[tz], priority_level=level, priority_rank=job.priority,
        priority_text=f"{PRIORITY_TZ[tz]} — {level}",
        equipment=eq, equipment_text=eq_text,
        transport_required=need,
        transport_text=(f"нужен {TRANSPORT_NEED[job.requires_transport]}"
                        if job.requires_transport else "любой"),
        address=job.address, zone=job.cluster, district=job.district,
        summary=" · ".join([f"Заявка {job.id}", kind, PRIORITY_TZ[tz],
                            f"окно {window_phrase(job)}",
                            f"{job.service_min} мин", job.address]),
    )


# --- день бригады: во что ещё можно вставлять заявки ----------------------------

@dataclass
class Day:
    """Часть дня бригады, в которую ещё можно вставлять заявки.

    В обычном плане это весь день: выезд из офиса или базы с начала смены.
    После события — только будущее: бригада стоит там, где закончила
    последнюю начатую работу, тронуться раньше события не может, уже
    отданное клиентам оборудование в запас не возвращается, а рабочий день
    считается от утреннего выезда.
    """
    seq: list = field(default_factory=list)      # заявки в порядке объезда
    start_key: str | None = None
    earliest: int | None = None
    finish_by: int | None = None
    used: dict = field(default_factory=dict)     # отдано клиентам до события
    head: list = field(default_factory=list)     # замороженные визиты (Stop)
    blocked: bool = False                        # выбыла по событию
    at: int | None = None                        # момент события


def plan_days(plan, problem) -> dict:
    """{engineer_id: Day} для готового плана. Для плана после события граница
    прошлого и будущего берётся из replan.freeze — той же функции, которой
    её провело само перепланирование: что заморожено, решает она, а не
    правило «начато до события», которое здесь пришлось бы повторить."""
    jobs = {j.id: j for j in problem.rd.jobs}
    routes = {r.engineer_id: r for r in plan.routes}
    meta = plan.meta or {}
    at = meta.get("at_min")
    if at is None:
        return {e.id: Day(seq=_seq_of(routes[e.id], jobs) if e.id in routes else [])
                for e in problem.engineers}
    from .replan import freeze                   # replan -> engine -> explain
    state = freeze(plan, problem, at)
    blocked = set(meta.get("blocked_engineers") or ())
    if meta.get("blocked_engineer"):
        blocked.add(meta["blocked_engineer"])
    days = {}
    for e in problem.engineers:
        r, st = routes.get(e.id), state.get(e.id)
        if r is None or st is None:
            days[e.id] = Day(earliest=max(e.shift_start, at), blocked=e.id in blocked,
                             at=at)
            continue
        frozen = {s.job_id for s in st["stops"]}
        days[e.id] = Day(
            seq=[jobs[s.job_id] for s in r.stops if s.job_id not in frozen],
            start_key=st["last_key"], earliest=max(st["free_at"], at),
            finish_by=st["end_by"], used=dict(st["used"]), head=list(st["stops"]),
            blocked=e.id in blocked, at=at)
    return days


def solver_days(problem, routes, start_at=None, available_from=None, end_by=None,
                capacity_used=None, blocked=(), not_before=None) -> dict:
    """{engineer_id: Day} из того, что знает планировщик в момент расчёта."""
    jobs = {j.id: j for j in problem.rd.jobs}
    by_engineer = {r.engineer_id: r for r in routes}
    blocked = set(blocked or ())
    days = {}
    for e in problem.engineers:
        r = by_engineer.get(e.id)
        earliest = (available_from or {}).get(e.id)
        if not_before is not None:
            earliest = max(e.shift_start if earliest is None else earliest,
                           not_before)
        days[e.id] = Day(seq=[jobs[s.job_id] for s in r.stops] if r else [],
                         start_key=(start_at or {}).get(e.id), earliest=earliest,
                         finish_by=(end_by or {}).get(e.id),
                         used=dict((capacity_used or {}).get(e.id, {})),
                         blocked=e.id in blocked, at=not_before)
    return days


# --- общие вспомогательные ---------------------------------------------------

def _locate(plan, job_id):
    for r in plan.routes:
        for s in r.stops:
            if s.job_id == job_id:
                return r, s
    return None, None


def _seq_of(route, jobs_by_id) -> list:
    return [jobs_by_id[s.job_id] for s in route.stops]


def _carried(seq, used=None) -> dict:
    out = dict(used or {})
    for j in seq:
        for k, n in j.equipment.items():
            out[k] = out.get(k, 0) + n
    return out


def _replay(eng, seq, problem, day=None):
    day = day or Day()
    return replay_route(eng, seq, problem.travel, problem.rd.depots,
                        start_key=day.start_key, earliest=day.earliest,
                        finish_by=day.finish_by)


def _fits(route, eng, day=None) -> Verdict:
    """route_fits плюс граница дня после события: двенадцать часов считаются
    от утреннего выезда, а не от момента события. Тем же числом ограничен
    планировщик при перепланировании (engine.solve, end_by)."""
    v = route_fits(route, eng)
    if not v.ok or day is None or day.finish_by is None or not route.stops:
        return v
    end = route.stops[-1].end_min
    if end > day.finish_by:
        return Verdict(False, Code.TIME,
                       f"работа закончится {hhmm(end)}, а 12 ч от утреннего "
                       f"выезда истекают в {hhmm(day.finish_by)}")
    return v


def _try_insert(eng, seq, job, problem, at: int, day=None):
    """Маршрут с заявкой на позиции `at`, если он допустим. Иначе None.

    Порядок остальных точек не меняется: мы отвечаем на вопрос «во что обошлась
    бы эта заявка сейчас», а не «как бы выглядел заново перестроенный день».
    """
    trial = seq[:at] + [job] + seq[at:]
    route = _replay(eng, trial, problem, day)
    return route if _fits(route, eng, day).ok else None


def _insertions(eng, seq, job, problem, base_km: float | None = None, day=None):
    """Все допустимые вставки: [(прирост пробега, позиция, маршрут)]."""
    if not check_equipment(job, eng, _carried(seq, day.used if day else None)).ok:
        return []
    if base_km is None:
        base_km = _replay(eng, seq, problem, day).km
    out = []
    for at in range(len(seq) + 1):
        route = _try_insert(eng, seq, job, problem, at, day)
        if route is not None:
            out.append((round(route.km - base_km, 2), at, route))
    return out


def _best_insertion(eng, seq, job, problem, base_km: float | None = None,
                    day=None):
    """-> (прирост пробега, позиция, маршрут) либо None, если не помещается."""
    best = None
    for fit in _insertions(eng, seq, job, problem, base_km, day):
        if best is None or fit[0] < best[0]:
            best = fit
    return best


def _travel_min(route) -> int:
    return sum(s.leg_min for s in route.stops)


def _asap_starts(stops, jobs) -> dict:
    """Начала аварий с окном на весь день — ровно тех заявок, за задержку
    которых платит оценка плана (engine.solve, EMERGENCY_ASAP_PENALTY)."""
    out = {}
    for s in stops:
        j = jobs.get(s.job_id)
        if j is not None and j.skill == "emergency" and j.floating_window:
            out[s.job_id] = s.start_min
    return out


def _shifts(before: dict, after: dict) -> dict:
    return {jid: after[jid] - before[jid] for jid in after
            if jid in before and after[jid] != before[jid]}


def _delay_text(shifts: dict, before: dict, after: dict) -> str:
    late = {k: v for k, v in shifts.items() if v > 0}
    jid = max(late, key=late.get)
    one = (f"авария {jid} началась бы в {hhmm(after[jid])} вместо "
           f"{hhmm(before[jid])}")
    if len(late) == 1:
        return one
    return (f"аварии начались бы позже в сумме на {_dur(sum(shifts.values()))} "
            f"(сильнее всего {one[len('авария '):]})")


# --- кто не может взять и почему -----------------------------------------------

@dataclass
class Miss:
    """Почему заявка не встаёт в день одной бригады, которая подходит по
    навыку, транспорту и зоне."""
    engineer_id: str
    kind: str              # equipment | busy | day | fits | blocked
    text: str
    start: int | None = None     # busy: самое раннее возможное начало; fits: начало
    span: int | None = None      # day: длина дня с этой заявкой
    km: float | None = None      # fits: прирост пробега
    items: tuple = ()            # equipment: чего не хватает


def _day_excess(route, eng, day) -> int:
    end = route.stops[-1].end_min
    excess = [route.span_min - MAX_WORKDAY_MIN]
    if HARD_SHIFT_END:
        excess.append(end - eng.shift_end)
    if day is not None and day.finish_by is not None:
        excess.append(end - day.finish_by)
    return max(excess)


def _day_text(route, eng, day) -> str:
    end = route.stops[-1].end_min
    if route.span_min > MAX_WORKDAY_MIN:
        return (f"день вышел бы {_dur(route.span_min)} — больше "
                f"{MAX_WORKDAY_MIN // 60} ч по нормативу")
    if HARD_SHIFT_END and end > eng.shift_end:
        return (f"работа закончилась бы в {hhmm(end)}, а смена до "
                f"{hhmm(eng.shift_end)}")
    return (f"работа закончилась бы в {hhmm(end)} — позже 12 ч от утреннего "
            f"выезда")


def _miss(eng, day, job, problem) -> Miss:
    """Та же проверка, что у планировщика, но с ответом «почему нет».

    Вставка пробуется в каждое место маршрута. Если есть место, где никто не
    опаздывает, но день выходит за норматив, — бригаде не хватает рабочего
    дня. Если опаздывает кто-то при любой вставке — бригада занята в окно
    клиента, и тогда считаем, когда она смогла бы начать, будь окно шире.
    """
    if day.blocked:
        return Miss(eng.id, "blocked",
                    f"выбыла в {hhmm(day.at)}" if day.at is not None else "выбыла")
    seq = day.seq
    carried = _carried(seq, day.used)
    if not check_equipment(job, eng, carried).ok:
        short = [k for k, n in job.equipment.items()
                 if eng.equipment.get(k, 0) - carried.get(k, 0) < n]
        names = [EQUIPMENT_RU.get(k, k) for k in short]
        parts = []
        for k in short:
            stock = eng.equipment.get(k, 0)
            parts.append(f"нет «{EQUIPMENT_RU.get(k, k)}» — в утренний запас "
                         f"бригады не входит" if stock == 0 else
                         f"закончился «{EQUIPMENT_RU.get(k, k)}»: все {stock} шт. "
                         f"утреннего запаса уже распределены по её заявкам")
        return Miss(eng.id, "equipment", "; ".join(parts), items=tuple(names))

    base_km = _replay(eng, seq, problem, day).km
    closest_day = None
    closest_late = None
    for at in range(len(seq) + 1):
        route = _replay(eng, seq[:at] + [job] + seq[at:], problem, day)
        if _fits(route, eng, day).ok:
            stop = next(s for s in route.stops if s.job_id == job.id)
            added = round(route.km - base_km, 2)
            grow = (f"маршрут вырастет на {_km(added)} км" if added >= -EPS_KM
                    else f"маршрут станет короче на {_km(abs(added))} км")
            return Miss(eng.id, "fits",
                        f"может взять: начало в {hhmm(stop.start_min)}, {grow}",
                        start=stop.start_min, km=added)
        late = [s for s in route.stops if s.late_min > 0]
        if late:
            worst = max(late, key=lambda s: s.late_min)
            if closest_late is None or worst.late_min < closest_late.late_min:
                closest_late = worst
        else:
            excess = _day_excess(route, eng, day)
            if closest_day is None or excess < closest_day[0]:
                closest_day = (excess, route)
    if closest_day is not None:
        route = closest_day[1]
        return Miss(eng.id, "day", _day_text(route, eng, day), span=route.span_min)

    # Занята в окно: когда смогла бы начать, если бы клиент ждал до конца смены.
    relaxed = replace(job, win_end=max(job.win_end, eng.shift_end))
    earliest = None
    for at in range(len(seq) + 1):
        route = _replay(eng, seq[:at] + [relaxed] + seq[at:], problem, day)
        if _fits(route, eng, day).ok:
            st = next(s for s in route.stops if s.job_id == job.id).start_min
            earliest = st if earliest is None else min(earliest, st)
    current = _replay(eng, seq, problem, day)
    during = [s.job_id for s in current.stops
              if s.start_min < job.win_end and s.end_min > job.win_start]
    text = f"занята в окно {window_str(job)}"
    if during:
        text += (f": в это время у неё {_plural(len(during), 'заявка', 'заявки', 'заявок')} "
                 f"{_ids(during)}")
    elif closest_late is not None and closest_late.job_id != job.id:
        text += (f": вставка сдвинула бы её заявку {closest_late.job_id} за окно "
                 f"клиента")
    if earliest is not None:
        text += f"; начать могла бы только в {hhmm(earliest)}"
    else:
        text += "; позже ей не хватает рабочего дня"
    return Miss(eng.id, "busy", text, start=earliest)


def _misses_text(misses: list, job) -> str:
    """Сгруппированная причина по нескольким бригадам: сначала те, кто занят
    в окно клиента, потом нехватка рабочего дня, потом оборудование."""
    win = window_str(job)
    by: dict = {}
    for m in misses:
        by.setdefault(m.kind, []).append(m)
    parts = []
    busy = by.get("busy", [])
    if busy:
        verb = "занята" if len(busy) == 1 else "заняты"
        who = busy[0].engineer_id if len(busy) == 1 else \
            _ids([m.engineer_id for m in busy])
        part = f"{who} {verb} в окно {win}"
        starts = [m for m in busy if m.start is not None]
        if starts:
            m = min(starts, key=lambda m: m.start)
            part += (f"; ближайшая, {m.engineer_id}, могла бы начать только в "
                     f"{hhmm(m.start)}" if len(busy) > 1
                     else f" и могла бы начать только в {hhmm(m.start)}")
        else:
            part += (", а позже ей не хватает рабочего дня" if len(busy) == 1
                     else ", а позже им не хватает рабочего дня")
        parts.append(part)
    day = by.get("day", [])
    if day:
        if len(day) == 1:
            parts.append(f"у {day[0].engineer_id} {day[0].text}")
        else:
            m = min(day, key=lambda m: m.span or 0)
            parts.append(f"у {_ids([x.engineer_id for x in day])} не хватает "
                         f"рабочего дня (у {m.engineer_id}: {m.text})")
    eq = by.get("equipment", [])
    if eq:
        names = sorted({n for m in eq for n in m.items})
        parts.append(f"у {_ids([m.engineer_id for m in eq])} не осталось "
                     f"{_items(names)}")
    gone = by.get("blocked", [])
    if gone:
        parts.append(f"{_ids([m.engineer_id for m in gone])} "
                     f"{'выбыла' if len(gone) == 1 else 'выбыли'}")
    return "; ".join(parts)


def _no_profile(job, problem, static) -> Unassigned:
    """Ни одна бригада не проходит навык, транспорт, зону и смену. Причина —
    не про первую попавшуюся бригаду, а про регион: какого сочетания в нём
    нет. Разбор идёт от зоны, потому что так думает диспетчер: «кто у меня
    работает в Кашире».
    """
    zone = [e for e in problem.engineers if e.cluster == job.cluster]
    skill = SKILL_RU[job.skill]
    if not zone:
        others = sorted({e.cluster for e in problem.engineers})
        return Unassigned(
            job_id=job.id, code=Code.CLUSTER,
            reason=f"в зоне «{job.cluster}» нет ни одной бригады",
            detail=f"все бригады региона работают в других зонах: "
                   f"{_items(others)}")
    skilled = [e for e in zone if job.skill in e.skills]
    if not skilled:
        return Unassigned(
            job_id=job.id, code=Code.SKILL,
            reason=f"в зоне «{job.cluster}» нет бригады с навыком «{skill}»",
            detail=f"ни у одной из {_count(len(zone), 'бригады', 'бригад', 'бригад')} "
                   f"зоны «{job.cluster}» нет навыка «{skill}»")
    need = job.requires_transport
    if need and not any(e.transport == need for e in skilled):
        return Unassigned(
            job_id=job.id, code=Code.TRANSPORT,
            reason=f"в зоне «{job.cluster}» нет бригады {TRANSPORT_ON[need]} "
                   f"с навыком «{skill}»",
            detail=f"навык «{skill}» в зоне «{job.cluster}» есть у "
                   f"{_count(len(skilled), 'бригады', 'бригад', 'бригад')}, но "
                   f"ни у одной нет {TRANSPORT_GEN[need]}, а работа его требует")
    v = next((v for e, v in static if e in skilled and not v.ok), None)
    # Verdict.__bool__ возвращает ok, а здесь v — как раз проваленная проверка:
    # `if v` был бы ложным всегда и подменял причину на «нет ни одного исполнителя».
    code = v.code if v is not None else Code.NO_ENGINEER
    return Unassigned(job_id=job.id, code=code,
                      reason=REASON_RU.get(code, REASON_RU[Code.NO_ENGINEER]),
                      detail=v.text if v is not None else "")


def unassigned_reason(job, problem, days: dict, not_before: int | None = None):
    """-> (Unassigned, [Miss]) — причина неназначения по одной заявке.

    Одна функция и для планировщика (engine._extract), и для карточки
    заявки: иначе список «Не назначено» и объяснение по клику разойдутся.
    Порядок разбора — от неустранимого сегодня к устранимому: окно уже
    закрылось; в регионе нет подходящей бригады; подходящие выбыли; у всех
    кончилось оборудование; не хватает времени — с конкретикой, у кого и чего.
    """
    win = window_str(job)
    if not_before is not None and job.win_end < not_before:
        return Unassigned(
            job_id=job.id, code=Code.WINDOW_PASSED,
            reason=REASON_RU[Code.WINDOW_PASSED],
            detail=f"окно {win} закрылось раньше события в {hhmm(not_before)}: "
                   f"начать работу в прошлом нельзя — новое время нужно "
                   f"согласовать с клиентом"), []

    static = [(e, check_static(job, e)) for e in problem.engineers]
    fit = [e for e, v in static if v.ok]
    if not fit:
        return _no_profile(job, problem, static), []
    free = [e for e in fit if not days.get(e.id, Day()).blocked]
    if not free:
        at = next((days[e.id].at for e in fit if days.get(e.id)), None)
        when = f" в {hhmm(at)}" if at is not None else ""
        return Unassigned(
            job_id=job.id, code=Code.NO_ENGINEER,
            reason="подходящая бригада выбыла" if len(fit) == 1
            else "все подходящие бригады выбыли",
            detail=f"заявку может взять только {_ids([e.id for e in fit])}, а "
                   f"{'она выбыла' if len(fit) == 1 else 'они выбыли'}{when}"), []

    misses = [_miss(e, days.get(e.id, Day()), job, problem) for e in free]
    n = len(free)
    fits = [m for m in misses if m.kind == "fits"]
    if fits:
        # Планировщик ограничен временем счёта и изредка не находит место,
        # которое есть. Сказать об этом честнее, чем выдумать причину.
        m = min(fits, key=lambda m: m.km)
        return Unassigned(
            job_id=job.id, code=Code.TIME,
            reason="есть свободное место — можно назначить вручную",
            detail=f"{m.engineer_id} {m.text}; за время счёта планировщик это "
                   f"место не нашёл"), misses
    eq = [m for m in misses if m.kind == "equipment"]
    if len(eq) == n:
        names = sorted({x for m in eq for x in m.items})
        who = (f"у единственной подходящей бригады, {eq[0].engineer_id}, не "
               f"осталось {_items(names)}" if n == 1 else
               f"ни у одной из {n} подходящих бригад не осталось {_items(names)}")
        return Unassigned(
            job_id=job.id, code=Code.EQUIPMENT,
            reason=f"у подходящих бригад не осталось {_items(names)}",
            detail=f"{who}: утренний запас уже распределён по другим заявкам"
                   + (f" ({_ids([m.engineer_id for m in eq])})" if n > 1 else "")), misses

    busy = [m for m in misses if m.kind == "busy"]
    reason = (f"нет свободных бригад на {win}" if busy
              else "подходящим бригадам не хватает рабочего дня")
    if n == 1:
        m = misses[0]
        detail = (f"единственная подходящая бригада, {m.engineer_id}, "
                  + (m.text if m.kind == "busy" else f"не успевает: {m.text}"))
    elif len(busy) == n:
        detail = (f"{_count(n, 'подходящая бригада', 'подходящие бригады', 'подходящих бригад')} "
                  f"{_plural(n, 'занята', 'заняты', 'заняты')} в окно {win}")
        starts = [m for m in busy if m.start is not None]
        if starts:
            m = min(starts, key=lambda m: m.start)
            detail += (f"; ближайшая, {m.engineer_id}, могла бы начать только в "
                       f"{hhmm(m.start)}")
        else:
            detail += "; позже им не хватает рабочего дня"
    else:
        detail = f"подходящих бригад {n}: " + _misses_text(misses, job)
    return Unassigned(job_id=job.id, code=Code.TIME, reason=reason,
                      detail=detail), misses


def _profile_text(job, e) -> str:
    """Все причины, по которым бригада не подходит, одной строкой — а не
    только первая: «нет навыка» при заодно отсутствующем автомобиле
    выглядит как полуправда."""
    out = []
    if job.skill not in e.skills:
        out.append(f"нет навыка «{SKILL_RU[job.skill]}» (умеет: "
                   f"{', '.join(SKILL_SHORT[s] for s in SKILL_ORDER if s in e.skills)})")
    need = job.requires_transport
    if need and e.transport != need:
        out.append(f"нет {TRANSPORT_GEN[need]} ({TRANSPORT_RU[e.transport].lower()})")
    if job.cluster != e.cluster:
        out.append(f"работает в другой зоне («{e.cluster}»)")
    if not out:
        return check_static(job, e).text
    return "; ".join(out)


def _rejected_summary(job, fails: list, only_one: bool) -> str:
    """«остальные 14 бригад региона отсеялись: у 10 нет навыка «Аварийные
    работы», 4 работают в других зонах». Группировка по первой причине в том
    же порядке, в каком их проверяет планировщик, — поэтому числа в сумме
    дают всех отсеянных."""
    if not fails:
        return ""
    groups: dict = {}
    for e, v in fails:
        groups.setdefault(v.code, []).append(e)
    parts = []
    for code, es in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        k = len(es)
        if code == Code.SKILL:
            parts.append(f"у {k} нет навыка «{SKILL_RU[job.skill]}»")
        elif code == Code.TRANSPORT:
            parts.append(f"у {k} нет {TRANSPORT_GEN[job.requires_transport]}")
        elif code == Code.CLUSTER:
            parts.append(f"{k} {_plural(k, 'работает', 'работают', 'работают')} "
                         f"в других зонах")
        elif code == Code.SHIFT_WINDOW:
            parts.append(f"у {k} окно {window_str(job)} не ложится в смену")
    total = len(fails)
    if only_one and total == 1:
        head = "другая бригада региона отсеялась"
    elif only_one:
        head = (f"остальные {_count(total, 'бригада', 'бригады', 'бригад')} региона "
                f"{_plural(total, 'отсеялась', 'отсеялись', 'отсеялись')}")
    else:
        head = (f"ещё {_count(total, 'бригада', 'бригады', 'бригад')} региона "
                f"{_plural(total, 'отсеялась', 'отсеялись', 'отсеялись')} сразу")
    return head + ": " + ", ".join(parts)


def _all_checks(job, plan, problem, days=None, owner_km=None) -> list:
    """Полная таблица «бригада x навык x транспорт x зона x окно x оборудование
    x время x вердикт». В карточку по умолчанию не попадает — только за кнопку.

    Заявка ИСКЛЮЧАЕТСЯ из маршрута той бригады, у которой стоит сейчас. Иначе
    её пришлось бы вставлять в день, где она уже есть, то есть проверять на
    двойное бронирование — и таблица заявляла бы «не помещается по времени»
    ровно про того исполнителя, который её выполняет. Строка про текущего
    владельца — первое, что читают, открыв таблицу, и противоречие плану
    именно в ней обесценивает всё остальное.

    Прирост пробега у владельца — фактический, на его месте в маршруте: ровно
    то число, что в заголовке карточки. У остальных — во что обошлась бы
    вставка в лучшее место их маршрута.

    Проверка, которую не выполняли, — это None, а не «нет». Бригада без
    навыка отсеивается сразу, и ни окно, ни время по ней не считаются;
    красное «нет» в такой клетке утверждало бы то, чего никто не проверял.
    """
    days = days or plan_days(plan, problem)
    routes = {r.engineer_id: r for r in plan.routes}
    jobs = {j.id: j for j in problem.rd.jobs}
    owner_route, owner_stop = _locate(plan, job.id)
    owner = owner_route.engineer_id if owner_route else None
    frozen = owner is not None and any(s.job_id == job.id
                                       for s in days.get(owner, Day()).head)
    rows = []
    for e in problem.engineers:
        profile, static = check_profile(job, e), check_static(job, e)
        day = days.get(e.id, Day())
        is_owner = e.id == owner
        if is_owner:
            others = [jobs[s.job_id] for s in owner_route.stops if s.job_id != job.id]
            eq = check_equipment(job, e, _carried(others))
        else:
            eq = check_equipment(job, e, _carried(day.seq, day.used))
        idle = not (routes.get(e.id) and routes[e.id].stops)
        checked = static.ok and eq.ok and not day.blocked and not frozen
        fit = None
        if checked and not is_owner:
            seq = [j for j in day.seq if j.id != job.id]
            fit = _best_insertion(e, seq, job, problem, day=day)
        if is_owner:
            verdict = "сейчас у неё"
        elif day.blocked:
            verdict = (f"выбыла в {hhmm(day.at)}: новых заявок не берёт"
                       if day.at is not None else "выбыла: новых заявок не берёт")
        elif not static.ok:
            verdict = _profile_text(job, e)
        elif not eq.ok:
            verdict = eq.text
        elif frozen:
            verdict = "визит зафиксирован событием — не перепланировался"
        elif fit and idle:
            verdict = ("берёт, но выйдет на смену — исполнителей станет на "
                       "одного больше")
        elif fit:
            verdict = "берёт"
        else:
            verdict = _miss(e, replace(day, seq=[j for j in day.seq if j.id != job.id]),
                            job, problem).text
        rows.append(dict(
            engineer_id=e.id, transport=TRANSPORT_RU[e.transport],
            is_current=is_owner, idle=idle, blocked=day.blocked,
            skill_ok=job.skill in e.skills,
            transport_ok=not job.requires_transport or e.transport == job.requires_transport,
            cluster_ok=job.cluster == e.cluster,
            window_ok=(static.ok or static.code != Code.SHIFT_WINDOW)
            if profile.ok else None,
            equipment_ok=eq.ok,
            time_ok=True if is_owner else (fit is not None if checked else None),
            added_km=owner_km if is_owner else (fit[0] if fit else None),
            verdict=verdict,
        ))
    return rows


# --- почему назначена --------------------------------------------------------

def _headline(eng, job, stop, added_km: float) -> str:
    """Заголовок читается вслух и закрывает первый вопрос: когда приедет и
    почему не начнёт сразу. Окно клиента называется всегда — без него
    «приедет в 15:41, начнёт в 20:00» выглядит ошибкой, а не обещанием."""
    who = f"{eng.id} ({TRANSPORT_RU[eng.transport]})"
    win = window_phrase(job)
    if stop.wait_min > 0:
        text = (f"{who} приедет в {hhmm(stop.arrive_min)} и будет ждать открытия "
                f"окна клиента {win} {_dur(stop.wait_min)}; начнёт в "
                f"{hhmm(stop.start_min)}, закончит в {hhmm(stop.end_min)}")
    elif stop.late_min > 0:
        text = (f"{who} приедет в {hhmm(stop.arrive_min)} и начнёт сразу, но с "
                f"опозданием {_dur(stop.late_min)} к окну клиента {win}; "
                f"закончит в {hhmm(stop.end_min)}")
    else:
        text = (f"{who} приедет в {hhmm(stop.arrive_min)} и сразу начнёт; окно "
                f"клиента {win}; закончит в {hhmm(stop.end_min)}")
    return f"{text}; заявка {_km_delta(added_km)}"


def _checked(job, eng, route, stop, pos, jobs) -> list:
    """Что проверено для назначенной бригады — списком, без открытия таблицы.
    Это и есть ответ на вопрос ТЗ 8.1 «все ли обязательные ограничения
    реально проверяются» в тот момент, когда диспетчер смотрит на результат."""
    out = []
    has = job.skill in eng.skills
    out.append(dict(key="skill", label="Навык", ok=has,
                    text=f"«{SKILL_RU[job.skill]}» — "
                         + ("у бригады есть" if has else "у бригады НЕТ")))
    need = job.requires_transport
    if need:
        ok = eng.transport == need
        text = f"нужен {TRANSPORT_NEED[need]} — {TRANSPORT_HAS[eng.transport]}"
    else:
        ok, text = True, f"особых требований нет — {TRANSPORT_HAS[eng.transport]}"
    out.append(dict(key="transport", label="Транспорт", ok=ok, text=text))
    ok = job.cluster == eng.cluster
    out.append(dict(key="zone", label="Зона", ok=ok,
                    text=f"«{job.cluster}» — " + ("бригада работает в этой зоне" if ok
                                                  else f"бригада из зоны «{eng.cluster}»")))
    ok = job.win_start <= stop.start_min <= job.win_end
    out.append(dict(key="window", label="Окно клиента", ok=ok,
                    text=f"{window_phrase(job)} — начало в {hhmm(stop.start_min)}, "
                         + ("внутри окна" if ok else "ВНЕ окна")))
    ok = stop.end_min <= eng.shift_end
    out.append(dict(key="shift", label="Смена", ok=ok,
                    text=f"{hhmm(eng.shift_start)}–{hhmm(eng.shift_end)} — работа "
                         f"закончится в {hhmm(stop.end_min)}"))
    if job.equipment:
        used: dict = {}
        for s in route.stops[:pos + 1]:
            for k, n in jobs[s.job_id].equipment.items():
                used[k] = used.get(k, 0) + n
        parts, ok = [], True
        for k, n in job.equipment.items():
            stock = eng.equipment.get(k, 0)
            left = stock - used.get(k, 0)
            ok = ok and left >= 0
            parts.append(f"{EQUIPMENT_RU.get(k, k)} ×{n} — после визита останется "
                         f"{max(left, 0)} из {stock}")
        out.append(dict(key="equipment", label="Оборудование", ok=ok,
                        text="; ".join(parts)))
    else:
        out.append(dict(key="equipment", label="Оборудование", ok=True,
                        text="не нужно"))
    span = route.span_min
    out.append(dict(key="workday", label="Длина дня", ok=span <= MAX_WORKDAY_MIN,
                    text=f"день бригады {_dur(span)} из {MAX_WORKDAY_MIN // 60} ч "
                         f"по нормативу"))
    return out


def _position_text(eng, route, pos, added_km, problem) -> str:
    """Где заявка стоит в маршруте и чего стоит заезд к ней: то самое «по пути
    между двумя адресами», которое диспетчер проверяет глазами на карте."""
    n = len(route.stops)
    start = problem.rd.depots[eng.depot].name
    km = _km(abs(added_km))
    detour = (f"заезд даже сокращает путь на {km} км" if added_km < -EPS_KM
              else f"крюк {km} км")
    if n == 1:
        return f"Единственная заявка маршрута: выезд к ней ({start}), {km} км"
    if pos == 0:
        return (f"В маршруте 1-я из {n}: сразу после выезда ({start}), перед "
                f"заявкой {route.stops[1].job_id}; {detour}")
    if pos + 1 < n:
        return (f"В маршруте {pos + 1}-я из {n}: между заявками "
                f"{route.stops[pos - 1].job_id} и {route.stops[pos + 1].job_id}; "
                f"{detour}")
    return (f"В маршруте {n}-я из {n}, последняя: после заявки "
            f"{route.stops[pos - 1].job_id}, переезд {km} км")


def why_assigned(job_id: str, plan, problem) -> Explanation:
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    job = jobs[job_id]
    route, stop = _locate(plan, job_id)
    if route is None:
        raise ValueError(f"{job_id}: заявка не назначена, нужен why_unassigned")
    eng = engs[route.engineer_id]
    days = plan_days(plan, problem)
    frozen = any(s.job_id == job_id for s in days[eng.id].head)

    # проверяем фактическое назначение теми же правилами, что и планировщик
    verdict = check_static(job, eng)
    seq = _seq_of(route, jobs)
    pos = [s.job_id for s in route.stops].index(job_id)
    without = seq[:pos] + seq[pos + 1:]
    base = replay_route(eng, without, problem.travel, problem.rd.depots)
    added_km = round(route.km - base.km, 2)
    added_min = _travel_min(route) - _travel_min(base)

    prev_key = (f"job:{route.stops[pos - 1].job_id}" if pos
                else f"depot:{problem.rd.depots[eng.depot].key}")
    modes = problem.travel.all_modes(prev_key, f"job:{job.id}")

    headline = _headline(eng, job, stop, added_km)
    if not verdict.ok:                      # не должно случаться; молчать нельзя
        headline = f"ВНИМАНИЕ: назначение противоречит ограничениям — {verdict.text}"

    reasons, rejected, summary = _reasons_and_rivals(
        job, eng, route, stop, pos, added_km, added_min, plan, problem, days, frozen)
    schedule = dict(seq=pos + 1, of=len(route.stops), arrive=hhmm(stop.arrive_min),
                    start=hhmm(stop.start_min), end=hhmm(stop.end_min),
                    wait_min=stop.wait_min,
                    wait_text=_dur(stop.wait_min) if stop.wait_min else "",
                    late_min=stop.late_min, window=window_str(job),
                    travel_min=stop.leg_min, leg_km=stop.leg_km)
    return Explanation(
        job_id=job_id, engineer_id=eng.id, headline=headline,
        reasons=reasons[:MAX_REASONS], rejected=rejected[:MAX_REJECTED],
        travel_by_mode=modes, used_mode=eng.transport,
        checks=_all_checks(job, plan, problem, days, owner_km=added_km),
        passport=passport(job), checked=_checked(job, eng, route, stop, pos, jobs),
        schedule=schedule, added_km=added_km,
        position_text=_position_text(eng, route, pos, added_km, problem),
        rejected_summary=summary, frozen=frozen)


def _move_effect(job, eng, route, rival, fit, plan, problem, days, jobs):
    """Что изменится в плане, если отдать заявку сопернику при неизменных
    маршрутах остальных: сколько станет бригад и когда начнутся аварии.
    Это слои оценки плана поверх километров (weights.py)."""
    routes = {r.engineer_id: r for r in plan.routes}
    rival_route = routes.get(rival.id)
    rival_stops = rival_route.stops if rival_route else []
    d_brig = (0 if rival_stops else 1) - (1 if len(route.stops) == 1 else 0)
    owner_day = days[eng.id]
    owner_after = _replay(eng, [j for j in owner_day.seq if j.id != job.id],
                          problem, owner_day)
    before = {**_asap_starts(route.stops, jobs), **_asap_starts(rival_stops, jobs)}
    after = {**_asap_starts(owner_after.stops, jobs), **_asap_starts(fit[2].stops, jobs)}
    return d_brig, before, after, _shifts(before, after)


def _compare_text(job, eng, route, pos, added_km, added_min, rivals, plan,
                  problem, days, jobs) -> str:
    best_km, rival, fit = rivals[0]
    rid = rival.id
    diff = round(best_km - added_km, 2)
    if diff > EPS_KM:
        rival_base = _replay(rival, days[rival.id].seq, problem, days[rival.id])
        dm = (_travel_min(fit[2]) - _travel_min(rival_base)) - added_min
        tail = f" и {dm} мин в пути" if dm > 0 else ""
        if abs(added_km) <= EPS_KM:
            # Разница равна цене соперника — печатать её второй раз незачем.
            return (f"дешевле всех по пробегу: заявка по пути, маршрут от неё "
                    f"не вырос, а следующей по цене бригаде {rid} заезд "
                    f"{_cost_phrase(best_km, would=True)}{tail}")
        return (f"дешевле всех по пробегу: заезд {_cost_phrase(added_km)}, а "
                f"следующей по цене бригаде {rid} {_cost_phrase(best_km, would=True)} "
                f"— разница {_km(diff)} км{tail}")

    d_brig, before, after, shifts = _move_effect(job, eng, route, rival, fit,
                                                 plan, problem, days, jobs)
    net = sum(shifts.values())
    if abs(diff) <= EPS_KM:
        same = (" — обе бригады выезжают из одной точки"
                if pos == 0 and fit[1] == 0 and rival.depot == eng.depot else "")
        head = (f"ничья по пробегу: у {rid} заезд {_cost_phrase(best_km, would=True)}"
                f", столько же{same}")
        if d_brig > 0:
            return (f"{head}, но у {rid} сегодня нет ни одной заявки: отдать её — "
                    f"вывести на линию ещё одну бригаду")
        if d_brig == 0 and net > 0:
            return f"{head}, но тогда {_delay_text(shifts, before, after)}; {ASAP_TEXT}"
        return (f"{head}; по остальным правилам варианты тоже равны, и "
                f"планировщик оставил заявку у {eng.id}")

    saving = -diff
    head = f"у {rid} вышло бы на {_km(saving)} км короче"
    if d_brig > 0:
        return (f"{head}, но у {rid} сегодня нет ни одной заявки: отдать её — "
                f"вывести на линию ещё одну бригаду, а число бригад план бережёт "
                f"прежде километров")
    if d_brig == 0 and net > 0 and \
            net * W.EMERGENCY_ASAP_PENALTY_PER_MIN >= saving * 1000:
        return f"{head}, но тогда {_delay_text(shifts, before, after)}; {ASAP_TEXT}"
    if d_brig < 0:
        return (f"{head}, и {eng.id} вовсе осталась бы без заявок — этот вариант "
                f"лучше, но за время счёта планировщик его не нашёл")
    return (f"{head}, и по остальным правилам план бы не пострадал — это место, "
            f"где за время счёта планировщик не нашёл лучший вариант")


def _own_position_note(job, eng, route, pos, added_km, problem, days, jobs):
    """Почему заявка не на самом коротком месте собственного маршрута. На
    Юго-востоке так бывает из-за аварий: их ставят раньше, чем выгодно по
    дороге. Если причина другая — так и говорим."""
    day = days[eng.id]
    if all(j.id != job.id for j in day.seq):
        return None
    tail = [j for j in day.seq if j.id != job.id]
    fit = _best_insertion(eng, tail, job, problem, day=day)
    if fit is None or fit[0] >= added_km - 0.01:
        return None
    saving = round(added_km - fit[0], 2)
    place = len(day.head) + fit[1] + 1
    before = _asap_starts(route.stops, jobs)
    after = _asap_starts(fit[2].stops, jobs)
    shifts = _shifts(before, after)
    net = sum(shifts.values())
    head = (f"стоит {pos + 1}-й по порядку, хотя на {place}-м месте маршрут был "
            f"бы короче на {_km(saving)} км")
    if net > 0 and net * W.EMERGENCY_ASAP_PENALTY_PER_MIN >= saving * 1000:
        return f"{head}: тогда {_delay_text(shifts, before, after)}; {ASAP_TEXT}"
    return f"{head} — этот вариант за время счёта планировщик не нашёл"


def _reasons_and_rivals(job, eng, route, stop, pos, added_km, added_min, plan,
                        problem, days, frozen):
    jobs = {j.id: j for j in problem.rd.jobs}
    reasons, rejected = [], []
    at = days[eng.id].at

    fails = [(e, check_static(job, e)) for e in problem.engineers if e.id != eng.id]
    fails = [(e, v) for e, v in fails if not v.ok]
    for e, v in fails:
        rejected.append(Rejection(e.id, v.code, _profile_text(job, e)))
    fit_profile = [e for e in problem.engineers if check_profile(job, e).ok]
    fit_static = [e for e in problem.engineers if check_static(job, e).ok]
    only_one = len(fit_static) == 1
    summary = _rejected_summary(job, fails, only_one)

    if frozen:
        if stop.start_min > at:
            reasons.append(f"в момент события ({hhmm(at)}) бригада уже ехала к "
                           f"клиенту и успевала только так — визит не переносился")
        elif stop.end_min > at:
            reasons.append(f"работа начата до события в {hhmm(at)} и доводится до "
                           f"конца: инженер не бросает начатую работу")
        else:
            reasons.append(f"работа выполнена до события в {hhmm(at)}; "
                           f"перепланирование меняет только то, что ещё не начато")
        rejected.sort(key=lambda r: REJECT_ORDER.get(r.code, 9))
        return reasons, rejected, summary

    rivals, misses = [], []
    for e in fit_static:
        if e.id == eng.id:
            continue
        day = days.get(e.id, Day())
        if day.blocked:
            misses.append(Miss(e.id, "blocked", f"выбыла в {hhmm(day.at)}"))
            rejected.append(Rejection(e.id, UNAVAILABLE,
                                      f"выбыла в {hhmm(day.at)}: новых заявок не берёт"))
            continue
        fit = _best_insertion(e, day.seq, job, problem, day=day)
        if fit is not None:
            rivals.append((fit[0], e, fit))
            continue
        m = _miss(e, day, job, problem)
        misses.append(m)
        rejected.append(Rejection(e.id, Code.EQUIPMENT if m.kind == "equipment"
                                  else Code.TIME, m.text))
    rivals.sort(key=lambda x: x[0])
    rejected.sort(key=lambda r: REJECT_ORDER.get(r.code, 9))

    if only_one:
        need = (f", нужен {TRANSPORT_NEED[job.requires_transport]}"
                if job.requires_transport else "")
        reasons.append(f"только эта бригада подходит по всем правилам: навык "
                       f"«{SKILL_RU[job.skill]}», зона «{job.cluster}»{need}")
        if summary:
            reasons.append(summary)
    elif rivals:
        reasons.append(_compare_text(job, eng, route, pos, added_km, added_min,
                                     rivals, plan, problem, days, jobs))
    elif misses:
        k = len(misses)
        reasons.append(f"ещё {_count(k, 'подходящая бригада', 'подходящие бригады', 'подходящих бригад')} "
                       f"не {_plural(k, 'может', 'могут', 'могут')} взять заявку: "
                       + _misses_text(misses, job))

    note = _own_position_note(job, eng, route, pos, added_km, problem, days, jobs)
    if note:
        reasons.append(note)
    if len(fit_profile) > len(fit_static):
        k = len(fit_profile) - len(fit_static)
        reasons.append(f"ещё {_count(k, 'бригада подходит', 'бригады подходят', 'бригад подходят')} "
                       f"по навыку, транспорту и зоне, но окно {window_str(job)} не "
                       f"ложится в их смену")
    if not reasons:
        reasons.append(f"подходящих бригад {len(fit_static)}; заявка "
                       f"{_km_delta(added_km)}")
    # Правило про аварии достаточно назвать один раз на карточку.
    seen = False
    for i, r in enumerate(reasons):
        if ASAP_TEXT in r:
            if seen:
                reasons[i] = r.replace(f"; {ASAP_TEXT}", "")
            seen = True
    return reasons, rejected, summary


# --- почему не назначена и что с этим делать ---------------------------------

def why_unassigned(job_id: str, plan, problem) -> Unassignment:
    jobs = {j.id: j for j in problem.rd.jobs}
    job = jobs[job_id]
    rec = next((u for u in plan.unassigned if u.job_id == job_id), None)
    if rec is None:
        raise ValueError(f"{job_id}: заявка назначена, нужен why_assigned")
    days = plan_days(plan, problem)
    at = next((d.at for d in days.values() if d.at is not None), None)
    _, misses = unassigned_reason(job, problem, days, not_before=at)
    out = Unassignment(job_id=job_id, code=rec.code, reason=rec.reason,
                       detail=rec.detail, passport=passport(job),
                       blocked_by=[dict(engineer_id=m.engineer_id, kind=m.kind,
                                        text=m.text) for m in misses])
    out.remedies = _remedies(job, plan, problem, days)
    out.remedy = out.remedies[0].text if out.remedies else ""
    return out


def remedy_line(job, plan, problem, days=None) -> str:
    """Первое «что можно сделать» одной фразой — для списка «Не назначено».
    Та же функция, что строит средства в карточке, поэтому строка списка и
    первая строка карточки совпадают буква в букву."""
    remedies = _remedies(job, plan, problem, days)
    return remedies[0].text if remedies else ""


def _remedies(job, plan, problem, days=None) -> list:
    days = days or plan_days(plan, problem)
    at = next((d.at for d in days.values() if d.at is not None), None)
    remedies = []
    win = window_str(job)

    fit_profile = [e for e in problem.engineers if check_profile(job, e).ok
                   and not days.get(e.id, Day()).blocked]

    # 1. Сдвиг окна — самое дешёвое, что может сделать человек. Считаем, кто
    #    тогда возьмёт заявку и в какое время. Предлагается время, ближайшее
    #    к обещанному клиенту окну: клиенту важнее «на час позже», чем
    #    «на полкилометра короче». Это же время называет причина отказа
    #    («ближайшая могла бы начать только в …»), и строки не расходятся.
    best_window = None
    for e in fit_profile:
        day = days.get(e.id, Day())
        free = replace(job, win_start=e.shift_start, win_end=e.shift_end)
        for added, _, route in _insertions(e, day.seq, free, problem, day=day):
            start = next(s for s in route.stops if s.job_id == job.id).start_min
            gap = max(job.win_start - start, start - job.win_end, 0)
            key = (gap, added, start)
            if best_window is None or key < best_window[0]:
                best_window = (key, start, e, added)
    if best_window and best_window[0][0] == 0:
        # Место в окне есть: так бывает у базового варианта (он ставит заявку
        # только в конец маршрута) и если планировщику не хватило времени.
        _, start, e, added = best_window
        grow = (f"маршрут вырастет на {_km(added)} км" if added >= -EPS_KM
                else f"маршрут станет короче на {_km(abs(added))} км")
        remedies.append(Remedy(
            kind="manual", engineer_id=e.id, window=(job.win_start, job.win_end),
            action=f"назначить вручную бригаде {e.id}",
            effect=f"начало в {hhmm(start)}, {grow}",
            text=f"Бригада {e.id} может взять заявку в окне клиента: начало в "
                 f"{hhmm(start)}, {grow} — назначьте её вручную."))
    elif best_window:
        _, start, e, added = best_window
        grow = (f"её маршрут вырастет на {_km(added)} км" if added >= -EPS_KM
                else f"её маршрут даже станет короче на {_km(abs(added))} км")
        remedies.append(Remedy(
            kind="window", engineer_id=e.id, window=(start, start + 60),
            action=f"согласовать с клиентом визит в {hhmm(start)} вместо окна {win}",
            effect=f"заявку возьмёт {e.id}, {grow}",
            text=f"Бригада {e.id} возьмёт заявку, если согласовать с клиентом "
                 f"визит в {hhmm(start)} вместо окна {win}; {grow}."))

    # 2. Оборудование: профиль и время есть, не хватает железа.
    if job.equipment:
        for e in fit_profile:
            day = days.get(e.id, Day())
            if check_equipment(job, e, _carried(day.seq, day.used)).ok:
                continue
            fit = _best_insertion(e, day.seq, replace(job, equipment={}), problem,
                                  day=day)
            if fit is None:
                continue
            items = ", ".join(f"«{EQUIPMENT_RU.get(k, k)}» ×{n}"
                              for k, n in job.equipment.items())
            remedies.append(Remedy(
                kind="equipment", engineer_id=e.id,
                action=f"довезти {items} бригаде {e.id}",
                effect=f"заявка закрывается, маршрут {e.id} вырастет на {_km(fit[0])} км",
                text=f"Если довезти бригаде {e.id} {items}, она возьмёт заявку; "
                     f"её маршрут вырастет на {_km(fit[0])} км."))
            break

    # 3. Профиль: навык или транспорт. Проверяем, что это реально снимает отказ.
    if not fit_profile:
        v = next((check_profile(job, e) for e in problem.engineers
                  if not check_profile(job, e).ok), None)
        same_zone = [e for e in problem.engineers if e.cluster == job.cluster
                     and not days.get(e.id, Day()).blocked]
        for e in same_zone[:1]:
            day = days.get(e.id, Day())
            upgraded = replace(e, skills=set(e.skills) | {job.skill},
                               transport=job.requires_transport or e.transport)
            fit = _best_insertion(upgraded, day.seq, job, problem, day=day)
            kind = "transport" if v and v.code == Code.TRANSPORT else "skill"
            what = (f"пересадить {e.id} на «{TRANSPORT_RU[job.requires_transport]}»"
                    if kind == "transport"
                    else f"дать {e.id} навык «{SKILL_RU[job.skill]}»")
            if fit:
                effect = f"заявка закрывается, маршрут вырастет на {_km(fit[0])} км"
                text = (f"Если {what}, бригада возьмёт заявку; её маршрут вырастет "
                        f"на {_km(fit[0])} км.")
            else:
                effect = ("заявка всё равно не помещается по времени — "
                          "понадобится ещё одна бригада")
                text = (f"Если {what}, этого не хватит: заявка всё равно не "
                        f"помещается в её день — понадобится ещё одна бригада.")
            remedies.append(Remedy(kind=kind, engineer_id=e.id, action=what,
                                   effect=effect,
                                   text=text[0].upper() + text[1:]))

    # 4. Ещё одна бригада. Проверяем на пустом дне: если и свежая бригада не
    #    успевает, дело не в парке, и обещать «добавьте людей» нельзя. После
    #    события новая бригада выезжает не раньше самого события.
    depot_key = _depot_for(job, problem)
    extra = Engineer(id="BR-НОВАЯ", name="новая бригада",
                     depot=depot_key, cluster=job.cluster,
                     skills={job.skill}, transport=job.requires_transport or "car",
                     shift_start=SHIFT_WINDOW[0], shift_end=SHIFT_WINDOW[1],
                     equipment=dict(job.equipment))
    fresh = _replay(extra, [job], problem,
                    Day(earliest=max(SHIFT_WINDOW[0], at)) if at is not None else None)
    if _fits(fresh, extra).ok:
        # Маршрут из одной заявки: выехать можно в любой момент, лишь бы успеть.
        # replay_route ставит выезд как можно позже (так короче день), и
        # бригада приезжала бы к самому закрытию окна. Для одной заявки длина
        # дня от этого не зависит, а обещать клиенту начало окна честнее.
        leg = problem.travel.minutes(f"depot:{problem.rd.depots[depot_key].key}",
                                     f"job:{job.id}", extra.transport)
        depart = max(SHIFT_WINDOW[0], at if at is not None else 0,
                     job.win_start - leg)
        arrive = depart + leg
        gear = "".join(f" и с {EQUIPMENT_WITH.get(k, EQUIPMENT_RU.get(k, k))}"
                       for k in job.equipment)
        start_point = problem.rd.depots[depot_key].name
        remedies.append(Remedy(
            kind="fleet",
            action=f"добавить бригаду в зону «{job.cluster}»: навык "
                   f"«{SKILL_RU[job.skill]}», транспорт "
                   f"«{TRANSPORT_RU[extra.transport]}»"
                   + (f", {', '.join(EQUIPMENT_RU.get(k, k) for k in job.equipment)}"
                      if job.equipment else ""),
            effect=f"новая бригада выедет в {hhmm(depart)}, приедет в "
                   f"{hhmm(arrive)} и проедет {_km(fresh.km)} км",
            text=f"Если в зоне «{job.cluster}» сегодня выйдет ещё одна бригада с "
                 f"навыком «{SKILL_RU[job.skill]}», {TRANSPORT_ON[extra.transport]}"
                 f"{gear}, она выедет в {hhmm(depart)} ({start_point}), приедет в "
                 f"{hhmm(arrive)} и проедет {_km(fresh.km)} км."))
    elif at is not None and job.win_end < at:
        why = (f"окно {win} закрылось до события в {hhmm(at)}, а до конца смены "
               f"ни одна подходящая бригада её не возьмёт")
        remedies.append(Remedy(
            kind="none", action="согласовать с клиентом другой день", effect=why,
            text=f"Согласовать с клиентом другой день: {why}."))
    else:
        why = (f"в смену {hhmm(SHIFT_WINDOW[0])}–{hhmm(SHIFT_WINDOW[1])} она не "
               f"помещается даже у свободной бригады: работа {job.service_min} мин, "
               f"окно {win}")
        remedies.append(Remedy(
            kind="none",
            action="перенести заявку на другой день или разбить работы",
            effect=why,
            text=f"Перенести заявку на другой день или разбить работы: {why}."))
    return remedies


def _depot_for(job, problem) -> str:
    for key, d in problem.rd.depots.items():
        if d.cluster == job.cluster:
            return key
    return next(iter(problem.rd.depots))
