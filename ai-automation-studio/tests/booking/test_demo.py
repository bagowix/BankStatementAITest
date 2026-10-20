from pathlib import Path

import pytest

from autostudio.booking import demo
from autostudio.booking.demo import find_example, run_demo
from autostudio.errors import ConfigError
from autostudio.registry import PACKAGES, demo_for, highlights_for, metrics_for


async def test__run_demo__full_offline_scenario(tmp_path):
    summary = await run_demo(tmp_path)
    assert 'записей: 3 (из свободного текста: 1), отмен: 1' in summary
    assert 'ответов из FAQ: 1, передано человеку: 1' in summary
    assert 'напоминаний: 2 (повторный проход: 0, после перезапуска: 0)' in summary
    assert 'записей, пока салон был закрыт: 2 из 3' in summary
    transcript = (tmp_path / 'transcript.md').read_text(encoding='utf-8')
    assert 'Можно на стрижку завтра после 17?' in transcript
    assert 'Sorry, that time has just been taken' in transcript
    assert 'Store check: 2 active bookings — nothing was cancelled.' in transcript
    assert '⏰ Напоминаем' in transcript and '⏰ Reminder' in transcript
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert '67% of bookings came in while you were closed (2 of 3).' in report
    assert 'Successful runs: 3 of 3' in report
    bookings_csv = (tmp_path / 'Bookings.csv').read_text(encoding='utf-8').splitlines()
    assert len(bookings_csv) == 5 and bookings_csv[0].startswith('Logged at,Action')
    assert len((tmp_path / 'Schedule.csv').read_text(encoding='utf-8').splitlines()) == 3


async def test__run_demo__repeatable(tmp_path):
    first = await run_demo(tmp_path)
    second = await run_demo(tmp_path)
    strip = [line for line in first.splitlines() if 'файлы' not in line]
    assert strip == [line for line in second.splitlines() if 'файлы' not in line]


def test__registry__booking_entries_resolve():
    info = PACKAGES['booking']
    assert metrics_for(info) and highlights_for(info) is not None and demo_for(info) is run_demo
    assert (Path(__file__).resolve().parents[2] / info.example).exists()


def test__find_example__missing__config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, '__file__', str(tmp_path / 'a' / 'b' / 'c' / 'demo.py'))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match='не найден образец'):
        find_example()


async def test__script_tap__missing_button__config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, '_scenario', _broken_scenario)
    with pytest.raises(ConfigError, match='нет кнопки'):
        await run_demo(tmp_path)


async def _broken_scenario(script, clock, config, store):
    persona = demo.Persona(key='tg:1', name='Anna', language='en')
    await script.say(persona, '/start')
    await script.tap(persona, 'No such button')
