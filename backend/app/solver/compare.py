# -*- coding: utf-8 -*-
"""Сравнение планов между собой и с контрольным днём.

Три колонки. «Наш план» и «Базовый вариант» посчитаны нами на одних и тех же
входных данных и одних и тех же ограничениях. «Контрольный день» — контрольное
распределение, то есть то, как регион отработал на самом деле.

Почему «контрольный», а не «живой день 17.08». Эксперты написали прямо:
«Контрольная выборка отражает фактическое распределение заявок и работу
бригад в один из реальных дней сентября в Москве на рассматриваемых участках»
(куратор, 19.09). Дата в файлах, значит, обезличена, и подписывать колонку ею
значило бы выдать условную дату за настоящую.

Две оговорки, без которых сравнение превращается в подтасовку, и обе живут
прямо в выводе, а не в устном комментарии:

  * пробег контрольного дня НЕВЫЧИСЛИМ. В контрольном файле есть бригада, но
    нет порядка объезда, а пробег зависит именно от порядка. Любое число здесь
    было бы выдумано;
  * распределено в контрольном дне меньше, потому что часть заявок отменена
    клиентом, а часть так и не отправлена бригадам. Сравнивать «56 у нас
    против 51 у них» нельзя: мы планируем все заявки дня, а отмена — это
    событие, которого на момент планирования ещё не было. Поэтому колонка
    раскладывает все строки файла без остатка: распределено + отменено
    клиентом + не отправлено = всего.
"""
from __future__ import annotations

from ..io.csv_loader import load_control
from .metrics import summarize

NA = "—"
# «Реальный день», а не «контрольный»: на экране слово должно объяснять само
# себя. Откуда данные, говорит CONTROL_ABOUT — там и контрольная выборка.
TITLES = {"solver": "Наш план", "baseline": "Базовый вариант (п. 2.3 ТЗ)",
          "control": "Реальный день"}
CONTROL_ABOUT = (
    "Реальный день — как этот участок на самом деле отработал день, который мы "
    "планируем: кто из бригад какие заявки взял и чем они закончились. Это "
    "контрольная выборка организаторов; по их словам, один из рабочих дней "
    "сентября, дату в файлах скрыли. В расчёте нашего плана эти данные не "
    "участвуют — только в сравнении.")
# Статусы BK распределённых заявок на момент выгрузки — словами множества.
STATUS_RU = {"Выполнена": "выполнено", "В работе": "в работе",
             "В пути": "бригада в пути", "Отправлена": "отправлено бригаде",
             "Просрочена": "просрочено"}


def build_plans(problem, time_limit_s: int = 20) -> list:
    """Оба наших плана на одной и той же задаче."""
    from .baseline import solve_baseline
    from .engine import solve
    return [solve(problem, time_limit_s=time_limit_s), solve_baseline(problem)]


def control_column(region: str) -> dict:
    """Колонка контрольного дня. Считается из контрольного файла и только
    описывает факт: в расчёте планов контрольное распределение не участвует
    (указание постановщика от 16.09).

    «Распределено» — заявки, которые ушли бригадам и не отменены клиентом.
    Не «всего минус отменённые»: иначе неотправленные заявки считались бы
    дважды — и распределёнными, и нераспределёнными (60 + 2 при 66 строках
    на Востоке).
    """
    c = load_control(region)
    distributed = c["rows"] - c["cancelled"] - c["not_sent"]
    statuses = [(st, c["statuses"][st]) for st in STATUS_RU
                if c["statuses"].get(st)]
    return dict(
        key="control", title=TITLES["control"],
        used_engineers=c["n_brigades"],
        assigned=distributed, total_jobs=c["rows"],
        unassigned=c["not_sent"], cancelled=c["cancelled"],
        total_km=None, late_jobs=c["overdue"], travel_min=None,
        breakdown=(f"{c['rows']} = {distributed} распределено по бригадам + "
                   f"{c['cancelled']} отменено клиентом + {c['not_sent']} не "
                   f"отправлено"),
        statuses=dict(statuses),
        about=CONTROL_ABOUT,
        note=f"из {c['rows']} заявок: {distributed} распределено по бригадам "
             f"({', '.join(f'{STATUS_RU[st]} {n}' for st, n in statuses)}), "
             f"{c['cancelled']} отменено клиентом, {c['not_sent']} не "
             f"отправлено; пробег невычислим — в файле нет порядка объезда",
    )


