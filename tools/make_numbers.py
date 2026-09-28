# -*- coding: utf-8 -*-
"""Единственный источник чисел для документов и слайдов.

Зачем. Числа расходятся быстрее, чем кажется: README, сводки, таблица
чувствительности и дампы в runs/ легко начинают показывать разное.
Расхождение на слайде — единственный вид ошибки, который на защите невозможно
объяснить.

Поэтому числа в документы не пишутся руками. Этот скрипт берёт artifacts/ и
исходные файлы data/raw/, собирает блоки и подставляет их между маркерами:

    <!-- ЧИСЛА:<имя> -->
    ... содержимое генерируется ...
    <!-- /ЧИСЛА:<имя> -->

Запуск:
    python tools/make_numbers.py            # подставить
    python tools/make_numbers.py --check    # только проверить: числа свежие и
                                            # в документах не осталось маркеров

Вторая проверка — напоминание перед сдачей. Всё, что может вписать только
команда (ссылки на стенд и видео, состав команды), помечено в документах словом
ЗАПОЛНИТЬ. Пока хоть одно такое место есть, --check падает и печатает, где оно.
Упоминание слова в «ёлочках» или в `коде` маркером не считается: так документы
рассказывают о самой проверке. Блок-цитаты реестра требований тоже не
проверяются: цитаты там дословные, править их нельзя.
"""
import argparse
import csv
import difflib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
RAW = ROOT / "data" / "raw"

TARGETS = [ROOT / "README.md"]


def load(name):
    p = ART / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# --- исходные файлы выгрузки --------------------------------------------------
# Читаются здесь же, без backend: блоки документов не должны зависеть от кода,
# который меняется быстрее них. Правила — те же, что у загрузчика.

