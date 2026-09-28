# -*- coding: utf-8 -*-
"""Солвер маршрутов на OR-Tools Routing.

Одна задача = один регион. Мульти-депо: московские бригады стартуют из офиса,
подмосковные — из «дома» в своём городе. Возврат в депо не требуется (ТЗ),
поэтому обратное плечо стоит ноль и по расстоянию, и по времени.

Модель времени — В МИНУТАХ. CumulVar узла означает НАЧАЛО РАБОТ (транзит
включает обслуживание предыдущего узла), поэтому расписание извлекается
функцией replay_route, а не чтением CumulVar.
"""
from __future__ import annotations
import json

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..config import (DATA, LATE_MAX_MIN, HARD_SHIFT_END, MAX_WORKDAY_MIN,
                      SHIFT_WINDOW)
from ..models import Engineer, Plan, Unassigned, hhmm
from ..geo.travel import TravelModel
from .feasibility import check_static, Code, REASON_RU, Verdict, candidates
from .metrics import replay_route
from . import weights as W

HORIZON = 24 * 60


def load_fleet(region: str) -> list:
    raw = json.loads((DATA / "fleet" / f"{region}.json").read_text(encoding="utf-8"))
    return [Engineer(id=e["id"], name=e["name"], depot=e["depot"], cluster=e["cluster"],
                     skills=set(e["skills"]), transport=e["transport"],
                     shift_start=e["shift_start"], shift_end=e["shift_end"],
                     equipment=dict(e["equipment"])) for e in raw]


class Problem:
    """Связка «регион + парк + матрица» и нумерация узлов."""

    def __init__(self, rd, engineers, travel: TravelModel):
        self.rd = rd
        self.engineers = engineers
        self.travel = travel
        self.depot_keys = list(rd.depots.keys())
        self.n_depots = len(self.depot_keys)
        # порядок узлов ровно как в матрице: сначала депо, потом заявки
        self.nodes = [f"depot:{k}" for k in self.depot_keys] + [f"job:{j.id}" for j in rd.jobs]
        assert self.nodes == [p["key"] for p in travel.points], \
            "порядок точек матрицы разошёлся с моделью — пересоберите tools/build_matrix.py"
        self.jobs_by_node = {self.n_depots + i: j for i, j in enumerate(rd.jobs)}
        self.node_of_job = {j.id: self.n_depots + i for i, j in enumerate(rd.jobs)}

    def is_depot(self, node: int) -> bool:
        return node < self.n_depots


