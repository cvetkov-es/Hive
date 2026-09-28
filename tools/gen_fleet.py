# -*- coding: utf-8 -*-
"""Генератор справочника бригад.

Справочника исполнителей в исходных данных НЕТ. Разъяснение экспертов от 19.09
разрешает моделировать характеристики бригад самостоятельно, а демонстрацию влияния
квалификации на маршрут — ожидает.

Три требования, которые конфликтуют:
  1) реалистичность — состав парка похож на описанный постановщиком;
  2) ограничения должны РЕАЛЬНО связывать, иначе «вы их не проверяете»;
  3) план обязан оставаться выполнимым на 100%.

Поэтому парк не просто генерируется, а ПРИНИМАЕТСЯ: в конце запускается солвер, и
если план не закрывает все заявки или какая-то группа ограничений не отсекла ни одной
пары «заявка-бригада» в одиночку, парк не сохраняется.

Смена одна: деления на когорты в данных нет (позже 10:00 выезжают 5 бригад из 35, и
лишь у двух реальная нагрузка). Ограничение длительности дня задаётся в config.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import DATA, REGIONS, SHIFT_WINDOW, FLEET_SEED          # noqa: E402
from app.io.csv_loader import load_region                               # noqa: E402
from app.geo.cluster import CL_MOSCOW                                   # noqa: E402

# Размер парка — по контрольному дню: 12 / 12 / 11 бригад. На Юго-востоке на три
# больше: у Каширы и Домодедова свои базы, и каждой зоне нужны свои бригады
# (split_fleet). Запас сверх нужного — резерв под срочные вбросы и ручные
# переназначения. Базовый вариант ТЗ 2.3 считается на этом же парке, а сам ТЗ
# (раздел 6) требует, чтобы в данных был «хотя бы один конфликт, при котором
# простое последовательное распределение даёт менее эффективный план».
FLEET_SIZE = {"Восток": 12, "Юго-восток": 15, "Югоцентр": 11}

# Постановщик: «в реальности преимущественно пешеходы, которые пользуются
# общественным транспортом». Автомобилей меньшинство — но их должно хватать
# на работы, где автомобиль обязателен (линейные работы и аварии).
MOSCOW_MIX = ["car", "transit", "foot", "transit", "car", "foot", "transit", "bike",
              "car", "transit", "foot", "car"]

SURNAMES = ["Ефимов", "Ковалёв", "Панкратов", "Дегтярёв", "Сорокин", "Лыткин",
            "Ямщиков", "Бердников", "Гуляев", "Шестаков", "Русаков", "Наумов",
            "Черепанов", "Ильин", "Захарченко", "Меркулов", "Ушаков", "Тарасенко",
            "Кондратьев", "Зыков", "Пахомов", "Одинцов", "Бирюков", "Савельев"]

EQUIP_MARGIN = 1.5


def demand(rd):
    d = defaultdict(lambda: dict(jobs=0, minutes=0, emerg_min=0, car_min=0,
                                 skills=Counter(), equipment=Counter(),
                                 emerg_windows=Counter(), car_windows=Counter()))
    for j in rd.jobs:
        c = d[j.cluster]
        c["jobs"] += 1
        c["minutes"] += j.service_min
        c["skills"][j.skill] += 1
        if j.requires_transport == "car":
            c["car_min"] += j.service_min
            if not j.floating_window:
                c["car_windows"][(j.win_start, j.win_end)] += 1
        if j.skill == "emergency":
            c["emerg_min"] += j.service_min
            if not j.floating_window:
                c["emerg_windows"][(j.win_start, j.win_end)] += 1
        for k, v in j.equipment.items():
            c["equipment"][k] += v
    return d


def split_fleet(rd, total):
    dem = demand(rd)
    if len(dem) == 1:
        return {next(iter(dem)): total}
    alloc = {}
    for c in dem:
        if c == CL_MOSCOW:
            continue
        alloc[c] = max(2, -(-dem[c]["minutes"] // 480))
    alloc[CL_MOSCOW] = total - sum(alloc.values())
    if alloc[CL_MOSCOW] < 1:
        raise ValueError(f"{rd.name}: парк {total} мал для кластеров {alloc}")
    return alloc


def _quota(value, per, n, floor=1):
    return min(n, max(floor, -(-value // per)))


def build(region):
    rd = load_region(region)
    total = FLEET_SIZE[region]
    dem = demand(rd)
    alloc = split_fleet(rd, total)
    ss, se = SHIFT_WINDOW

    fleet, k = [], 0
    for cluster, n in sorted(alloc.items(), key=lambda kv: (kv[0] != CL_MOSCOW, kv[0])):
        depot = "office" if cluster == CL_MOSCOW else cluster
        d = dem[cluster]

        # Сколько бригад обязаны быть на автомобиле: по объёму работ, требующих авто,
        # и по числу аварий, делящих одно окно.
        concurrent = max((v for v in d["emerg_windows"].values()), default=0)
        n_emerg = min(n, max(1, -(-d["emerg_min"] // 360), concurrent)) if d["emerg_min"] \
            else (1 if n >= 2 else n)
        # Автомобилей должно хватать и по объёму, и по ОДНОВРЕМЕННОСТИ: четыре
        # линейные работы в одном двухчасовом окне не закроются одной машиной,
        # сколько бы свободных минут у неё ни было. Одна машина успевает в окне
        # не больше двух таких работ.
        car_concurrent = max((v for v in d["car_windows"].values()), default=0)
        n_car = max(_quota(d["car_min"], 420, n, floor=n_emerg),
                    min(n, -(-car_concurrent // 2)))

        crew = []
        for idx in range(n):
            k += 1
            if cluster != CL_MOSCOW:
                transport = "car"          # межгородские расстояния
            else:
                transport = "car" if idx < n_car else \
                    MOSCOW_MIX[idx % len(MOSCOW_MIX)] if MOSCOW_MIX[idx % len(MOSCOW_MIX)] != "car" \
                    else "transit"
            # Навыки: раскладка из ответа постановщика — у части все три, у части два,
            # у части один. Аварийный навык идёт ТОЛЬКО автомобилистам и распределяется
            # независимо от чего-либо ещё: привязка навыка к смене или к порядковому
            # номеру делает вечерние аварии неназначаемыми.
            skills = {"local", "connect"}
            if idx % 5 == 4:
                skills = {"local"}
            elif idx % 5 == 3:
                skills = {"connect"}
            if idx < n_emerg and transport == "car":
                skills = skills | {"emergency", "local"}
            crew.append(dict(
                id=f"BR-{region[:3].upper()}-{k:02d}",
                name=f"Бригада {SURNAMES[(k - 1) % len(SURNAMES)]}",
                depot=depot, cluster=cluster, skills=sorted(skills),
                transport=transport, shift_start=ss, shift_end=se, equipment={}))
        _stock(crew, d["equipment"])
        fleet.extend(crew)
    return rd, fleet


def _stock(crew, need):
    for item, cnt in sorted(need.items()):
        total = max(len(crew), int(cnt * EQUIP_MARGIN + 0.999))
        base, rest = divmod(total, len(crew))
        for i, e in enumerate(crew):
            e["equipment"][item] = base + (1 if i < rest else 0)


def accept(region, rd, fleet):
    """Приёмка парка. Парк, который не проходит, не сохраняется."""
    from app.models import Engineer
    from app.geo.travel import TravelModel
    from app.solver.engine import Problem, solve
    from app.solver.feasibility import check_static, Code

    engs = [Engineer(id=e["id"], name=e["name"], depot=e["depot"], cluster=e["cluster"],
                     skills=set(e["skills"]), transport=e["transport"],
                     shift_start=e["shift_start"], shift_end=e["shift_end"],
                     equipment=dict(e["equipment"])) for e in fleet]
    rd.engineers = engs
    problem = Problem(rd, engs, TravelModel.load(region))

    # 1. Каждая группа обязательных ограничений должна отсекать хотя бы одну пару
    #    В ОДИНОЧКУ. Иначе ограничение декоративно: код есть, а данных для него нет.
    alone = Counter()
    for j in rd.jobs:
        for e in engs:
            codes = []
            if j.skill not in e.skills:
                codes.append(Code.SKILL)
            if j.requires_transport and e.transport != j.requires_transport:
                codes.append(Code.TRANSPORT)
            if j.cluster != e.cluster:
                codes.append(Code.CLUSTER)
            if len(codes) == 1:
                alone[codes[0]] += 1
    # 2. Оборудование должно связывать: спрос кластера не должен быть намного ниже запаса
    eq_tight = {}
    for cl in {e["cluster"] for e in fleet}:
        crew = [e for e in fleet if e["cluster"] == cl]
        for item in ("router", "tvbox", "speaker"):
            need = sum(j.equipment.get(item, 0) for j in rd.jobs if j.cluster == cl)
            stock = sum(e["equipment"].get(item, 0) for e in crew)
            if need:
                eq_tight[f"{cl}/{item}"] = f"{need}/{stock}"

    plan = solve(problem, time_limit_s=40)
    report = dict(
        used=plan.used_engineers, fleet=len(engs), km=plan.total_km,
        assigned=plan.assigned, total=len(rd.jobs), unassigned=len(plan.unassigned),
        late=plan.late_jobs, alone=dict(alone), equipment=eq_tight,
        max_span=max((r.span_min for r in plan.routes if r.stops), default=0),
        spans=sorted((r.span_min for r in plan.routes if r.stops), reverse=True)[:3],
        unassigned_detail=[(u.job_id, u.code) for u in plan.unassigned[:8]])
    errs = []
    if plan.unassigned:
        errs.append(f"не назначено {len(plan.unassigned)} заявок: {report['unassigned_detail']}")
    for code in (Code.SKILL, Code.TRANSPORT):
        if not alone.get(code):
            errs.append(f"ограничение {code} не отсекает ни одной пары в одиночку — декоративно")
    return report, errs


def main():
    out = DATA / "fleet"
    out.mkdir(parents=True, exist_ok=True)
    failed = False
    for region in REGIONS:
        rd, fleet = build(region)
        report, errs = accept(region, rd, fleet)
        tr = Counter(e["transport"] for e in fleet)
        sk = Counter(s for e in fleet for s in e["skills"])
        print(f"\n{region}: парк {len(fleet)}  |  транспорт {dict(tr)}")
        print(f"   навыки {dict(sk)} | кластеры {dict(Counter(e['cluster'] for e in fleet))}")
        print(f"   ПЛАН: задействовано {report['used']}, пробег {report['km']} км, "
              f"назначено {report['assigned']}/{report['total']}, "
              f"опозданий {report['late']}, макс. день {report['max_span'] // 60}ч{report['max_span'] % 60:02d}м")
        print(f"   ограничения режут в одиночку: {report['alone']}")
        print(f"   оборудование спрос/запас: {report['equipment']}")
        if errs:
            failed = True
            for e in errs:
                print(f"   ОТКАЗ: {e}")
            continue
        (out / f"{region}.json").write_text(
            json.dumps(fleet, ensure_ascii=False, indent=1), encoding="utf-8")
        print("   парк принят и сохранён")
    if failed:
        print("\nПарк принят не для всех регионов. Несохранённые требуют правки.")
        sys.exit(1)


if __name__ == "__main__":
    main()
