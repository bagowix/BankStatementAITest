"""Точка входа для uvicorn: `uvicorn bankstatement.web.app:app`.

Здесь намеренно ничего нет, кроме вызова фабрики. Всё, что можно настроить,
настраивается в ней и приходит из окружения. Логи включаются до сборки: uvicorn
настраивает только свои логгеры, и без этого строка о выбранном режиме — очередь
или один процесс — не попадала бы в вывод, а именно она первой объясняет, почему
воркер молчит.
"""

from bankstatement.logs import configure_logging
from bankstatement.web.factory import create_app

configure_logging()

app = create_app()
