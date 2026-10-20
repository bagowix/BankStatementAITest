import logging

from autostudio import logsafe


def test__redact__telegram_token_in_url__masked():
    line = (
        'HTTP Request: POST https://api.telegram.org/bot123456789:AAH8f9dk2LmZpQ0x_AbCdEfGhIjKlMnOpQr/sendMessage "200"'
    )
    assert 'AAH8f9dk2' not in logsafe.redact(line)
    assert '/bot***/sendMessage' in logsafe.redact(line)


def test__redact__bearer_and_api_keys__masked():
    text = 'Authorization: Bearer ya29.a0AfH6SMBxyz123 key sk-ant-api03-abcdefghijklmnop sk-proj-ABCDEFGHIJKLMNOPQR'
    out = logsafe.redact(text)
    assert 'ya29' not in out
    assert 'sk-ant-api03' not in out
    assert 'sk-proj-ABC' not in out


def test__redact__key_value_params__value_masked_key_kept():
    out = logsafe.redact('GET /cb?code=1&access_token=abc.def.ghi&refresh_token="r-123456" password: hunter22')
    assert 'access_token=***' in out
    assert 'refresh_token="***' in out
    assert 'password: ***' in out
    assert 'abc.def' not in out and 'hunter22' not in out


def test__redact__exact_env_values__masked_and_plain_text_untouched():
    secrets = logsafe.secret_values(
        {'INBOX_IMAP_PASSWORD': 'abcd efgh ijkl mnop', 'AUTOSTUDIO_LLM_PROVIDER': 'anthropic', 'X_TOKEN': 'short'}
    )
    assert secrets == ['abcd efgh ijkl mnop']
    assert logsafe.redact('login failed for abcd efgh ijkl mnop', secrets) == 'login failed for ***'
    assert logsafe.redact('5 bookings made, 2 reminders sent') == '5 bookings made, 2 reminders sent'


def test__redacting_formatter__covers_traceback():
    formatter = logsafe.RedactingFormatter('%(message)s', secrets=['s3cr3t-value'])
    try:
        raise RuntimeError(
            'url=https://api.telegram.org/bot123456789:AAH8f9dk2LmZpQ0x_AbCdEfGhIjKlMnOpQr/getMe s3cr3t-value'
        )
    except RuntimeError:
        record = logging.LogRecord('x', logging.ERROR, __file__, 1, 'boom', None, exc_info=__import__('sys').exc_info())
    out = formatter.format(record)
    assert 'AAH8f9dk2' not in out
    assert 's3cr3t-value' not in out
    assert 'RuntimeError' in out


def test__install__sets_formatter_on_handlers(monkeypatch):
    logger = logging.getLogger('test_logsafe_install')
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    try:
        logsafe.install('%(message)s', logger)
        assert isinstance(handler.formatter, logsafe.RedactingFormatter)
    finally:
        logger.removeHandler(handler)
