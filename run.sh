#!/usr/bin/env bash
# Запуск «Улья» одной командой. Вся логика — в run.py: он же запускается на
# Windows (py run.py). Этот файл оставлен для привычного ./run.sh.
#
#   ./run.sh                — порт 8000
#   ./run.sh 8010           — другой порт
#   NO_BROWSER=1 ./run.sh   — не открывать браузер
cd "$(dirname "$0")" || exit 1
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "!! не найден python3. Нужен Python 3.10 или новее." >&2; exit 1; }
exec "$PY" run.py "$@"
