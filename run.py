#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск «Улья» одной командой — на Windows, macOS и Linux одинаково.

    python run.py            — порт 8000 (на Windows: py run.py)
    python run.py 8010       — другой порт
    NO_BROWSER=1 python run.py   — не открывать браузер

Только стандартная библиотека: скрипт запускается раньше, чем появились
зависимости. Три правила — запуск с чужой машины, и второй попытки нет:

  1. Ничего не делать молча. Каждый шаг называет себя и свой итог.
  2. Не падать там, где можно предупредить: нет Node — интерфейс берётся из
     закоммиченной сборки; нет сборки — поднимается API, и об этом сказано
     прямо. Отказ старта оправдан, только когда поднимать нечего.
  3. Не чинить окружение молча. Провал самопроверки печатается строкой с
     причиной — и сервис всё равно работает.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WINDOWS = os.name == "nt"


def say(msg: str) -> None:
    print(f"== {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"!! {msg}", file=sys.stderr, flush=True)


def die(msg: str) -> None:
    warn(msg)
    raise SystemExit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "8000"
    if not port.isdigit():
        die(f"порт — число, а не «{port}»")

    # --- 1. Python ------------------------------------------------------------
    if sys.version_info < (3, 10):
        die(f"нужен Python 3.10 или новее, а это {sys.version.split()[0]}")
    say(f"Python {sys.version.split()[0]}")

    # --- 2. Окружение и зависимости ------------------------------------------
    # Окружение — здесь же, чтобы не трогать системный Python чужой машины.
    py = venv_python()
    if not py.exists():
        say(f"Создаём окружение {VENV.name}")
        if subprocess.run([sys.executable, "-m", "venv", str(VENV)]).returncode:
            die("не удалось создать окружение (на Linux нужен пакет python3-venv)")
    have = subprocess.run([str(py), "-c", "import fastapi, uvicorn, ortools"],
                          capture_output=True).returncode == 0
    if have:
        say("Зависимости на месте")
    else:
        say("Ставим зависимости (requirements.txt) — это разово, нужна сеть")
        subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        if subprocess.run([str(py), "-m", "pip", "install", "--quiet", "-r",
                           str(ROOT / "requirements.txt")]).returncode:
            die("зависимости не установились. Проверьте сеть и повторите запуск")

    # --- 3. Интерфейс -----------------------------------------------------------
    # Сборка закоммичена намеренно: на машине без Node интерфейс всё равно есть.
    if (ROOT / "web" / "dist" / "index.html").is_file():
        say("Интерфейс: готовая сборка web/dist")
    elif shutil.which("npm"):
        say("Сборки нет — собираем интерфейс (npm ci && npm run build)")
        npm = shutil.which("npm")
        ok = (subprocess.run([npm, "ci", "--silent"], cwd=ROOT / "web").returncode == 0
              and subprocess.run([npm, "run", "build"], cwd=ROOT / "web").returncode == 0)
        if not ok:
            warn("интерфейс не собрался: поднимаем API, экран будет недоступен")
    else:
        warn("нет ни сборки web/dist, ни Node — поднимаем только API (/api, /docs)")

    # --- 4. Запуск ---------------------------------------------------------------
    say(f"Запускаем сервис на порту {port}")
    server = subprocess.Popen([str(py), "-m", "uvicorn", "app.main:app", "--port", port],
                              cwd=ROOT / "backend")
    url = f"http://127.0.0.1:{port}"
    try:
        health = None
        for _ in range(60):
            if server.poll() is not None:
                die("сервис не поднялся — смотрите сообщения выше")
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=30) as r:
                    health = json.loads(r.read().decode("utf-8"))
                break
            except OSError:
                time.sleep(1)

        # --- 5. Самопроверка -----------------------------------------------------
        # Тот же отчёт, что интерфейс показывает плашкой. В консоли он нужен,
        # когда экран ещё не открыли, а знать о провале надо уже сейчас.
        if health is None:
            warn("самопроверка не ответила за минуту")
        else:
            say(f"Самопроверка: {health['headline']} ({health['passed']} из {health['total']})")
            for c in health["checks"]:
                if not c["ok"]:
                    warn(f"{c['name']}: {c['detail']}")

        # --- 6. Браузер ----------------------------------------------------------
        if os.environ.get("NO_BROWSER"):
            say(f"Откройте в браузере: {url}")
        elif not webbrowser.open(url):
            say(f"Откройте в браузере: {url}")

        say(f"Готово: {url}  (остановить — Ctrl+C)")
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
