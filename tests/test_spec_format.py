# -*- coding: utf-8 -*-
"""Формат входных данных ТЗ 2.4 и формат результата ТЗ 2.4.2.

Наши рабочие файлы — выгрузки Beekeeper в cp1251 с колонками «Заявка», «Тип
заявки BK» и адресом офиса отдельной строкой внизу. Читать их умеем только мы.
ТЗ 2.1.1 требует другого: «загружать готовые тестовые данные из CSV или JSON»
в формате, описанном в 2.4, — то есть в том, который эксперт может составить
сам и подать на вход.

Проверка здесь одна и она круговая: выгрузить наш набор в формат ТЗ, прочитать
обратно и убедиться, что получилась та же задача. Тест на «файл записался»
ничего не стоит: записать можно и мусор.

Отдельно проверяются справочники 2.4.1. Их три, все закрытые, и у приоритета
значений ДВА, а не три: внутри модели уровней три (Р6), наружу отдаётся
справочник ТЗ.
"""
import json

import pytest

from app.io.export import plan_to_csv, plan_to_json
from app.io.spec_format import (SKILL_CODE, TRANSPORT_CODE, dump_engineers,
                                dump_events, dump_jobs, load_engineers,
                                load_jobs, spec_priority)

from helpers import problem, solver_plan


@pytest.fixture(scope="module")
def dumped(tmp_path_factory):
    p = problem("Югоцентр")
    d = tmp_path_factory.mktemp("spec")
    dump_jobs(p.rd.jobs, d / "jobs.csv")
    dump_engineers(p.engineers, d / "engineers.csv", p.rd.depots)
    dump_events(p, d / "events.json")
    return p, d


def test_roundtrip_through_spec_format(dumped):
    """Выгруженный набор читается обратно и даёт ту же задачу."""
    p, d = dumped
    jobs = load_jobs(d / "jobs.csv")
    engs = load_engineers(d / "engineers.csv")
    assert len(jobs) == len(p.rd.jobs)
    assert {j.id for j in jobs} == {j.id for j in p.rd.jobs}
    for j in jobs:
        orig = p.rd.job_by_id(j.id)
        assert (j.service_min, j.win_start, j.win_end, j.skill) == \
               (orig.service_min, orig.win_start, orig.win_end, orig.skill)
        assert (round(j.lat, 6), round(j.lon, 6)) == \
               (round(orig.lat, 6), round(orig.lon, 6))
        assert j.requires_transport == orig.requires_transport
        assert j.equipment == orig.equipment
    assert len(engs) == len(p.engineers)
    for e in engs:
        orig = next(x for x in p.engineers if x.id == e.id)
        assert (e.skills, e.transport, e.shift_start, e.shift_end) == \
               (orig.skills, orig.transport, orig.shift_start, orig.shift_end)
        assert e.equipment == orig.equipment


def test_job_has_every_mandatory_field(dumped):
    """ТЗ 2.4: ID, координаты или адрес, длительность, начало и конец окна,
    приоритет, требуемый навык, требуемый транспорт (при наличии)."""
    _, d = dumped
    head = (d / "jobs.csv").read_text(encoding="utf-8").splitlines()[0]
    need = {"id", "lat", "lon", "address", "duration_min", "window_start",
            "window_end", "priority", "required_skill", "required_transport"}
    assert need <= set(head.split(";"))


def test_engineer_has_every_mandatory_field(dumped):
    """ТЗ 2.4: ID/имя, стартовая точка, начало и конец смены, 1-3 навыка,
    тип транспортного средства."""
    _, d = dumped
    head = (d / "engineers.csv").read_text(encoding="utf-8").splitlines()[0]
    need = {"id", "name", "depot_lat", "depot_lon", "shift_start", "shift_end",
            "skills", "transport"}
    assert need <= set(head.split(";"))


def test_engineer_skill_cardinality(dumped):
    """«У исполнителя может быть от 1-го до 3-х навыков одновременно.»"""
    _, d = dumped
    for e in load_engineers(d / "engineers.csv"):
        assert 1 <= len(e.skills) <= 3, f"{e.id}: навыков {len(e.skills)}"


