"""Настройка логов там, где процесс запускает не наш код.

Воркер стартует через `taskiq worker`, стенд — через `uvicorn`; оба настраивают
только собственные логгеры, и строки вроде «разбор начат» или «стенд запущен:
режим очередь» без этого никуда не попадают. Настраивается ровно свой логгер и
только если приложение не настроило логирование само: библиотека не вправе
перебивать конфигурацию хоста.
"""

import logging


def configure_logging() -> None:
    """Включить логи пакета под чужим запускающим процессом."""
    logger = logging.getLogger('bankstatement')

    if logger.handlers or logging.getLogger().handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
