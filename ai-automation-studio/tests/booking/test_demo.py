from pathlib import Path

import pytest

from autostudio.booking import demo
from autostudio.booking.demo import find_example, run_demo
from autostudio.errors import ConfigError
from autostudio.registry import PACKAGES, demo_for, highlights_for, metrics_for


async def test__run_demo__offline__full_scenario_and_files(tmp_path):
    summary = await run_demo(tmp_path)
    assert 'записей: 4 (из свободного текста: 2), отмен: 1' in summary
    assert 'ответов из FAQ: 1, передано человеку: 1' in summary
    assert 'напоминаний: 2 (повторный проход: 0, после перезапуска: 0)' in summary
    assert 'записей, пока салон был закрыт: 3 из 4' in summary
    # Отменённая запись Anna удалена из календаря, три активные — событиями.
    assert 'Google Calendar (заглушка): событий записей сейчас 3' in summary
    assert 'WhatsApp: сообщений 6 (шаблоном: 1); поддельных вебхуков отброшено: 5' in summary
    transcript = (tmp_path / 'transcript.md').read_text(encoding='utf-8')
    assert 'Можно на стрижку завтра после 17?' in transcript
    assert 'Sorry, that time has just been taken' in transcript
    assert 'Store check: 2 active bookings — nothing was cancelled.' in transcript
    assert '⏰ Напоминаем' in transcript and '⏰ Reminder' in transcript
    # Личное дело Гиорги 15:00–16:00 из его календаря не предлагается: после 13:15 сразу 16:00.
    assert '`[13:00]` `[13:15]` `[16:00]` `[16:15]`' in transcript
    assert '**Bot → WhatsApp (list «Choose»):** Beard trim: free times that suit you:' in transcript
    assert 'agree to get a reminder about this visit from Demo Barbers here in WhatsApp' in transcript
    assert '(template `booking_reminder`, en_US):** Beard trim · Fri 23 Oct, 17:00' in transcript
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert '75% of bookings came in while you were closed (3 of 4).' in report
    assert 'Bookings by channel: Telegram 3, WhatsApp 1.' in report
    assert 'Successful runs: 4 of 4' in report
    bookings_csv = (tmp_path / 'Bookings.csv').read_text(encoding='utf-8').splitlines()
    assert len(bookings_csv) == 6 and bookings_csv[0].startswith('Logged at,Action')
    assert bookings_csv[1].endswith(',40,GEL,buttons,Telegram')
    # Последнее обновление вкладки — в четверг: в расписании только запись Nino на пятницу.
    schedule = (tmp_path / 'Schedule.csv').read_text(encoding='utf-8').splitlines()
    assert len(schedule) == 2 and ',Beard trim,25,GEL,Nino,' in schedule[1]


async def test__run_demo__twice__same_summary(tmp_path):
    first = await run_demo(tmp_path)
    second = await run_demo(tmp_path)
    strip = [line for line in first.splitlines() if 'файлы' not in line]
    assert strip == [line for line in second.splitlines() if 'файлы' not in line]


def test__registry__booking_entry__all_paths_resolve():
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
