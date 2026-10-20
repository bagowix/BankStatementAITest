from autostudio.events import Event
from autostudio.inbox.metrics import METRICS, highlights
from tests.inbox.helpers import NOW


def _event(kind: str, **payload: str) -> Event:
    return Event(at=NOW, package='inbox', kind=kind, payload=payload)


EVENTS = [
    _event('inbox.triaged', needs_reply='yes'),
    _event('inbox.triaged', needs_reply='yes'),
    _event('inbox.triaged', needs_reply='no'),
    _event('inbox.filtered'),
    _event('inbox.draft_created'),
    _event('inbox.lead_found', urgency='high'),
    _event('inbox.lead_found', urgency='normal'),
    _event('inbox.injection_blocked'),
]


def test__metrics__codes_labels_and_honest_minutes():
    kinds = {spec.kind: spec for spec in METRICS}
    assert set(kinds) == {
        'inbox.triaged',
        'inbox.filtered',
        'inbox.draft_created',
        'inbox.lead_found',
        'inbox.injection_blocked',
        'inbox.failed',
    }
    assert (kinds['inbox.triaged'].minutes_saved, kinds['inbox.draft_created'].minutes_saved) == (2, 4)
    assert kinds['inbox.lead_found'].minutes_saved == 1 and kinds['inbox.injection_blocked'].minutes_saved == 0
    assert all(spec.label['en'] and spec.label['ru'] for spec in METRICS)


def test__highlights__english__leads_drafts_hours_injections():
    assert highlights(EVENTS, 'en') == [
        '2 new leads found in the inbox, 1 of them urgent.',
        'Reply drafts were ready for 1 of 2 emails that needed an answer (50%).',
        'About 0.2 h of manual email work saved (estimate).',
        '1 email with hidden instructions to the AI was blocked — no action was taken on it.',
    ]


def test__highlights__russian_and_unknown_language_fallback():
    ru = highlights(EVENTS, 'ru')
    assert ru[0] == 'Найдено новых лидов: 2, из них срочных: 1.'
    assert highlights(EVENTS, 'ka')[0].startswith('2 new leads')


def test__highlights__no_events__empty():
    assert highlights([], 'en') == []
    assert highlights([_event('inbox.failed')], 'en') == []
