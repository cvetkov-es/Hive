# -*- coding: utf-8 -*-
"""Формат входных данных по ТЗ 2.4 и справочники 2.4.1.

Наш рабочий вход — выгрузка Beekeeper: cp1251, колонки «Заявка» и «Тип заявки
BK», адрес офиса отдельной строкой в самом низу файла. Читать её умеет только
csv_loader, и это нормально для исходных данных хакатона. Но ТЗ 2.1.1 требует
другого: «загружать готовые тестовые данные из CSV или JSON» в формате из 2.4 —
то есть в таком, который эксперт составит сам и подаст на вход, не зная ничего
про Beekeeper.

Отсюда два разных входа и один внутренний Job. Здесь — второй вход.

Три решения, которые стоит назвать явно.

Справочники 2.4.1 закрытые и ПРОНУМЕРОВАННЫЕ, поэтому в файл пишется номер:
навык 1-3, транспорт 1-4, приоритет 1-2. На чтении принимается и номер, и
русское название, и наш внутренний код — эксперту, который правит файл руками,
не должно быть больно.

Приоритет наружу двухуровневый (Р6). Внутри уровней три, и они участвуют в
целевой функции, но справочник ТЗ знает только «обычная» и «срочная». При
чтении берётся БОЛЕЕ СРОЧНОЕ из двух: уровня по навыку (тем же правилом, что
у классификатора нормативов: авария срочная, подключение важнее ремонта) и
заявленного в колонке priority. «Срочная» в колонке поднимает заявку до
уровня аварии — ТЗ 2.4.1: «Срочная заявка имеет более высокий приоритет при
перепланировании»; «Обычная» ничего не понижает: аварийные работы остаются
аварией при любой колонке.

Зона обслуживания в справочниках ТЗ отсутствует, поэтому в файл не пишется:
иначе формат перестал бы быть форматом ТЗ и стал бы нашим внутренним. У заявки
она восстанавливается из адреса тем же классификатором, что и при чтении
выгрузки, у исполнителя — по близости стартовой точки к «дому» удалённого
кластера. Потерять её нельзя: без зоны подмосковные бригады станут московскими,
и солвер погонит их Москва-Кашира.
"""
from __future__ import annotations
import csv
import json
from pathlib import Path

from ..config import (PRIO_CONNECT, PRIO_EMERGENCY, PRIO_ROUTINE, SHIFT_WINDOW,
                      SKILL_RU, TRANSPORT_RU)
from ..geo.cluster import classify_cluster
from ..models import Depot, Engineer, Job, hhmm

SEP = ";"
ENC = "utf-8"

# --- справочники ТЗ 2.4.1 ----------------------------------------------------
SKILL_CODE = {"local": 1, "connect": 2, "emergency": 3}
CODE_SKILL = {v: k for k, v in SKILL_CODE.items()}
TRANSPORT_CODE = {"car": 1, "foot": 2, "bike": 3, "transit": 4}
CODE_TRANSPORT = {v: k for k, v in TRANSPORT_CODE.items()}
PRIORITY_RU = {1: "Обычная", 2: "Срочная"}

JOB_FIELDS = ["id", "lat", "lon", "address", "duration_min", "window_start",
              "window_end", "priority", "required_skill", "required_transport",
              "equipment"]
ENGINEER_FIELDS = ["id", "name", "depot_lat", "depot_lon", "shift_start",
                   "shift_end", "skills", "transport", "equipment"]


def cluster_of_point(lat: float, lon: float, jobs=None) -> str:
    """Зона обслуживания стартовой точки.

    В справочниках ТЗ зоны нет, и писать её в файл значило бы подменить формат
    ТЗ нашим внутренним. Но и терять её нельзя: без зоны подмосковные бригады
    станут московскими, и солвер погонит их Москва-Кашира.

    Восстанавливается по БЛИЖАЙШЕЙ ЗАЯВКЕ, а не по расстоянию до «дома»
    удалённого кластера. Порог по расстоянию здесь не работает в принципе:
    «дом» в Домодедово лежит в 19 км от офиса Юго-востока на Бирюлёвской, и
    любой радиус, накрывающий Домодедово, забирает туда же весь московский
    парк — проверено, 32 заявки из 83 вместо всех.

    Заявки свою зону знают из адреса, и точка наследует зону соседей: офис
    окружён московскими заявками, «дом» в Кашире — каширскими.
    """
    from ..geo.cluster import CL_MOSCOW
    from ..geo.travel import haversine_km
    if not jobs:
        return CL_MOSCOW
    nearest = min(jobs, key=lambda j: haversine_km(lat, lon, j.lat, j.lon))
    return nearest.cluster or CL_MOSCOW