def solve(problem: Problem, time_limit_s: int = 20, fixed_assignment: dict | None = None,
          log: bool = False, skip_jobs=None, start_at: dict | None = None,
          available_from: dict | None = None, end_by: dict | None = None,
          capacity_used: dict | None = None,
          blocked_engineers=None, stability: dict | None = None,
          seed_routes: dict | None = None, not_before: int | None = None,
          free_vehicles=None, algo: str = "solver", on_solution=None) -> Plan:
    """Обычный день — при вызове с одним аргументом.

    Остальные параметры нужны перепланированию и описывают уже случившийся факт:
    `skip_jobs` — что не планируем заново (выполненное, отменённое);
    `start_at` — бригада стоит не в депо, а в этой точке;
    `available_from` — раньше этого момента она тронуться не может;
    `end_by` — когда её рабочий день обязан закончиться;
    `capacity_used` — что она уже израсходовала из выданного утром запаса;
    `blocked_engineers` — кто выбыл и новых заявок не берёт;
    `stability` — прежнее назначение заявки, отход от него платный;
    `seed_routes` — прежний план как стартовое решение поиска;
    `not_before` — момент события: раньше него никто не выезжает и ни одна
                   работа не начинается, а заявка, чьё окно к этому моменту
                   закрылось, уходит в неназначенные с этой причиной;
    `free_vehicles` — кто уже задействован и повторно за это не платит.

    `on_solution` — для кривой «время счёта → результат» (tools/convergence.py):
    вызывается на каждом найденном плане со словарём
    {seconds, cost, engineers, km, assigned}. На ход поиска не влияет.
    """
    p, travel = problem, problem.travel
    engs = p.engineers
    skip_jobs = set(skip_jobs or ())
    blocked = set(blocked_engineers or ())
    stability = stability or {}
    n_nodes, n_veh = len(p.nodes), len(engs)

    # Старт — депо либо точка, где бригаду застало событие. Финиш ВСЕГДА депо:
    # возврат по ТЗ не требуется и стоит ноль, и менять финиш нельзя, иначе
    # обратное плечо внезапно начнёт считаться в обязательную метрику пробега.
    ends = [p.depot_keys.index(e.depot) for e in engs]
    starts = list(ends)
    for v, e in enumerate(engs):
        key = (start_at or {}).get(e.id)
        if key:
            starts[v] = p.nodes.index(key)
    start_nodes = {n for n in starts if not p.is_depot(n)}

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_veh, starts, ends)
    routing = pywrapcp.RoutingModel(manager)
    D = travel.dist

    # --- стоимость дуги: метры. Возврат в депо бесплатен (ТЗ: возврат не требуется)
    def make_dist_cb(eng_id):
        def cb(i, j):
            a, b = manager.IndexToNode(i), manager.IndexToNode(j)
            if p.is_depot(b):
                return 0
            cost = int(round(D[a][b]))
            was = stability.get(p.jobs_by_node[b].id)
            # Штраф за отход от уже озвученного клиенту назначения. Он платится
            # ОДИН раз, на въезде в точку, и только если исполнитель сменился.
            if was is not None and was != eng_id:
                cost += W.STABILITY_PENALTY
            return cost
        return cb

    if stability:
        for v, e in enumerate(engs):
            routing.SetArcCostEvaluatorOfVehicle(
                routing.RegisterTransitCallback(make_dist_cb(e.id)), v)
    else:
        routing.SetArcCostEvaluatorOfAllVehicles(
            routing.RegisterTransitCallback(make_dist_cb(None)))
    # Стоимость задействования. При перепланировании бригада, отработавшая утро,
    # УЖЕ посчитана в обязательной метрике «задействовано исполнителей», и её
    # предельная стоимость — ноль. Брать с неё полную цену второй раз значит
    # сделать выгодным сброс её хвоста на кого угодно, вплоть до совершенно
    # свежей бригады: метрика от этого растёт, а целевая функция улучшается.
    already = set(free_vehicles or ())
    for v, e in enumerate(engs):
        routing.SetFixedCostOfVehicle(0 if e.id in already else W.VEHICLE_COST, v)

    # --- время: свой профиль скорости у каждой бригады
    def make_time_cb(eng):
        def cb(i, j):
            a, b = manager.IndexToNode(i), manager.IndexToNode(j)
            # Работа в стартовой точке перепланирования уже выполнена — её
            # длительность не должна списываться второй раз.
            service = (0 if p.is_depot(a) or a in start_nodes
                       else p.jobs_by_node[a].service_min)
            if p.is_depot(b):
                return service
            return service + travel.minutes(a, b, eng.transport)
        return cb

    time_cbs = [routing.RegisterTransitCallback(make_time_cb(e)) for e in engs]
    routing.AddDimensionWithVehicleTransits(
        time_cbs, HORIZON, HORIZON, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    opens_at = {}
    # Бригады, чей рабочий день к моменту события уже исчерпан норматив: новых
    # заявок им не дать. Это не «выбыла» — объяснения это различают, поэтому
    # отдельное множество, а не blocked.
    day_over = set()
    for v, e in enumerate(engs):
        opens = (available_from or {}).get(e.id, e.shift_start)
        # Раньше момента события не трогается никто. Нижняя граница на начале
        # работ этого не даёт: бригада, получившая заявку в 15:40, «выезжала»
        # бы в 14:35, и новый план описывал бы поездку, которой не было.
        # Граница одна и та же здесь и в пересчёте времён (_extract), иначе
        # солвер и расписание на экране разойдутся.
        if not_before is not None:
            opens = max(opens, not_before)
        # Событие после конца смены: бригаде просто нечего делать. Пустой
        # диапазон сделал бы задачу неразрешимой целиком.
        opens = min(opens, e.shift_end)
        opens_at[e.id] = opens
        time_dim.CumulVar(routing.Start(v)).SetRange(opens, e.shift_end)
        end = time_dim.CumulVar(routing.End(v))
        closes = e.shift_end if HARD_SHIFT_END else HORIZON
        # Крайний срок окончания дня. При перепланировании он отсчитывается от
        # УТРЕННЕГО выезда, а не от момента события: иначе бригаде, отработавшей
        # шесть часов, молча дарятся ещё двенадцать. Ограничение на span этого не
        # ловит — простой между событием и выездом в него не попадает.
        closes = min(closes, (end_by or {}).get(e.id, closes))
        if closes < opens:
            # День по нормативу закончился раньше, чем бригада может тронуться
            # (событие поздно вечером). Диапазон «конец не позже closes, но не
            # раньше выезда >= opens» пуст, и неразрешимой стала бы ВСЯ задача:
            # солвер не нашёл бы решения даже с отказом от всех заявок. Поэтому
            # бригада стоит и новых заявок не берёт.
            day_over.add(e.id)
            closes = opens
        end.SetRange(min(opens, closes), closes)
        # Ограничение длительности рабочего дня. Окно доступности широкое (09:30-23:30),
        # но день одной бригады не может тянуться всё окно: это наш нормативный выбор,
        # см. README, «Принятые допущения». Возврат в депо стоит 0 минут, поэтому span — это
        # ровно «от выезда до окончания последней работы».
        # При перепланировании остаток дня уже израсходован наполовину, и считать
        # его заново от момента события значило бы молча подарить бригаде лишние
        # часы сверх норматива.
        time_dim.SetSpanUpperBoundForVehicle(MAX_WORKDAY_MIN, v)

    # --- оборудование: запас выдан в офисе на весь день -> ограничение по ёмкости
    items = sorted({k for j in p.rd.jobs for k in j.equipment})
    used = capacity_used or {}
    for item in items:
        def make_eq_cb(item):
            def cb(i):
                n = manager.IndexToNode(i)
                if p.is_depot(n) or n in start_nodes:
                    return 0
                return p.jobs_by_node[n].equipment.get(item, 0)
            return cb
        # При перепланировании часть запаса уже отдана клиентам утром. Не
        # вычесть её — значит разрешить бригаде выдать роутеров больше, чем
        # она увезла из офиса, и узнать об этом от аудитора.
        caps = [max(0, e.equipment.get(item, 0) - used.get(e.id, {}).get(item, 0))
                for e in engs]
        routing.AddDimensionWithVehicleCapacity(
            routing.RegisterUnaryTransitCallback(make_eq_cb(item)), 0,
            caps, True, f"Eq:{item}")

    # --- депо, из которых никто не выезжает
    # Такой узел остаётся в матрице, но в маршруте ему делать нечего. Беда в том,
    # что узел, не входящий ни в одну дизъюнкцию, OR-Tools ОБЯЗАН посетить — и
    # посещает, пользуясь тем, что дорога до депо стоит ноль (правило написано для
    # возврата в конце маршрута). В середине маршрута это бесплатная телепортация:
    # солвер получает расписание, которое пересчёт воспроизвести не может, и план
    # проваливает аудит по окнам. Разрешаем такие узлы пропускать, штраф нулевой.
    # Одной дизъюнкции мало: она лишь РАЗРЕШАЕТ пропуск, а посещать такой узел
    # по-прежнему выгодно — бесплатный переезд экономит больше, чем стоит пропуск.
    # Поэтому узел ещё и запрещается к посещению: VehicleVar только -1.
    used_depots = set(starts) | set(ends)
    for node in range(p.n_depots):
        if node not in used_depots:
            idx_d = manager.NodeToIndex(node)
            routing.AddDisjunction([idx_d], 0)
            routing.VehicleVar(idx_d).SetValues([-1])

    # --- по каждой заявке: окно, допустимые бригады, право не назначать
    drop = W.drop_penalty(n_veh)
    unreachable = {}
    window_passed = {}            # заявка -> почему её окно уже не наступит
    for node, job in p.jobs_by_node.items():
        if node in start_nodes:
            continue                      # это стартовая точка, а не визит
        idx = manager.NodeToIndex(node)
        if job.id in skip_jobs:
            # Выполненное и отменённое в задаче не участвует, но узел обязан
            # быть куда-то пристроен: даём бесплатно его отбросить.
            routing.VehicleVar(idx).SetValues([-1])
            routing.AddDisjunction([idx], 0)
            continue
        cv = time_dim.CumulVar(idx)
        # Окно — на НАЧАЛО работ. При LATE_MAX_MIN = 0 оно жёсткое: заявка, которая
        # не помещается, честно уходит в неназначенные с причиной, а не планируется
        # с опозданием.
        # Нижняя граница — не только окно, но и момент события: начать работу
        # в прошлом нельзя. Выезд ограничен тем же моментом выше, у бригады;
        # здесь та же граница стоит на самой заявке — страховка, которая не
        # зависит от того, как задан выезд.
        floor = job.win_start if not_before is None else max(job.win_start, not_before)
        closes = min(HORIZON, job.win_end + LATE_MAX_MIN)
        if not_before is not None and not_before > closes:
            # Окно клиента закрылось раньше, чем случилось событие. Поднимать
            # верхнюю границу до момента события не годится: заявка с окном
            # 12:00-14:00 встала бы на 15:40 — с опозданием, о котором никто
            # не просил. Выполнить её после события нельзя
            # никак, и диспетчер должен услышать ровно эту причину, а не
            # «не помещается по времени».
            routing.VehicleVar(idx).SetValues([-1])
            routing.AddDisjunction([idx], 0)
            window_passed[job.id] = Verdict(
                False, Code.WINDOW_PASSED,
                f"окно {job.window_str} закончилось раньше события "
                f"в {hhmm(not_before)}: после события заявку не выполнить, "
                f"новое время согласуется с клиентом")
            continue
        cv.SetRange(floor, max(floor, closes))
        if LATE_MAX_MIN > 0:
            time_dim.SetCumulVarSoftUpperBound(idx, job.win_end, W.LATE_PENALTY_PER_MIN)
        if job.floating_window and job.skill == "emergency":
            # «Как можно раньше нужно выполнить. Это авария.» Мягкая граница ставится
            # в начало окна доступности, а не в середину дня: иначе до полудня у
            # солвера нет никакого стимула двигать аварию вперёд.
            time_dim.SetCumulVarSoftUpperBound(
                idx, SHIFT_WINDOW[0], W.EMERGENCY_ASAP_PENALTY_PER_MIN)

        allowed = [v for v, e in enumerate(engs)
                   if e.id not in blocked and e.id not in day_over
                   and check_static(job, e).ok]
        if not allowed:
            _, rejected = candidates(job, engs)
            unreachable[job.id] = rejected
        # -1 обязателен: без него заявку нельзя оставить неназначенной
        routing.VehicleVar(idx).SetValues([-1] + allowed)
        routing.AddDisjunction([idx], drop * W.PRIORITY_MULT.get(job.priority, 1))

        if fixed_assignment and job.id in fixed_assignment:
            v = fixed_assignment[job.id]
            if v is not None and v in allowed:
                routing.VehicleVar(idx).SetValues([v])

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(time_limit_s)
    params.log_search = log

    # Засев прежним планом. Без него «минимальные изменения» не работают:
    # поиск стартует с нуля, штраф за перетасовку лишь смещает ландшафт, и за
    # отпущенные секунды солвер просто не успевает вернуться к исходному
    # решению. С засевом прежний план — стартовая точка, и всякое отклонение
    # от него солвер обязан окупить.
    # Маршруты засева — это ИНДЕКСЫ модели, а не номера узлов матрицы. Пока
    # все бригады стартуют из депо, они совпадают, и путаницы не видно. Но при
    # перепланировании бригада стартует с точки последней заявки, узлы-старты
    # получают свои индексы, и номера узлов съезжают: засев номерами узлов
    # отдал бы заявки не тем бригадам, OR-Tools ответил бы «Vehicle 1 is not
    # allowed at index 62», и «минимальные изменения» молча отключились бы.
    if on_solution is not None:
        routing.AddAtSolutionCallback(
            _progress_reporter(p, manager, routing, n_veh, on_solution))

    sol, warm = None, None
    if seed_routes:
        initial = [[manager.NodeToIndex(p.node_of_job[j])
                    for j in seed_routes.get(e.id, []) if j in p.node_of_job]
                   for e in engs]
        start_assignment = routing.ReadAssignmentFromRoutes(initial, True)
        warm = start_assignment is not None
        if start_assignment is not None:
            sol = routing.SolveFromAssignmentWithParameters(start_assignment, params)
    if sol is None:
        sol = routing.SolveWithParameters(params)
    if sol is None:
        raise RuntimeError(f"{p.rd.name}: решение не найдено за {time_limit_s} с")

    plan = _extract(p, manager, routing, sol, unreachable, skip_jobs,
                    start_at or {}, opens_at, end_by or {}, algo,
                    blocked=blocked, capacity_used=used, not_before=not_before)
    # Причина «окно закрылось до события» точнее любой, которую можно вывести
    # из маршрутов, поэтому ставится поверх: выполнить такую заявку после
    # события нельзя ни при каком плане.
    for u in plan.unassigned:
        v = window_passed.get(u.job_id)
        if v is not None:
            u.code, u.reason, u.detail = v.code, REASON_RU[v.code], v.text
    if warm is not None:
        # Не принятый засев — не авария, но разница «до/после» тогда шире
        # обычной. Молчать об этом нельзя: сказано в плане, проверено тестом.
        plan.meta["warm_start"] = warm
    return plan


def _progress_reporter(p, manager, routing, n_veh, report):
    """Отклик на каждый найденный план: сколько секунд прошло и что в плане.

    Внутри отклика переменные модели связаны с текущим решением, поэтому
    маршрут читается через NextVar(...).Value(). Километры — по той же
    матрице и без обратного плеча, как в обязательной метрике.
    """
    import time
    t0 = time.monotonic()

    def cb():
        used, meters, visited = 0, 0, 0
        for v in range(n_veh):
            idx = routing.Start(v)
            nxt = routing.NextVar(idx).Value()
            if routing.IsEnd(nxt):
                continue
            used += 1
            while not routing.IsEnd(nxt):
                a, b = manager.IndexToNode(idx), manager.IndexToNode(nxt)
                meters += p.travel.dist[a][b]
                visited += 1
                idx, nxt = nxt, routing.NextVar(nxt).Value()
        report(dict(seconds=round(time.monotonic() - t0, 2),
                    cost=routing.CostVar().Value(), engineers=used,
                    km=round(meters / 1000.0, 2), assigned=visited))
    return cb


def _extract(p, manager, routing, sol, unreachable, skip_jobs=(), start_at=None,
             available_from=None, end_by=None, algo="solver", blocked=(),
             capacity_used=None, not_before=None) -> Plan:
    start_at, available_from, end_by = start_at or {}, available_from or {}, end_by or {}
    routes, assigned = [], set()
    for v, eng in enumerate(p.engineers):
        seq = []
        idx = routing.Start(v)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if not p.is_depot(node) and idx != routing.Start(v):
                seq.append(p.jobs_by_node[node])
                assigned.add(p.jobs_by_node[node].id)
            idx = sol.Value(routing.NextVar(idx))
        routes.append(replay_route(eng, seq, p.travel, p.rd.depots,
                                   start_key=start_at.get(eng.id),
                                   earliest=available_from.get(eng.id),
                                   finish_by=end_by.get(eng.id)))

    # Причина — по каждой заявке своя (ТЗ 2.2), и та же функция объясняет её
    # в карточке: список «Не назначено» и разбор по клику не расходятся.
    # Разбор идёт по тому же дню бригады, который видел планировщик: после
    # события — с точки, где бригада закончила работу, не раньше события и с
    # тем запасом оборудования, что остался после утра.
    from .explain import solver_days, unassigned_reason
    days = solver_days(p, routes, start_at, available_from, end_by,
                       capacity_used, blocked, not_before)
    unassigned = [unassigned_reason(j, p, days, not_before=not_before)[0]
                  for j in p.rd.jobs
                  if j.id not in assigned and j.id not in skip_jobs]
    return Plan(region=p.rd.name, algo=algo, routes=routes, unassigned=unassigned,
                meta=dict(objective=sol.ObjectiveValue(), fleet_size=len(p.engineers)))


def build_problem(region: str):
    from ..io.csv_loader import load_region
    rd = load_region(region)
    engs = load_fleet(region)
    rd.engineers = engs
    return Problem(rd, engs, TravelModel.load(region))


def subset_fleet(engineers: list, keep: int) -> list:
    """Урезанный парк из `keep` бригад, порядок исходный.

    Наивное «взять первые N» выключило бы целый кластер: в Юго-востоке бригад
    Каширы всего три, и при keep=8 все 20 заявок Каширы ушли бы в неназначенные
    по причине «нет ни одного исполнителя». Кривая «парк -> результат» после
    такого показывает не дефицит ресурса, а дыру в географии. Поэтому места
    распределяются между кластерами пропорционально их доле в полном парке
    (метод наибольших остатков), и пока места есть — каждый кластер получает
    хотя бы одну бригаду.
    """
    keep = max(0, min(keep, len(engineers)))
    order = {e.id: i for i, e in enumerate(engineers)}
    by_cluster: dict = {}
    for e in engineers:
        by_cluster.setdefault(e.cluster, []).append(e)

    quotas, remainders = {}, []
    for cl, group in by_cluster.items():
        exact = keep * len(group) / len(engineers)
        quotas[cl] = min(len(group), int(exact))
        remainders.append((exact - int(exact), cl))
    # пока есть места — каждому непустому кластеру хотя бы одна бригада
    for cl in by_cluster:
        if quotas[cl] == 0 and sum(quotas.values()) < keep:
            quotas[cl] = 1
    for _, cl in sorted(remainders, reverse=True):
        while sum(quotas.values()) < keep and quotas[cl] < len(by_cluster[cl]):
            quotas[cl] += 1
            break
    i = 0
    while sum(quotas.values()) < keep:          # добор, если остатки не закрыли
        cl = list(by_cluster)[i % len(by_cluster)]
        if quotas[cl] < len(by_cluster[cl]):
            quotas[cl] += 1
        i += 1

    picked = [e for cl, group in by_cluster.items() for e in group[:quotas[cl]]]
    return sorted(picked, key=lambda e: order[e.id])


def fleet_for_curve(engineers: list, keep: int, plan) -> list:
    """Штат из `keep` бригад для кривой «штат -> результат»: кого оставить, если
    людей меньше. Сначала уходят бригады, у которых в основном плане нет заявок:
    основной план без них выполним. Если и работающих больше, чем мест, уходят
    наименее загруженные по числу заявок, но зона не остаётся без бригады, пока
    места есть. Порядок исходный.

    subset_fleet урезает иначе — по списку, не глядя на загрузку: он нужен
    кнопке «Оставить N бригад», где отказы показывают нарочно. Для кривой такое
    урезание неверно: при 13 бригадах на Юго-востоке выбывала работающая машина
    Каширы, а две бригады без заявок оставались, и кривая показывала потери там,
    где их нет."""
    keep = max(0, min(keep, len(engineers)))
    order = {e.id: i for i, e in enumerate(engineers)}
    load = {r.engineer_id: len(r.stops) for r in plan.routes}
    working = [e for e in engineers if load.get(e.id, 0) > 0]
    idle = [e for e in engineers if load.get(e.id, 0) == 0]
    if keep >= len(working):
        picked = working + idle[:keep - len(working)]
    else:
        picked = list(working)
        while len(picked) > keep:
            per_zone: dict = {}
            for e in picked:
                per_zone[e.cluster] = per_zone.get(e.cluster, 0) + 1
            spare = [e for e in picked if per_zone[e.cluster] > 1] or picked
            picked.remove(min(spare, key=lambda e: (load[e.id], order[e.id])))
    return sorted(picked, key=lambda e: order[e.id])


def solve_with_fleet(problem: Problem, keep: int, **kw) -> Plan:
    """Тот же солвер на урезанном парке. Нужен для кривой «парк -> результат»
    и для демонстрации причин неназначения: при полном парке неназначенных нет,
    и панель «что делать» нечем показать."""
    reduced = Problem(problem.rd, subset_fleet(problem.engineers, keep), problem.travel)
    plan = solve(reduced, **kw)
    plan.meta["fleet_kept"] = keep
    return plan
