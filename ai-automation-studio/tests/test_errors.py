import pytest

from autostudio.errors import LLMError, kind_for_status


@pytest.mark.parametrize(
    ('status', 'kind'),
    [
        (429, 'outage'),
        (500, 'outage'),
        (529, 'outage'),
        (408, 'outage'),
        (401, 'config'),
        (403, 'config'),
        (404, 'config'),
        (400, 'content'),
        (413, 'content'),
    ],
)
def test__kind_for_status__mapping(status, kind):
    assert kind_for_status(status) == kind


def test__llm_error__defaults_to_content():
    exc = LLMError('bad schema')
    assert exc.kind == 'content'
    assert exc.status_code is None