def read_raw(path: Path) -> list:
    """CSV выгрузки: cp1251, «;», под таблицей служебная строка «Адрес Офиса».
    Заявка — строка с числовым номером, всё остальное пропускается."""
    rows = list(csv.reader(io.StringIO(path.read_bytes().decode("cp1251")), delimiter=";"))
    if not rows:
        return []
    header = [c.strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        rec = dict(zip(header, (c.strip() for c in r)))
        if rec.get("Заявка", "").isdigit():
            out.append(rec)
    return out


def control_day() -> dict:
    """Контрольный день: как участки отработали на самом деле. В расчёте планов
    не участвует — только в сравнении. -> {регион: {brigades, overdue, ...}}"""
    out = {}
    for path in sorted(RAW.glob("* Контрольное распределение*.csv")):
        region = path.name.split(" Контрольное")[0]
        recs = read_raw(path)
        status = Counter(r.get("Статус BK", "") for r in recs)
        out[region] = dict(
            rows=len(recs),
            brigades=len({r["Бригада"] for r in recs if r.get("Бригада")}),
            overdue=status["Просрочена"], cancelled=status["Отменена"],
            not_sent=status["Не отправлена"])
    return out


# --- блоки ----------------------------------------------------------------------

def block_results(summary) -> str:
    if not summary:
        return ("_Числа появятся после `python tools/build_reference.py`._\n"
                "Пока эталонные планы не посчитаны, таблицы результатов в документах нет "
                "намеренно: числа, написанные руками, расходятся с кодом.")
    b = summary.get("seconds_per_region")
    out = [f"Посчитано `tools/build_reference.py`, бюджет решателя {b} с на регион, "
           f"машина: {summary.get('machine', '—')}.", "",
           "| Регион | Наш план | Базовый вариант ТЗ 2.3 |",
           "|---|---|---|"]
    te, tk, ta, tj, tl = 0, 0.0, 0, 0, 0
    be, bk, ba = 0, 0.0, 0
    for r in summary["regions"]:
        p, bs = r["plan"], r["baseline"]
        out.append(f"| {r['region']} | {p['engineers']} бригад, {p['km']:.1f} км, "
                   f"{p['assigned']}/{r['jobs']} заявок | {bs['engineers']} бригад, "
                   f"{bs['km']:.1f} км, {bs['assigned']}/{r['jobs']} |")
        te += p["engineers"]; tk += p["km"]; ta += p["assigned"]; tj += r["jobs"]
        tl += p.get("late", 0)
        be += bs["engineers"]; bk += bs["km"]; ba += bs["assigned"]
    out.append(f"| **Всего** | **{te} бригад, {tk:.1f} км, {ta}/{tj}** | "
               f"{be} бригад, {bk:.1f} км, {ba}/{tj} |")
    if te and be:
        out += ["", f"Против базового варианта на том же парке: людей меньше на "
                    f"{100 * (be - te) / be:.0f}%, пробег меньше на "
                    f"{100 * (bk - tk) / bk:.0f}%, и базовый не закрывает "
                    f"{tj - ba} заявок из {tj}."]
    ctrl = control_day()
    regions = [r["region"] for r in summary["regions"]]
    if regions and all(x in ctrl for x in regions):
        ce = sum(ctrl[x]["brigades"] for x in regions)
        cl = sum(ctrl[x]["overdue"] for x in regions)
        ours = " / ".join(str(r["plan"]["engineers"]) for r in summary["regions"])
        theirs = " / ".join(str(ctrl[x]["brigades"]) for x in regions)
        out += ["", f"Против контрольного дня: бригад {te} против {ce} (по участкам "
                    f"{ours} против {theirs}); в нашем плане опозданий {tl}, в контрольном "
                    f"дне просрочено {cl} заявок."]
    return "\n".join(out)


def block_curve(summary) -> str:
    if not summary or not summary.get("curves"):
        return "_Кривая появится после `python tools/build_reference.py --curve`._"
    if summary.get("curve_built_at"):
        # Кривая от основного плана (fleet_for_curve): штат урезается с тех,
        # кто без заявок, потом с наименее загруженных.
        out = ["Все заявки закрывает ровно столько бригад, сколько работает в "
               "основном плане: на таком штате он проходит аудит. Бригадой меньше — "
               "заявки остаются без исполнителя. Штат урезали так: первыми уходят "
               "бригады без заявок, потом наименее загруженные; зона не остаётся "
               f"без бригады. Меньшие штаты считались по {summary.get('curve_seconds')} с.",
               "", "| Регион | Штат | Работают | Закрыто заявок | Без бригады | Пробег |",
               "|---|---|---|---|---|---|"]
        for region, pts in summary["curves"].items():
            for p in pts:
                note = " (основной план)" if "основной" in str(p.get("source", "")) else ""
                out.append(f"| {region} | {p['fleet']} | {p['used']} | {p['assigned']} | "
                           f"{p['unassigned']} | {p['km']:.1f} км{note} |")
        return "\n".join(out)
    out = ["Сколько бригад действительно нужно — посчитано, а не заявлено.", "",
           "| Регион | Парк | Задействовано | Закрыто заявок | Пробег |", "|---|---|---|---|---|"]
    for region, pts in summary["curves"].items():
        for p in pts:
            note = " (основной прогон)" if "основной" in str(p.get("source", "")) else ""
            out.append(f"| {region} | {p['fleet']} | {p['used']} | {p['assigned']} | "
                       f"{p['km']:.1f} км{note} |")
    return "\n".join(out)


# Сколько ждёт интерфейс — как в tools/convergence.py (MARKS). Подписи берутся
# отсюда, а не из artifacts/convergence.json: так их можно поправить без
# пересчёта кривой.
TIME_MARKS = {10: "событие дня", 20: "кнопка «Пересчитать»", 600: "план, посчитанный заранее"}


def block_time(conv) -> str:
    """Кривая «время счёта -> результат» (tools/convergence.py). Пока её нет,
    блок держит маркер: make_numbers.py --check напомнит перед сдачей."""
    if not conv or not conv.get("table"):
        return ("ЗАПОЛНИТЬ: `python tools/convergence.py` на той же машине, что "
                "эталонные планы, затем `python tools/make_numbers.py`.")
    summary = load("summary.json") or {}
    regions = summary.get("regions") or []
    jobs = sum(r["jobs"] for r in regions) or None
    of = f" из {jobs}" if jobs else ""
    secs = conv["seconds_per_region"]
    together = conv.get("processes", 1) >= len(conv.get("regions") or {})
    out = [f"Решатель ищет, пока не кончится время, и сообщает о каждом найденном "
           f"плане. Поэтому хватает одного прогона на участок: {secs} с, участки "
           f"{'параллельно' if together else 'по очереди'}. "
           f"Машина: {conv['machine']}. В строке — лучший план, "
           f"найденный к этому моменту, сумма по трём участкам.", "",
           "| Время счёта | Бригад | Пробег | Закрыто заявок | Где в интерфейсе |",
           "|---|---|---|---|---|"]
    for row in conv["table"]:
        t = row.get("total")
        if not t:
            continue
        out.append(f"| {row['seconds']} с | {t['engineers']} | {t['km']:.1f} км | "
                   f"{t['assigned']}{of} | {TIME_MARKS.get(row['seconds'], '')} |")
    if regions:
        be = sum(r["baseline"]["engineers"] for r in regions)
        bk = sum(r["baseline"]["km"] for r in regions)
        ba = sum(r["baseline"]["assigned"] for r in regions)
        out.append(f"| базовый вариант ТЗ 2.3 | {be} | {bk:.1f} км | {ba}{of} | |")
    out += ["", "Диспетчер десять минут не ждёт: план дня считается заранее, "
                "перестройка после события — 10 с, «Пересчитать» — 20 с."]
    return "\n".join(out)


# Сценарии двух родов: одни СНИМАЮТ ограничение, другие МЕНЯЮТ допущение.
# Подавать их одинаково нельзя — читатель перепутает цену ограничения с ценой выбора.
RELAXATIONS = {"Без квалификаций", "Без требований к транспорту",
               "Без лимита оборудования", "Без зон и без местных баз",
               "Без запрета зон, базы на месте", "Опоздание до 30 минут"}

# Сценарий «Без зон» в artifacts/sensitivity.json показывается под точным
# именем: он переносит ВСЕ бригады в московский офис, то есть меряет отсутствие
# местных баз, а не запрет зоны, и короткая подпись читалась бы как цена
# запрета зон.
LEGACY_NAMES = {"Без зон": "Без зон и без местных баз"}

# Строки, смысл которых из названия не читается.
SCENARIO_NOTES = {
    "Без зон и без местных баз": "все бригады выезжают из московского офиса, "
                                 "зоны не действуют",
    "Без запрета зон, базы на месте": "бригада может взять заявку в чужой зоне, "
                                      "но выезжает со своей базы",
}


def block_sensitivity(sens) -> str:
    if not sens:
        return "_Таблица появится после `python tools/sensitivity.py`._"
    summary = load("summary.json")
    secs = sens["seconds"]
    if sens.get("from_main_plan"):
        out = [f"Первая строка — основной план. Остальные — тот же день с одним "
               f"изменённым правилом, расчёт по {secs} с; всё, кроме названного, "
               "неизменно. Если правило только ослаблено, основной план остаётся "
               "допустимым, и в строке — лучшее из двух: нового расчёта и основного плана."]
    else:
        out = [f"Каждая строка — отдельный прогон решателя, бюджет {secs} с. "
               "Всё, кроме названного, остаётся неизменным."]

    # Строка «Как есть» — не основной результат, а ещё один прогон той же задачи.
    # Первый вопрос эксперта «какое число правда?» получает ответ здесь же, рядом
    # с числами, а не в соседнем абзаце, который разойдётся с ними после пересчёта.
    main = {r["region"]: r["plan"] for r in (summary or {}).get("regions", [])}
    diffs = []
    for region, rows in sens["regions"].items():
        base, p = rows[0], main.get(region)
        if p and (base["engineers"] != p["engineers"] or abs(base["km"] - p["km"]) >= 0.05):
            diffs.append(f"{region} — в результате выше {p['engineers']} бригад и "
                         f"{p['km']:.1f} км, в строке «Как есть» {base['engineers']} бригад "
                         f"и {base['km']:.1f} км")
    if diffs:
        why = [f"> **Почему «Как есть» не совпадает с результатом выше.** Это другой прогон "
               f"той же задачи с тем же бюджетом {secs} с. Решатель ищет, пока не кончится "
               "время, и останавливается на лучшем найденном плане, а не на доказанном "
               "оптимуме, поэтому два прогона могут прийти к разным планам: "
               + "; ".join(diffs) + "."]
        if sens.get("jobs") and summary:
            why[0] += (f" Вдобавок таблица считалась {sens['jobs']} процессами одновременно, "
                       f"эталон — {len(summary['regions'])}, и каждому прогону таблицы "
                       "досталось меньше процессора.")
        why[0] += (" Строки таблицы посчитаны в одинаковых условиях и сравнимы только между "
                   "собой. Основной результат — таблица «Результаты».")
        out += [""] + why
    elif main:
        out += ["", "Строка «Как есть» совпадает с основным результатом во всех регионах."]

    out += ["", "Читается так: если при снятом ограничении бригад становится **меньше** — "
                "значит это ограничение стоит нам разницы в людях. Если больше "
                "километров — ограничение экономит пробег."]
    shown = set()
    for region, rows in sens["regions"].items():
        base = rows[0]
        out += ["", f"**{region}**", "",
                "| Сценарий | Бригад | Пробег | Заявок |", "|---|---|---|---|",
                f"| _Как есть_ | **{base['engineers']}** | **{base['km']:.1f} км** | "
                f"{base['assigned']}/{base['total']} |"]
        for group, title in ((True, "снимаем ограничение"), (False, "меняем допущение")):
            sel = [r for r in rows[1:]
                   if (LEGACY_NAMES.get(r["scenario"], r["scenario"]) in RELAXATIONS) == group]
            if not sel:
                continue
            out.append(f"| _{title}_ | | | |")
            for r in sel:
                name = LEGACY_NAMES.get(r["scenario"], r["scenario"])
                shown.add(name)
                if not r.get("audit_ok", True):
                    out.append(f"| {name} | _аудит не пройден_ | — | — |")
                    continue
                de, dk = r["d_eng"], r["d_km"]
                e = f"{r['engineers']}" + (f" ({de:+d})" if de else " (=)")
                k = f"{r['km']:.1f} км" + (f" ({dk:+.0f})" if abs(dk) >= 0.5 else " (=)")
                soft = " *" if r.get("soft_windows") else ""
                lost = "" if r["assigned"] == base["assigned"] else " ⚠"
                out.append(f"| {name}{soft} | {e} | {k} | "
                           f"{r['assigned']}/{r['total']}{lost} |")
    out += [""]
    out += [f"_«{n}» — {SCENARIO_NOTES[n]}._" for n in SCENARIO_NOTES if n in shown]
    out += [r"_\* Мягкие окна меняют постановку задачи, а не только ограничение: "
            r"километры этой строки с остальными несопоставимы._",
            "_⚠ В этом сценарии закрыты не все заявки._"]
    return "\n".join(out)


# Ориентир экспертов для синтетических данных (разъяснение 19.09, п. 11).
WORK_KINDS = (("Локальные", 40), ("Подключения", 40), ("Аварии", 10), ("Дозаказы", 10))


def work_kind(bk: str, hd: str) -> str:
    """Тип работ по полю BK. Авария — по полю HD, как в модели
    (backend/app/io/normatives.py): «Глобальная проблема» бывает и
    «Информацией», и тогда это обследование сети, то есть локальные работы."""
    b, h = bk.lower(), hd.lower()
    if "авари" in h:
        return "Аварии"
    if "подключение" in b:
        return "Подключения"
    if "дозаказ" in b:
        return "Дозаказы"
    return "Локальные"


def block_work_kinds() -> str:
    files = sorted(RAW.glob("* Синтетические данные.csv"))
    if not files:
        return "_Нет исходных файлов `data/raw/* Синтетические данные.csv`._"
    per, inspections = {}, 0
    for path in files:
        c = Counter()
        for rec in read_raw(path):
            kind = work_kind(rec.get("Тип заявки BK", ""), rec.get("Тип заявки HD", ""))
            c[kind] += 1
            if kind == "Локальные" and "глобальн" in rec.get("Тип заявки BK", "").lower():
                inspections += 1
        per[path.name.split(" Синтетические")[0]] = c
    total = sum(per.values(), Counter())

    def cell(n: int, of: int) -> str:
        return f"{n} ({100 * n / of:.0f}%)" if of else "—"

    out = ["Посчитано `tools/make_numbers.py` по файлам `data/raw/* Синтетические данные.csv`.",
           "",
           "| Тип работ | " + " | ".join(per) + " | Всего | Ориентир экспертов |",
           "|---" * (len(per) + 3) + "|"]
    for kind, guide in WORK_KINDS:
        out.append(f"| {kind} | "
                   + " | ".join(cell(c[kind], sum(c.values())) for c in per.values())
                   + f" | {cell(total[kind], sum(total.values()))} | {guide}% |")
    out.append("| Заявок | " + " | ".join(str(sum(c.values())) for c in per.values())
               + f" | {sum(total.values())} | |")
    out += ["", f"_Тип — по полю «Тип заявки BK». «Глобальная проблема» считается аварией, "
                f"только если в поле HD стоит «Авария», — так же решает модель; остальные "
                f"{inspections} — обследование сети, то есть локальные работы._"]
    return "\n".join(out)


def _totals(summary) -> dict:
    regs = summary["regions"]
    return dict(
        te=sum(r["plan"]["engineers"] for r in regs), tk=sum(r["plan"]["km"] for r in regs),
        ta=sum(r["plan"]["assigned"] for r in regs), tj=sum(r["jobs"] for r in regs),
        be=sum(r["baseline"]["engineers"] for r in regs),
        bk=sum(r["baseline"]["km"] for r in regs),
        ba=sum(r["baseline"]["assigned"] for r in regs))


def block_demo_totals(summary) -> str:
    """Реплика шага «Сравнение» в сценарии защиты."""
    if not summary:
        return "_Числа появятся после `python tools/build_reference.py`._"
    t = _totals(summary)
    ctrl = control_day()
    regions = [r["region"] for r in summary["regions"]]
    live = (f" и {sum(ctrl[x]['brigades'] for x in regions)} в контрольном дне"
            if regions and all(x in ctrl for x in regions) else "")
    return (f"> «По трём участкам: {t['te']} бригад против {t['be']} у базового "
            f"варианта{live}; {t['tk']:.1f} км против {t['bk']:.1f} км; закрыто "
            f"{t['ta']} заявок из {t['tj']} против {t['ba']} у базового.»")


DEMO_REGION = "Юго-восток"


def block_demo_region(summary) -> str:
    """Реплика про регион защиты: сколько людей против контрольного дня и почему.
    Сказать это нужно самим и первыми, пока эксперты не спросили."""
    if not summary:
        return "_Числа появятся после `python tools/build_reference.py`._"
    ctrl = control_day()
    plans = {r["region"]: r["plan"] for r in summary["regions"]}
    if DEMO_REGION not in plans or DEMO_REGION not in ctrl:
        return f"_Нет данных по региону {DEMO_REGION}._"
    ours, theirs = plans[DEMO_REGION]["engineers"], ctrl[DEMO_REGION]["brigades"]
    late, overdue = plans[DEMO_REGION].get("late", 0), ctrl[DEMO_REGION]["overdue"]
    others = [f"{x} {plans[x]['engineers']} против {ctrl[x]['brigades']}"
              for x in plans if x != DEMO_REGION and x in ctrl
              and plans[x]["engineers"] < ctrl[x]["brigades"]]
    if ours == theirs:
        head = (f"На {DEMO_REGION}е людей у нас столько же, сколько в контрольном дне, "
                f"— {ours}. Это самый тяжёлый участок: три "
                "зоны обслуживания, больше трети заявок в Подмосковье, и каждой зоне нужны свои "
                "бригады. Выигрыш здесь не в людях, а в опозданиях")
    elif ours < theirs:
        head = (f"На {DEMO_REGION}е людей у нас меньше, чем в контрольном дне: "
                f"{ours} против {theirs}. И это самый тяжёлый участок: три зоны "
                "обслуживания и больше трети заявок в Подмосковье. Второй выигрыш — в опозданиях")
    else:
        head = (f"На {DEMO_REGION}е людей у нас больше, чем в контрольном дне: "
                f"{ours} против {theirs}. Это самый тяжёлый участок: три зоны "
                "обслуживания, больше трети заявок в Подмосковье, и каждой зоне нужны свои "
                "бригады. Выигрыш здесь не в людях, а в опозданиях")
    tail = f": в плане их {late}, в контрольном дне просрочено {overdue}."
    if others:
        tail += " Людей экономим на двух других участках: " + ", ".join(others) + "."
    return f"> «{head}{tail}»"


def block_demo_effect(summary) -> str:
    """Реплика «эффект»: часы в дороге. Это факт из результатов расчёта, а не
    допущение, поэтому его называют первым, а рубли — только чужой ставкой."""
    if not summary:
        return "_Числа появятся после `python tools/build_reference.py`._"
    regions = [r["region"] for r in summary["regions"]]
    plans = [load(f"plan_{x}.json") for x in regions]
    bases = [load(f"baseline_{x}.json") for x in regions]
    if not (plans and all(plans) and all(bases)):
        return "_Нет artifacts/plan_*.json или artifacts/baseline_*.json._"
    pm, bm = sum(p["travel_min"] for p in plans), sum(b["travel_min"] for b in bases)
    pa, ba = sum(p["assigned"] for p in plans), sum(b["assigned"] for b in bases)
    return (f"> «За день по трём участкам наши бригады проводят в дороге {pm / 60:.1f} часа, "
            f"в базовом варианте — {bm / 60:.1f}. Это {pm / pa:.1f} минуты дороги на одну "
            f"закрытую заявку против {bm / ba:.1f}.»")


BLOCKS = {
    "результаты": lambda: block_results(load("summary.json")),
    "кривая": lambda: block_curve(load("summary.json")),
    "чувствительность": lambda: block_sensitivity(load("sensitivity.json")),
    "время": lambda: block_time(load("convergence.json")),
    "типы-работ": block_work_kinds,
    "демо-итог": lambda: block_demo_totals(load("summary.json")),
    "демо-регион": lambda: block_demo_region(load("summary.json")),
    "демо-эффект": lambda: block_demo_effect(load("summary.json")),
}


# --- маркеры «заполнить перед сдачей» ----------------------------------------------

MARKER = re.compile(r"(?<![«`A-Za-zА-Яа-яЁё0-9])(TODO|ЗАПОЛНИТЬ)(?![»`A-Za-zА-Яа-яЁё0-9])")
VERBATIM = {ROOT / "docs" / "requirements.md"}   # блок-цитаты там дословные


def find_markers() -> list:
    found = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if path in VERBATIM and line.startswith(">"):
                continue
            if MARKER.search(line):
                found.append((path.relative_to(ROOT), n, line.strip()))
    return found


def apply(check: bool) -> int:
    stale = 0
    for path in TARGETS:
        if not path.exists():
            continue
        text = original = path.read_text(encoding="utf-8")
        for name, build in BLOCKS.items():
            pat = re.compile(rf"(<!-- ЧИСЛА:{name} -->\n).*?(\n<!-- /ЧИСЛА:{name} -->)",
                             re.S)
            if not pat.search(text):
                continue
            text = pat.sub(lambda m: m.group(1) + build() + m.group(2), text)
        if text != original:
            stale += 1
            if check:
                print(f"  устарело: {path.relative_to(ROOT)}")
                diff = list(difflib.unified_diff(original.splitlines(), text.splitlines(),
                                                 "в файле", "из артефактов", n=0, lineterm=""))
                for line in diff[:40]:
                    print(f"    {line}")
                if len(diff) > 40:
                    print(f"    … ещё {len(diff) - 40} строк")
            else:
                path.write_text(text, encoding="utf-8")
                print(f"  обновлено: {path.relative_to(ROOT)}")
    code = 0
    if check and stale:
        print("\nЧисла в документах разошлись с артефактами. "
              "Запустите python tools/make_numbers.py")
        code = 1
    if not stale:
        print("  числа в документах совпадают с артефактами")

    markers = find_markers()
    if markers:
        print(f"\n  Перед сдачей заполнить ({len(markers)} строк):")
        for path, n, line in markers:
            print(f"    {path}:{n}: {line[:110]}")
        if check:
            code = 1
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="только проверить, ничего не менять (код возврата 1, если числа "
                         "разошлись с артефактами или в документах остались маркеры "
                         "TODO и ЗАПОЛНИТЬ)")
    a = ap.parse_args()
    if not ART.exists():
        print(f"Нет каталога {ART}. Сначала tools/build_reference.py и tools/sensitivity.py.")
    sys.exit(apply(a.check))
