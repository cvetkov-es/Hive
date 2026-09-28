# -*- coding: utf-8 -*-
"""КТ-1: проверка целостности исходных данных. Запускается до всего остального."""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import REGIONS, SKILL_RU, PRIO_RU            # noqa: E402
from app.models import hhmm                                   # noqa: E402
from app.io.csv_loader import load_region, load_control       # noqa: E402
from app.geo.travel import TravelModel, haversine_km          # noqa: E402

OK, BAD = "  ok ", "  !! "
problems = []


def check(cond, msg):
    print((OK if cond else BAD) + msg)
    if not cond:
        problems.append(msg)


def main():
    for region in REGIONS:
        rd = load_region(region)
        tm = TravelModel.load(region)
        ctl = load_control(region)
        print("=" * 78)
        print(f"{region}  —  {len(rd.jobs)} заявок, офис: {rd.office_address}")
        print("-" * 78)

        check(not rd.problems, f"все строки разобраны (проблемных: {len(rd.problems)})")
        check(len(tm.points) == len(rd.jobs) + len(rd.depots),
              f"матрица {len(tm.points)}x{len(tm.points)} соответствует "
              f"{len(rd.depots)} депо + {len(rd.jobs)} заявкам")
        check(all(f"job:{j.id}" in tm.index for j in rd.jobs),
              "каждая заявка присутствует в матрице")

        lvl = Counter(j.geo_level for j in rd.jobs)
        exact = lvl.get(0, 0)
        check(exact / len(rd.jobs) >= 0.85,
              f"точность геопривязки: до дома {exact}/{len(rd.jobs)}"
              f" ({100 * exact / len(rd.jobs):.0f}%), грубее — {len(rd.jobs) - exact}")

        sk = Counter(j.skill for j in rd.jobs)
        print("       навыки: " + ", ".join(
            f"{SKILL_RU[k]} {v}" for k, v in sorted(sk.items())))
        pr = Counter(j.priority for j in rd.jobs)
        print("    приоритет: " + ", ".join(
            f"{PRIO_RU[k]} {v}" for k, v in sorted(pr.items())))
        check(set(sk) <= {"local", "connect", "emergency"}, "навыки только из справочника ТЗ")

        work = sum(j.service_min for j in rd.jobs)
        norm = sum(j.norm_min for j in rd.jobs)
        print(f"    нагрузка: чистая работа {work} мин ({work / 60:.1f} ч); "
              f"по нормативу с дорогой было бы {norm} мин — разница {norm - work} мин "
              f"(это и есть двойной учёт, который мы сняли)")

        cl = Counter(j.cluster for j in rd.jobs)
        print("    кластеры: " + ", ".join(f"{k} {v}" for k, v in cl.most_common()))
        for key, dep in rd.depots.items():
            n = sum(1 for j in rd.jobs if j.cluster == dep.cluster)
            print(f"              депо «{dep.name}» ({dep.lat:.4f}, {dep.lon:.4f}) -> {n} заявок")
        check(set(cl) <= {d.cluster for d in rd.depots.values()},
              "у каждого кластера заявок есть своё депо")

        far = []
        for j in rd.jobs:
            d = rd.depots.get(j.cluster) or rd.depots["office"]
            far.append(haversine_km(d.lat, d.lon, j.lat, j.lon))
        far.sort()
        p90 = far[int(0.9 * len(far))]
        check(p90 < 25, f"удалённость от своего депо: медиана {far[len(far) // 2]:.1f} км, "
                        f"p90 {p90:.1f} км, max {far[-1]:.1f} км")

        win = defaultdict(lambda: [0, 0])
        for j in rd.jobs:
            k = "сутки" if j.floating_window else f"{hhmm(j.win_start)}-{hhmm(j.win_end)}"
            win[k][0] += 1
            win[k][1] += j.service_min
        print("        окна: " + " | ".join(
            f"{k} {v[0]}шт/{v[1]}мин" for k, v in sorted(win.items())))
        morning = sum(v[0] for k, v in win.items() if k[:2] in ("10", "12"))
        print(f"              утренний пик 10:00-14:00: {morning} из {len(rd.jobs)} "
              f"({100 * morning / len(rd.jobs):.0f}%) — узкое место плана")

        bad_win = [j.id for j in rd.jobs if j.win_end <= j.win_start]
        check(not bad_win, f"все окна корректны (перевёрнутых: {len(bad_win)})")
        fl = [j for j in rd.jobs if j.floating_window]
        check(all(j.skill == "emergency" for j in fl) or not fl,
              f"плавающее окно только у аварий ({len(fl)} шт)")

        dup = [k for k, v in Counter(j.address for j in rd.jobs).items() if v > 1]
        print(f"    дубли адресов: {len(dup)}"
              + (f" (напр. {dup[0][:55]})" if dup else ""))
        ids = Counter(j.id for j in rd.jobs)
        check(all(v == 1 for v in ids.values()), "номера заявок уникальны")

        print(f"  контрольный день: {ctl['n_brigades']} бригад, {ctl['rows']} строк; "
              f"отменено {ctl['cancelled']}, просрочено {ctl['overdue']}, "
              f"не отправлено {ctl['not_sent']} -> живых заявок {ctl['live_jobs']}")

    warn = check_no_warnings()
    print("=" * 78)
    check(not warn, f"модули компилируются без предупреждений (найдено: {len(warn)})")
    for w in warn[:5]:
        print("      " + w)

    if problems:
        print(f"ПРОВАЛЕНО проверок: {len(problems)}")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("КТ-1 пройдена: данные целостны, матрицы согласованы, кластеры покрыты депо.")


def check_no_warnings():
    """Компиляция всех модулей проекта без предупреждений.

    Предупреждение вида «invalid escape sequence» сегодня косметика, а в
    следующих версиях Python — ошибка. В сдаваемом проекте вывод должен быть
    чистым: эксперт запускает по README и видит ровно то, что мы обещали.
    """
    import py_compile
    import warnings
    root = Path(__file__).resolve().parents[1]
    problems = []
    for f in sorted(list((root / "tools").glob("*.py"))
                    + list((root / "backend").rglob("*.py"))):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                py_compile.compile(str(f), doraise=True)
            except Exception as exc:                        # noqa: BLE001
                problems.append(f"{f.name}: {exc}")
                continue
            problems += [f"{f.name}: {c.message}" for c in caught]
    return problems


if __name__ == "__main__":
    main()
