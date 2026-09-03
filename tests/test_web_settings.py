"""Настройки — единственная граница между окружением и приложением.

Поэтому проверяется не «читаются ли переменные», а что происходит с тем, что
пришло снаружи неправильным.
"""

from bankstatement.web.settings import DEFAULT_MAX_UPLOAD, Settings


def test__from_env__nothing_set__defaults():
    settings = Settings.from_env({})

    assert settings.redis_url is None
    assert settings.samples_dir == 'samples'
    assert settings.max_upload == DEFAULT_MAX_UPLOAD


def test__from_env__everything_set__taken_as_is():
    settings = Settings.from_env(
        {
            'BANKSTATEMENT_REDIS': 'redis://localhost:6379',
            'BANKSTATEMENT_SAMPLES': '/data/samples',
            'BANKSTATEMENT_UPLOADS': '/data/uploads',
            'BANKSTATEMENT_MAX_UPLOAD': '1024',
            'BANKSTATEMENT_MAX_BATCH': '3',
            'BANKSTATEMENT_MAX_JOBS': '5',
            'BANKSTATEMENT_MAX_CONCURRENT': '1',
        }
    )

    assert settings.redis_url == 'redis://localhost:6379'
    assert (settings.samples_dir, settings.uploads_dir) == ('/data/samples', '/data/uploads')
    assert (settings.max_upload, settings.max_batch) == (1024, 3)
    assert (settings.max_jobs, settings.max_concurrent) == (5, 1)


def test__from_env__empty_redis_url__counts_as_absent():
    """Пустая переменная в окружении — обычное дело, и включать Redis она не должна."""
    assert Settings.from_env({'BANKSTATEMENT_REDIS': ''}).redis_url is None


def test__from_env__non_numeric_limit__falls_back_to_the_default():
    """Задокументировано намеренно: приложение с опечаткой в переменной поднимется.

    Оборотная сторона — `MAX_UPLOAD=64MB` тихо даёт значение по умолчанию. Для
    предела в мегабайтах это заметят не сразу, и падать на старте было бы честнее.
    """
    settings = Settings.from_env({'BANKSTATEMENT_MAX_UPLOAD': '64MB'})

    assert settings.max_upload == DEFAULT_MAX_UPLOAD


def test__from_env__trusted_proxy_disabled_by_default():
    settings = Settings.from_env({})

    assert settings.trusted_proxy is False


def test__from_env__trusted_proxy_enabled():
    """Доверять X-Forwarded-For можно явно, но не по умолчанию."""
    for value in ('1', 'true', 'yes', 'TRUE', 'YES'):
        settings = Settings.from_env({'BANKSTATEMENT_TRUSTED_PROXY': value})
        assert settings.trusted_proxy is True

    # Другие значения — ложь
    for value in ('0', 'false', 'no', ''):
        settings = Settings.from_env({'BANKSTATEMENT_TRUSTED_PROXY': value})
        assert settings.trusted_proxy is False


def test__from_env__resource_limits():
    """Пределы ресурсов: страниц, времени, хранения."""
    settings = Settings.from_env(
        {
            'BANKSTATEMENT_MAX_PAGES': '500',
            'BANKSTATEMENT_DOCUMENT_TIMEOUT': '1200',
            'BANKSTATEMENT_UPLOAD_TTL': '172800',  # 2 дня
        }
    )

    assert settings.max_pages == 500
    assert settings.document_timeout == 1200.0
    assert settings.upload_ttl == 172800.0


def test__from_env__defaults_for_resource_limits():
    settings = Settings.from_env({})

    assert settings.max_pages == 400
    assert settings.document_timeout == 900.0
    assert settings.upload_ttl == 24 * 3600.0
