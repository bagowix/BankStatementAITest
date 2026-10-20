import csv

import pytest

from autostudio.errors import ConfigError
from autostudio.inbox import demo
from autostudio.inbox.demo import run_demo


async def test__run_demo__full_offline_scenario__idempotent_second_run(tmp_path):
    summary = await run_demo(tmp_path)
    assert 'Run 1: 10 processed, 0 failed; 2 sorted by rules without AI' in summary
    assert 'Run 2 (same mailbox, 15 min later): 0 processed, 0 model calls, 0 drafts' in summary
    assert 'BLOCKED: injection, operator alerted' in summary
    assert 'phone +1 415 555 0134' in summary and 'was dropped' in summary
    assert 'booking_request 3' in summary and 'lead 3' in summary
    drafts = sorted((tmp_path / 'drafts').glob('*.eml'))
    assert len(drafts) == 6
    with (tmp_path / 'Inbox.csv').open(encoding='utf-8', newline='') as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 11
    injected = next(row for row in rows if row[2].startswith('Group booking for 20'))
    assert injected[13] == 'no' and 'suspicious' in injected[14]

    again = await run_demo(tmp_path)
    assert 'Run 1: 10 processed' in again
    assert len(list((tmp_path / 'drafts').glob('*.eml'))) == 6


def test__examples_dir__not_found__config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, '__file__', str(tmp_path / 'a' / 'b' / 'c' / 'demo.py'))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match='examples/inbox'):
        demo.examples_dir()