def test_reference_books_are_closed():
    """Справочники 2.4.1 закрытые и пронумерованные."""
    assert sorted(SKILL_CODE.values()) == [1, 2, 3]
    assert sorted(TRANSPORT_CODE.values()) == [1, 2, 3, 4]


def test_priority_is_binary_outside(dumped):
    """Р6: внутри три уровня, наружу справочник ТЗ из двух значений."""
    p, d = dumped
    assert {spec_priority(j.priority) for j in p.rd.jobs} <= {1, 2}
    rows = (d / "jobs.csv").read_text(encoding="utf-8").splitlines()[1:]
    col = (d / "jobs.csv").read_text(encoding="utf-8").splitlines()[0] \
        .split(";").index("priority")
    assert {r.split(";")[col] for r in rows} <= {"1", "2"}


def test_units_follow_the_recommendation(dumped):
    """ТЗ 2.4: время — HH:MM, длительность — минуты, координаты — широта/долгота."""
    _, d = dumped
    lines = (d / "jobs.csv").read_text(encoding="utf-8").splitlines()
    head, row = lines[0].split(";"), lines[1].split(";")
    assert ":" in row[head.index("window_start")]
    assert row[head.index("duration_min")].isdigit()
    assert 30 < float(row[head.index("lat")]) < 80


def test_events_file_matches_the_spec(dumped):
    """ТЗ 2.4: тип события, время, ID заявки или инженера; для срочной —
    полный набор полей заявки."""
    _, d = dumped
    events = json.loads((d / "events.json").read_text(encoding="utf-8"))
    kinds = {e["type"] for e in events}
    assert kinds == {"urgent", "cancel", "unavailable"}
    for e in events:
        assert ":" in e["at"]
        if e["type"] == "urgent":
            assert {"id", "lat", "lon", "duration_min", "window_start",
                    "window_end", "priority", "required_skill"} <= set(e["job"])
        elif e["type"] == "cancel":
            assert e["job_id"]
        else:
            assert e["engineer_id"]


