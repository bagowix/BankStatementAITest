"""Где лежат файлы офиса: относительно корня репозитория, а не текущего каталога.

Реестр счетов и книга поступлений — одна на оператора. Если путь по умолчанию считать
от текущего каталога, команда из другой папки молча создаст пустой реестр и выдаст
номер `AS-2026-001` второй раз. Поэтому умолчания считаются от корня репозитория:
каталога, где рядом лежат `pyproject.toml` и `operator.example.toml`.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Final

from autostudio.errors import ConfigError

ROOT_MARKERS: Final = ('pyproject.toml', 'operator.example.toml')
OFFICE_SUBDIR: Final = Path('var/office')
OPERATOR_FILE: Final = 'operator.toml'
TEMPLATES_SUBDIR: Final = Path('docs/legal')


def find_project_root(starts: Iterable[Path]) -> Path | None:
    """Первый каталог вверх от каждой стартовой точки, где есть все `ROOT_MARKERS`.

    Args:
        starts: Откуда искать, по порядку.

    Returns:
        Корень репозитория или None.
    """
    for start in starts:
        for candidate in (start, *start.parents):
            if all((candidate / marker).is_file() for marker in ROOT_MARKERS):
                return candidate
    return None


def project_root() -> Path:
    """Корень репозитория: сначала от исходников пакета (uv ставит проект editable), затем от текущего каталога.

    Raises:
        ConfigError: Корень не найден (пакет установлен не из репозитория) — пути нужно задать явно.
    """
    root = find_project_root([Path(__file__).resolve().parent, Path.cwd().resolve()])
    if root is None:
        raise ConfigError(
            'корень репозитория autostudio не найден (каталог с pyproject.toml и operator.example.toml): '
            'задайте пути явно — --data-dir или AUTOSTUDIO_OFFICE_DIR, --operator или AUTOSTUDIO_OPERATOR, --templates'
        )
    return root