def spec_priority(internal: int) -> int:
    """Внутренние три уровня -> справочник ТЗ из двух значений (Р6)."""
    return 2 if internal == PRIO_EMERGENCY else 1


def _internal_priority(skill: str, declared=None) -> int:
    """Внутренний уровень: более срочный из навыка и заявленного приоритета.

    По навыку — потому что справочник ТЗ двухуровневый и различить
    «подключение» и «ремонт» не может, а в целевой функции они весят
    по-разному. По колонке — потому что она есть в формате ТЗ 2.4 и эксперт,
    выставивший «Срочная», вправе увидеть реакцию. Меньше число — срочнее."""
    by_skill = {"emergency": PRIO_EMERGENCY, "connect": PRIO_CONNECT}.get(
        skill, PRIO_ROUTINE)
    if _declared_priority(declared) == 2:
        return min(by_skill, PRIO_EMERGENCY)
    return by_skill


def _declared_priority(v) -> int | None:
    """Колонка priority по справочнику ТЗ 2.4.1: номер или название. Пусто —
    приоритет не заявлен, остаётся уровень по навыку."""
    s = str(v if v is not None else "").strip()
    if not s:
        return None
    if s.isdigit() and int(s) in PRIORITY_RU:
        return int(s)
    for code, ru in PRIORITY_RU.items():
        if ru.lower() == s.lower():
            return code
    raise ValueError(f"неизвестный приоритет «{s}», справочник ТЗ 2.4.1: "
                     + ", ".join(f"{c} {ru}" for c, ru in PRIORITY_RU.items()))


# --- разбор значений ---------------------------------------------------------

def _hhmm_to_min(s: str) -> int:
    """ЧЧ:ММ от 00:00 до 23:59 либо минуты от полуночи целым числом. Всё
    остальное — ошибка с текстом: иначе «99:99» молча стало бы 6039-й
    минутой суток."""
    s = str(s if s is not None else "").strip()
    if not s:
        raise ValueError("пустое время, ожидался формат ЧЧ:ММ")
    try:
        if ":" not in s:
            m = int(s)
            if not 0 <= m < 24 * 60:
                raise ValueError
            return m
        h, mm = s.split(":")
        if not (h.isdigit() and mm.isdigit() and len(mm) == 2
                and int(h) < 24 and int(mm) < 60):
            raise ValueError
        return int(h) * 60 + int(mm)
    except ValueError:
        raise ValueError(f"время «{s}» — ожидался формат ЧЧ:ММ от 00:00 до "
                         f"23:59") from None


def _skill(v: str) -> str:
    v = (v or "").strip()
    if v.isdigit():
        return CODE_SKILL[int(v)]
    if v in SKILL_CODE:
        return v
    for key, ru in SKILL_RU.items():                 # русское название
        if ru.lower() == v.lower():
            return key
    raise ValueError(f"неизвестный навык «{v}», справочник ТЗ 2.4.1: "
                     f"1 {SKILL_RU['local']}, 2 {SKILL_RU['connect']}, "
                     f"3 {SKILL_RU['emergency']}")


def _transport(v: str, required: bool = True) -> str | None:
    v = (v or "").strip()
    if not v or v in ("-", "—"):
        if required:
            raise ValueError("у исполнителя обязан быть тип транспорта")
        return None
    if v.isdigit():
        return CODE_TRANSPORT[int(v)]
    if v in TRANSPORT_CODE:
        return v
    for key, ru in TRANSPORT_RU.items():
        if ru.lower() == v.lower():
            return key
    raise ValueError(f"неизвестный транспорт «{v}», справочник ТЗ 2.4.1: "
                     + ", ".join(f"{c} {TRANSPORT_RU[k]}"
                                 for k, c in TRANSPORT_CODE.items()))


def _equipment(v: str) -> dict:
    out: dict = {}
    for part in (v or "").split("|"):
        part = part.strip()
        if not part:
            continue
        item, _, n = part.partition(":")
        out[item.strip()] = int(n or 1)
    return out