def test_exported_plan_carries_every_required_field():
    """ТЗ 2.4.2: по исполнителю — упорядоченный список с прибытием, началом и
    пробегом; по заявке — исполнитель либо «не назначена» с причиной; по плану —
    число задействованных и пробег по каждому и суммарно."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    d = plan_to_json(plan, p)
    assert {"used_engineers", "total_km", "routes", "jobs", "unassigned"} <= set(d)
    assert d["used_engineers"] == plan.used_engineers
    assert d["total_km"] == plan.total_km
    for r in d["routes"]:
        assert {"engineer_id", "km", "stops"} <= set(r)
        assert [s["seq"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1))
        for s in r["stops"]:
            assert {"job_id", "seq", "arrive", "start", "km"} <= set(s)
            assert ":" in s["arrive"] and ":" in s["start"]
    assert len(d["jobs"]) == len(p.rd.jobs), "разрез по заявкам обязан быть полным"
    for row in d["jobs"]:
        assert row["engineer_id"] or row["status"] == "не назначена"
        if not row["engineer_id"]:
            assert row["reason"]
    assert round(sum(r["km"] for r in d["routes"]), 2) == d["total_km"]


def test_exported_csv_is_readable_and_complete():
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    text = plan_to_csv(plan, p)
    lines = [l for l in text.splitlines() if l.strip()]
    head = lines[0].split(";")
    assert {"engineer_id", "seq", "job_id", "address", "window",
            "arrive", "start", "km"} <= set(head)
    body = [l for l in lines[1:] if not l.startswith("#")]
    assert len(body) == plan.assigned + len(plan.unassigned)


def test_spec_dataset_can_actually_be_planned(dumped):
    """ТЗ 2.1.1: «загружать готовые тестовые данные из CSV или JSON».

    Прочитать файл мало — по нему надо построить план, иначе требование
    закрыто декоративно. Дорожной матрицы для произвольных адресов нет,
    поэтому расстояния оценочные, и это единственное отличие.
    """
    from app.io.spec_format import build_problem
    from app.solver.audit import audit_plan
    from app.solver.engine import solve

    _, d = dumped
    p = build_problem(d / "jobs.csv", d / "engineers.csv")
    assert len(p.rd.jobs) == 56
    plan = solve(p, time_limit_s=10)
    assert plan.assigned > 0
    rep = audit_plan(plan, p)
    assert rep.passed == rep.total, rep.violations
    assert p.travel.is_estimated(f"job:{p.rd.jobs[0].id}")


def test_zones_survive_the_roundtrip(tmp_path):
    """Зона обслуживания в справочниках ТЗ отсутствует и восстанавливается по
    ближайшей заявке. Проверяется на Юго-востоке — единственном регионе, где
    зон больше одной.

    Радиус до «дома» удалённого кластера здесь не годится: «дом» в Домодедово
    лежит в 19 км от офиса на Бирюлёвской, весь московский парк уехал бы в
    Домодедово, и план дал бы 32 заявки из 83.
    """
    from app.io.spec_format import build_problem, dump_engineers, dump_jobs

    p = problem("Юго-восток")
    dump_jobs(p.rd.jobs, tmp_path / "jobs.csv")
    dump_engineers(p.engineers, tmp_path / "engineers.csv", p.rd.depots)
    back = build_problem(tmp_path / "jobs.csv", tmp_path / "engineers.csv")

    was = {e.id: e.cluster for e in p.engineers}
    now = {e.id: e.cluster for e in back.engineers}
    assert now == was, "зона обслуживания исполнителей не пережила выгрузку"

    from collections import Counter
    assert Counter(j.cluster for j in back.rd.jobs) == \
           Counter(j.cluster for j in p.rd.jobs)


# --- время -------------------------------------------------------------------

def _event_file(tmp_path, **job):
    body = dict(id="N-1", lat=55.66, lon=37.62, address="Москва, тест",
                duration_min=40, window_start="16:00", window_end="18:00",
                required_skill="Локальные работы")
    body.update(job)
    path = tmp_path / "events.json"
    path.write_text(json.dumps([dict(type="urgent", at="15:40", job=body)],
                               ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("bad", ["99:99", "24:00", "12:60", "abc", "-1", "1440"])
def test_time_outside_the_day_is_refused(tmp_path, bad):
    """«99:99» — отказ, а не молча 6039-я минута суток."""
    from app.io.spec_format import load_events
    with pytest.raises(ValueError, match="ЧЧ:ММ"):
        load_events(_event_file(tmp_path, window_end=bad))


# --- приоритет из колонки ---------------------------------------------------

@pytest.mark.parametrize("declared, skill, want", [
    (2, "Локальные работы", 1),            # «Срочная» поднимает до уровня аварии
    ("Срочная", "Локальные работы", 1),    # название справочника тоже читается
    (1, "Локальные работы", 3),            # «Обычная» — уровень по навыку
    (None, "Работы на подключение и дозаказы", 2),
    (1, "Аварийные работы", 1),            # «Обычная» аварию не понижает
])
def test_declared_priority_is_read(tmp_path, declared, skill, want):
    """ТЗ 2.4.1: «Срочная заявка имеет более высокий приоритет при
    перепланировании». Колонка читается: priority=2 не планируется молча как
    обычная. Правило: более срочное из навыка и колонки."""
    from app.io.spec_format import load_events
    extra = {} if declared is None else dict(priority=declared)
    ev = load_events(_event_file(tmp_path, required_skill=skill, **extra))[0]
    assert ev.job.priority == want
    assert ev.mode == ("replan" if want == 1 else "insert")


def test_declared_priority_in_the_jobs_file(dumped, tmp_path):
    """То же правило в CSV заявок: колонка priority=2 делает заявку срочной."""
    _, d = dumped
    lines = (d / "jobs.csv").read_text(encoding="utf-8").splitlines()
    head = lines[0].split(";")
    col, skill_col = head.index("priority"), head.index("required_skill")
    row = next(r.split(";") for r in lines[1:] if r.split(";")[skill_col] == "1")
    row[col] = "2"
    (tmp_path / "jobs.csv").write_text("\n".join([lines[0], ";".join(row)]) + "\n",
                                      encoding="utf-8")
    assert load_jobs(tmp_path / "jobs.csv")[0].priority == 1


def test_unknown_priority_is_refused(tmp_path):
    from app.io.spec_format import load_events
    with pytest.raises(ValueError, match="приоритет"):
        load_events(_event_file(tmp_path, priority=5))
