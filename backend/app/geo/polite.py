# -*- coding: utf-8 -*-
"""Пауза между запросами к публичным сервисам.

Nominatim разрешает не больше одного запроса в секунду и за нарушение
блокирует IP — а на машине стенда могут жить и другие сервисы с тем же IP.
Обычная новая заявка решатель не запускает и проходит мгновенно, поэтому
предел живых расчётов частоту запросов не держит. Держит эта пауза: общая
на процесс, и следующий запрос ждёт, пока с предыдущего не пройдёт интервал.
"""
from __future__ import annotations
import threading
import time


class Throttle:
    def __init__(self, interval_s: float):
        self.interval = interval_s
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        # Ждём под замком: так запросы выстраиваются по одному, а не
        # просыпаются разом после общей паузы.
        with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next = time.monotonic() + self.interval