def _fmt_equipment(eq: dict) -> str:
    return "|".join(f"{k}:{n}" for k, n in sorted(eq.items()))


# --- выгрузка ----------------------------------------------------------------

def dump_jobs(jobs, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=ENC, newline="") as f:
        w = csv.writer(f, delimiter=SEP)
        w.writerow(JOB_FIELDS)
        for j in jobs:
            w.writerow([j.id, f"{j.lat:.6f}", f"{j.lon:.6f}", j.address,
                        j.service_min, hhmm(j.win_start), hhmm(j.win_end),
                        spec_priority(j.priority), SKILL_CODE[j.skill],
                        TRANSPORT_CODE[j.requires_transport]
                        if j.requires_transport else "",
                        _fmt_equipment(j.equipment)])
    return path


def dump_engineers(engineers, path, depots: dict) -> Path:
    """`depots` — {ключ: Depot} из задачи. Стартовая точка в формате ТЗ это
    координаты, а не наш внутренний ключ депо: файл должен читаться тем, кто
    про наши депо ничего не знает."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=ENC, newline="") as f:
        w = csv.writer(f, delimiter=SEP)
        w.writerow(ENGINEER_FIELDS)
        for e in engineers:
            d = depots[e.depot]
            lat, lon = d.lat, d.lon
            w.writerow([e.id, e.name, f"{lat:.6f}", f"{lon:.6f}",
                        hhmm(e.shift_start), hhmm(e.shift_end),
                        "|".join(str(SKILL_CODE[s]) for s in sorted(e.skills)),
                        TRANSPORT_CODE[e.transport], _fmt_equipment(e.equipment)])
    return path


def dump_events(problem, path, at_min: int = 15 * 60 + 40) -> Path:
    """Три события ТЗ 2.4 готовыми к подаче на вход."""
    from ..solver.replan import make_demo_emergency
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    job = make_demo_emergency(problem, at_min)
    victim = problem.rd.jobs[len(problem.rd.jobs) // 2]
    events = [
        dict(type="urgent", at=hhmm(at_min), job=_job_dict(job)),
        dict(type="cancel", at=hhmm(at_min), job_id=victim.id),
        dict(type="unavailable", at=hhmm(at_min),
             engineer_id=problem.engineers[0].id),
    ]
    path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding=ENC)
    return path


def _job_dict(j) -> dict:
    return dict(id=j.id, lat=round(j.lat, 6), lon=round(j.lon, 6),
                address=j.address, duration_min=j.service_min,
                window_start=hhmm(j.win_start), window_end=hhmm(j.win_end),
                priority=spec_priority(j.priority),
                required_skill=SKILL_CODE[j.skill],
                required_transport=TRANSPORT_CODE[j.requires_transport]
                if j.requires_transport else None,
                equipment=dict(j.equipment))


# --- чтение ------------------------------------------------------------------

def _rows(path):
    with open(Path(path), encoding=ENC, newline="") as f:
        return list(csv.DictReader(f, delimiter=SEP))


def load_jobs(path) -> list:
    out = []
    for i, r in enumerate(_rows(path), 2):
        try:
            skill = _skill(r["required_skill"])
            ws, we = _hhmm_to_min(r["window_start"]), _hhmm_to_min(r["window_end"])
            address = (r.get("address") or "").strip()
            out.append(Job(
                id=r["id"].strip(), address=address,
                lat=float(r["lat"]), lon=float(r["lon"]), geo_level=0,
                district="", bk="", hd="", skill=skill,
                service_min=int(r["duration_min"]),
                equipment=_equipment(r.get("equipment", "")),
                priority=_internal_priority(skill, r.get("priority")),
                win_start=ws, win_end=we, floating_window=(we - ws) > 10 * 60,
                cluster=classify_cluster(address),
                requires_transport=_transport(r.get("required_transport", ""),
                                              required=False)))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{Path(path).name}, строка {i}: {exc}") from exc
    return out


def load_engineers(path, jobs=None) -> list:
    """`jobs` нужны только для восстановления зоны обслуживания: без них все
    исполнители считаются московскими, и подмосковный кластер разваливается."""
    out, depots = [], {}
    for i, r in enumerate(_rows(path), 2):
        try:
            skills = {_skill(s) for s in r["skills"].split("|") if s.strip()}
            if not 1 <= len(skills) <= 3:
                raise ValueError(f"навыков {len(skills)}, ТЗ 2.4.1 разрешает 1-3")
            lat, lon = float(r["depot_lat"]), float(r["depot_lon"])
            key = f"{lat:.5f},{lon:.5f}"
            zone = cluster_of_point(lat, lon, jobs)
            depots.setdefault(key, Depot(key=key, name=f"Стартовая точка {key}",
                                         lat=lat, lon=lon, cluster=zone))
            e = Engineer(id=r["id"].strip(), name=(r.get("name") or r["id"]).strip(),
                         depot=key, cluster=zone,
                         skills=skills, transport=_transport(r["transport"]),
                         shift_start=_hhmm_to_min(r.get("shift_start")
                                                  or hhmm(SHIFT_WINDOW[0])),
                         shift_end=_hhmm_to_min(r.get("shift_end")
                                                or hhmm(SHIFT_WINDOW[1])),
                         equipment=_equipment(r.get("equipment", "")))
            out.append(e)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{Path(path).name}, строка {i}: {exc}") from exc
    return out


def load_events(path) -> list:
    """События ТЗ 2.4 -> replan.Event."""
    from ..solver.replan import Event
    raw = json.loads(Path(path).read_text(encoding=ENC))
    out = []
    for e in raw:
        job = None
        if e.get("job"):
            d = dict(e["job"])
            skill = _skill(str(d["required_skill"]))
            address = d.get("address", "")
            job = Job(id=str(d["id"]), address=address,
                      lat=float(d["lat"]), lon=float(d["lon"]), geo_level=0,
                      district="", bk="", hd="", skill=skill,
                      service_min=int(d["duration_min"]),
                      equipment=dict(d.get("equipment") or {}),
                      priority=_internal_priority(skill, d.get("priority")),
                      win_start=_hhmm_to_min(d["window_start"]),
                      win_end=_hhmm_to_min(d["window_end"]),
                      floating_window=False, cluster=classify_cluster(address),
                      requires_transport=_transport(
                          str(d.get("required_transport") or ""), required=False))
        out.append(Event(kind=e["type"], at_min=_hhmm_to_min(e["at"]), job=job,
                         job_id=e.get("job_id"), engineer_id=e.get("engineer_id")))
    return out


# --- задача целиком из файлов формата ТЗ -------------------------------------

def build_problem(jobs_path, engineers_path):
    """Задача из пары CSV формата ТЗ 2.4 — вход по требованию ТЗ 2.1.1.

    Дорожной матрицы для произвольного набора адресов у нас нет и быть не
    может: она считается офлайн по нашим трём регионам. Поэтому расстояния
    берутся по прямой с коэффициентом извилистости. Это ХУЖЕ, чем по дорогам,
    и об этом обязана сообщать плашка в интерфейсе — но это работает без сети
    и на любых данных, которые подаст эксперт, а отказ «нет матрицы» не работает
    вообще.
    """
    from ..geo.travel import TravelModel
    from ..solver.engine import Problem
    from ..models import RegionData

    jobs = load_jobs(jobs_path)
    engineers = load_engineers(engineers_path, jobs)
    if not jobs:
        raise ValueError(f"{Path(jobs_path).name}: ни одной заявки")
    if not engineers:
        raise ValueError(f"{Path(engineers_path).name}: ни одного исполнителя")

    depots = {}
    for e in engineers:
        if e.depot not in depots:
            lat, lon = (float(x) for x in e.depot.split(","))
            depots[e.depot] = Depot(key=e.depot, name=f"Стартовая точка {e.depot}",
                                    lat=lat, lon=lon, cluster=e.cluster,
                                    is_office=not depots)

    points = ([dict(key=f"depot:{k}", lat=d.lat, lon=d.lon) for k, d in depots.items()]
              + [dict(key=f"job:{j.id}", lat=j.lat, lon=j.lon) for j in jobs])
    travel = TravelModel.estimated(points)

    rd = RegionData(name=Path(jobs_path).parent.name or "Набор из файла",
                    office_address="", depots=depots, jobs=jobs,
                    engineers=engineers)
    return Problem(rd, engineers, travel)