def compare(problem, plans: list) -> dict:
    """-> {region, columns: [...], deltas: {...}}. Первая колонка — опорная."""
    columns = []
    for plan in plans:
        s = summarize(plan, problem.rd)
        columns.append(dict(
            key=plan.algo, title=TITLES.get(plan.algo, plan.algo),
            used_engineers=s["used_engineers"], assigned=s["assigned"],
            total_jobs=s["total_jobs"], unassigned=s["unassigned"],
            cancelled=0,
            total_km=s["total_km"], late_jobs=s["late_jobs"],
            travel_min=s["travel_min"], note=plan.meta.get("rule", ""),
            breakdown=(f"{s['total_jobs']} = {s['assigned']} распределено + "
                       f"{s['unassigned']} не распределено"),
        ))
    columns.append(control_column(problem.rd.name))

    base = columns[0]
    deltas = {}
    for col in columns[1:]:
        d = dict(used_engineers=base["used_engineers"] - col["used_engineers"],
                 assigned=base["assigned"] - col["assigned"])
        d["total_km"] = (round(base["total_km"] - col["total_km"], 2)
                         if col["total_km"] is not None else None)
        deltas[col["key"]] = d
    return dict(region=problem.rd.name, columns=columns, deltas=deltas,
                control_about=CONTROL_ABOUT)


# --- печать ------------------------------------------------------------------

ROWS = [
    ("Задействовано исполнителей", "used_engineers", "{}"),
    ("Заявок распределено", "assigned", "{}"),
    ("Не распределено", "unassigned", "{}"),
    ("Отменено клиентом", "cancelled", "{}"),
    ("Суммарный пробег, км", "total_km", "{}"),
    ("Опозданий", "late_jobs", "{}"),
    ("Время в пути, мин", "travel_min", "{}"),
]


def format_table(cmp: dict) -> str:
    cols = cmp["columns"]
    head = ["Показатель"] + [c["title"] for c in cols]
    body = []
    for title, key, fmt in ROWS:
        row = [title]
        for c in cols:
            v = c.get(key)
            row.append(NA if v is None else fmt.format(v))
        body.append(row)

    widths = [max(len(str(head[i])), *(len(str(r[i])) for r in body))
              for i in range(len(head))]
    out = [f"\n=== {cmp['region']}: сравнение планов ===",
           "  ".join(str(head[i]).ljust(widths[i]) for i in range(len(head))),
           "  ".join("-" * w for w in widths)]
    for r in body:
        out.append("  ".join(str(r[i]).ljust(widths[i]) for i in range(len(head))))

    out.append("")
    base = cols[0]
    for c in cols[1:]:
        d = cmp["deltas"][c["key"]]
        # Знак сам по себе непонятен: «-1 исполнитель» можно прочитать и как
        # экономию, и как отставание. Поэтому печатаем оба числа и разницу.
        parts = [f"исполнителей {base['used_engineers']} против "
                 f"{c['used_engineers']} ({d['used_engineers']:+d})",
                 f"заявок {base['assigned']} против {c['assigned']} "
                 f"({d['assigned']:+d})"]
        if d["total_km"] is not None:
            parts.append(f"пробег {base['total_km']} против {c['total_km']} км "
                         f"({d['total_km']:+.2f})")
        out.append(f"{base['title']} против «{c['title']}»: " + ", ".join(parts))
    for c in cols:
        if c.get("note"):
            out.append(f"  * {c['title']}: {c['note']}")
    return "\n".join(out)
