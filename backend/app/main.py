# -*- coding: utf-8 -*-
"""Точка входа: REST и собранный интерфейс одним процессом.

    cd backend && python -m uvicorn app.main:app --port 8000

Отдельного сервера для фронтенда нет намеренно. Стенд запускается одной
командой; всё, что требует «сначала поднимите второй процесс», на демонстрации
ломается первым.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import ROOT

WEB_DIST = ROOT / "web" / "dist"

app = FastAPI(title="Улей — планирование маршрутов выездных инженеров",
              description=__doc__, version="1.0")
app.include_router(router)


@app.get("/api/health")
def health():
    """Самопроверка окружения. Любой провал — предупреждение, а не отказ
    старта: лучше работающий интерфейс с плашкой, чем пустой экран
    с честным сообщением об ошибке."""
    from .api.selfcheck import run_selfcheck
    return run_selfcheck()


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"
                                     if (WEB_DIST / "assets").is_dir()
                                     else WEB_DIST), name="assets")

    # index.html — без кэша: он ссылается на сборку с хэшем в имени, и браузер,
    # запомнивший старый index.html, после обновления стенда показывал бы
    # прежний интерфейс. Сами файлы из /assets меняют имя при каждой сборке,
    # их кэшировать можно.
    NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/")
    def index():
        return FileResponse(WEB_DIST / "index.html", headers=NO_CACHE)

    DIST = WEB_DIST.resolve()

    @app.get("/{path:path}")
    def spa(path: str):
        # Путь из адреса обязан остаться внутри сборки. Без проверки
        # «/%2e%2e/%2e%2e/…» отдал бы любой файл, доступный процессу, — вплоть
        # до ключей и паролей соседних сервисов на машине стенда.
        target = (DIST / path).resolve()
        if target.is_relative_to(DIST) and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html", headers=NO_CACHE)
else:
    @app.get("/")
    def no_frontend():
        return JSONResponse(dict(
            detail="Интерфейс не собран. Соберите: cd web && npm ci && npm run build. "
                   "API доступен на /api, описание — на /docs"))
