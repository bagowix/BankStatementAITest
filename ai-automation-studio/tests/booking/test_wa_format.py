import hashlib
import hmac

import msgspec
import pytest

from autostudio.booking.messages import Button, Outgoing
from autostudio.booking.wa_format import (
    BUTTON_TITLE_MAX,
    MORE_PREFIX,
    OWN_PHONE_ID,
    ROW_TITLE_MAX,
    Labels,
    Option,
    WebhookPayload,
    fit,
    options_of,
    page_of,
    parse_webhook,
    render,
    template_message,
    verify_signature,
)

LABELS = Labels(
    list_button='Choose',
    section='Options',
    more='More…',
    choose='Please choose:',
    use_my_number='Use this number',
    opt_in='By confirming, you agree to get a reminder from Demo here in WhatsApp.',
)


def as_json(message) -> dict:
    return msgspec.json.decode(msgspec.json.encode(message))


def buttons(*pairs: tuple[str, str]) -> list[list[Button]]:
    return [[Button(label, data)] for label, data in pairs]


def test__render__plain_text__text_message_without_empty_fields():
    [message] = render(Outgoing(text='Hello'), labels=LABELS, phone_button=False)
    assert as_json(message) == {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'type': 'text',
        'text': {'body': 'Hello'},
    }


def test__render__three_buttons__interactive_buttons_ids_kept():
    outgoing = Outgoing(text='Pick', buttons=buttons(('Yes', 'cy:K7Q2MX'), ('No', 'bk:K7Q2MX'), ('Menu', 'm')))
    [message] = render(outgoing, labels=LABELS, phone_button=False)
    body = as_json(message)['interactive']
    assert body['type'] == 'button' and body['body'] == {'text': 'Pick'}
    assert [b['reply'] for b in body['action']['buttons']] == [
        {'id': 'cy:K7Q2MX', 'title': 'Yes'},
        {'id': 'bk:K7Q2MX', 'title': 'No'},
        {'id': 'm', 'title': 'Menu'},
    ]
    assert all(b['type'] == 'reply' for b in body['action']['buttons'])


def test__render__long_button_titles__truncated_to_20_and_made_unique():
    long = 'Wed 21 Oct, 12:00 · Haircut and beard'
    outgoing = Outgoing(text='Your bookings', buttons=buttons((long, 'bk:A'), (long + ' 2', 'bk:B')))
    [message] = render(outgoing, labels=LABELS, phone_button=False)
    titles = [b['reply']['title'] for b in as_json(message)['interactive']['action']['buttons']]
    assert len(set(titles)) == 2
    assert all(len(title.encode('utf-16-le')) // 2 <= BUTTON_TITLE_MAX for title in titles)
    assert titles[0].endswith('…')


def test__render__body_over_1024_with_buttons__text_first_then_short_prompt():
    outgoing = Outgoing(text='x' * 1500, buttons=buttons(('OK', 'm')))
    first, second = render(outgoing, labels=LABELS, phone_button=False)
    assert first.type == 'text' and first.text is not None and first.text.body == 'x' * 1500
    assert second.interactive is not None and second.interactive.body.text == 'Please choose:'


def test__render__more_than_three__list_with_full_label_in_description():
    rows = buttons(*[(f'Haircut + beard + wash · {i} GEL', f'sv:s{i}') for i in range(5)])
    [message] = render(Outgoing(text='Which service?', buttons=rows), labels=LABELS, phone_button=False)
    body = as_json(message)['interactive']
    assert body['type'] == 'list' and body['action']['button'] == 'Choose'
    [section] = body['action']['sections']
    assert section['title'] == 'Options' and len(section['rows']) == 5
    row = section['rows'][0]
    assert len(row['title']) <= ROW_TITLE_MAX and row['title'].endswith('…')
    assert row['description'] == 'Haircut + beard + wash · 0 GEL' and row['id'] == 'sv:s0'


def test__render__short_list_rows__no_description():
    rows = buttons(*[(f'{h}:00', f't:{h}') for h in range(10, 14)])
    [message] = render(Outgoing(text='Time?', buttons=rows), labels=LABELS, phone_button=False)
    assert all('description' not in row for row in as_json(message)['interactive']['action']['sections'][0]['rows'])


def test__page_of__26_options__9_and_more_until_last_page():
    options = [Option(f't:{i}', f'{i}') for i in range(26)]
    first, more = page_of(options, 0, LABELS)
    assert more and len(first) == 10 and first[-1] == Option(f'{MORE_PREFIX}9', 'More…')
    second, more = page_of(options, 9, LABELS)
    assert more and second[0].id == 't:9' and second[-1].id == f'{MORE_PREFIX}18'
    last, more = page_of(options, 18, LABELS)
    assert not more and [option.id for option in last] == [f't:{i}' for i in range(18, 26)]


def test__render__second_page__short_body_and_next_rows():
    rows = buttons(*[(f'{i:02d}:00', f't:{i}') for i in range(12)])
    [message] = render(Outgoing(text='Pick a time', buttons=rows), labels=LABELS, phone_button=False, offset=9)
    body = as_json(message)['interactive']
    assert body['body']['text'] == 'Please choose:'
    assert [row['id'] for row in body['action']['sections'][0]['rows']] == ['t:9', 't:10', 't:11']


def test__render__request_contact__own_number_and_keyboard_buttons():
    outgoing = Outgoing(text='Your phone?', request_contact='📱 Share my number', keyboard=['✖️ Cancel'])
    [message] = render(outgoing, labels=LABELS, phone_button=True)
    replies = [b['reply'] for b in as_json(message)['interactive']['action']['buttons']]
    assert replies == [{'id': OWN_PHONE_ID, 'title': 'Use this number'}, {'id': 'wa:kb:✖️ Cancel', 'title': '✖️ Cancel'}]
    # Без известного телефона (username) — только клавиатура: номер покупатель наберёт сам.
    assert [o.id for o in options_of(outgoing, phone_button=False, labels=LABELS)] == ['wa:kb:✖️ Cancel']


def test__render__confirm_screen__opt_in_text_appended():
    outgoing = Outgoing(text='Please check', buttons=buttons(('✅ Confirm', 'ok:b:haircut:_:202610211200')))
    [message] = render(outgoing, labels=LABELS, phone_button=False)
    assert as_json(message)['interactive']['body']['text'] == f'Please check\n\n{LABELS.opt_in}'


def test__template_message__positional_body_parameters():
    message = template_message('booking_reminder', 'en_US', ['Haircut', 'Wed 21 Oct, 12:00'])
    assert as_json(message)['template'] == {
        'name': 'booking_reminder',
        'language': {'code': 'en_US'},
        'components': [
            {
                'type': 'body',
                'parameters': [{'text': 'Haircut', 'type': 'text'}, {'text': 'Wed 21 Oct, 12:00', 'type': 'text'}],
            }
        ],
    }


@pytest.mark.parametrize(
    ('text', 'limit', 'expected'),
    [('short', 20, 'short'), ('a b   c', 20, 'a b c'), ('abcdefghij', 5, 'abcd…'), ('🙂🙂🙂', 4, '🙂…')],
)
def test__fit__limits_in_utf16_units__ellipsis(text, limit, expected):
    assert fit(text, limit) == expected


def sign(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test__verify_signature__valid_forged_missing_or_empty_secret():
    body = b'{"object":"whatsapp_business_account"}'
    assert verify_signature('s3cret', body, sign('s3cret', body))
    assert verify_signature('s3cret', body, sign('s3cret', body).upper().replace('SHA256=', 'sha256='))
    assert not verify_signature('s3cret', body, sign('other', body))
    assert not verify_signature('s3cret', body + b' ', sign('s3cret', body))
    assert not verify_signature('s3cret', body, None)
    assert not verify_signature('s3cret', body, 'md5=abc')
    assert not verify_signature('', body, sign('', body))


def webhook(*messages: dict, statuses: list[dict] | None = None, contacts: list[dict] | None = None) -> WebhookPayload:
    value = {
        'messaging_product': 'whatsapp',
        'metadata': {'display_phone_number': '15550783881', 'phone_number_id': '106540352242922'},
        'contacts': contacts if contacts is not None else [{'profile': {'name': 'Sheena'}, 'wa_id': '16505551234'}],
        'messages': list(messages),
        'statuses': statuses or [],
        'unknown_future_field': {'x': 1},
    }
    raw = {
        'object': 'whatsapp_business_account',
        'entry': [{'id': 'WABA', 'changes': [{'field': 'messages', 'value': value}]}],
    }
    return msgspec.json.decode(msgspec.json.encode(raw), type=WebhookPayload)


def test__parse_webhook__text_message__recorded_shape_from_meta_docs():
    [message], failures = parse_webhook(
        webhook(
            {
                'from': '16505551234',
                'id': 'wamid.A',
                'timestamp': '1749416383',
                'type': 'text',
                'text': {'body': 'Does it come in another color?'},
            }
        )
    )
    assert failures == []
    assert (message.message_id, message.phone, message.user_id, message.name) == (
        'wamid.A',
        '16505551234',
        None,
        'Sheena',
    )
    assert message.text == 'Does it come in another color?' and message.sent_at is not None
    assert message.sent_at.year == 2025 and not message.ignored


def test__parse_webhook__bsuid_without_phone__user_id_and_matching_profile():
    contacts = [{'user_id': 'US.111', 'profile': {'name': 'Other'}}, {'user_id': 'US.222', 'profile': {'name': 'Nino'}}]
    [message], _ = parse_webhook(
        webhook({'from_user_id': 'US.222', 'id': 'wamid.B', 'type': 'text', 'text': {'body': 'hi'}}, contacts=contacts)
    )
    assert message.phone is None and message.user_id == 'US.222' and message.name == 'Nino'
    assert message.sent_at is None


def test__parse_webhook__button_list_template_replies__reply_ids():
    replies, _ = parse_webhook(
        webhook(
            {
                'from': '1',
                'id': 'w1',
                'type': 'interactive',
                'interactive': {'type': 'button_reply', 'button_reply': {'id': 'ok:x', 'title': 'Confirm'}},
            },
            {
                'from': '1',
                'id': 'w2',
                'type': 'interactive',
                'interactive': {
                    'type': 'list_reply',
                    'list_reply': {'id': 't:y', 'title': '17:00', 'description': 'd'},
                },
            },
            {'from': '1', 'id': 'w3', 'type': 'button', 'button': {'payload': 'm', 'text': 'Menu'}},
        )
    )
    assert [(m.reply_id, m.text) for m in replies] == [('ok:x', 'Confirm'), ('t:y', '17:00'), ('m', 'Menu')]


def test__parse_webhook__shared_contacts__own_card_verified_foreign_not():
    own = {
        'from': '16505551234',
        'id': 'c1',
        'type': 'contacts',
        'contacts': [
            {'name': {'formatted_name': 'Me'}, 'phones': [{'phone': '+1 (650) 555-1234', 'wa_id': '16505551234'}]}
        ],
    }
    other = {
        'from': '16505551234',
        'id': 'c2',
        'type': 'contacts',
        'contacts': [{'phones': [{'wa_id': '14125550829', 'type': 'MOBILE'}]}],
    }
    empty = {'from': '16505551234', 'id': 'c3', 'type': 'contacts', 'contacts': [{'name': {'first_name': 'X'}}]}
    first, second, third = parse_webhook(webhook(own, other, empty))[0]
    assert (first.shared_phone, first.shared_own) == ('+1 (650) 555-1234', True)
    assert (second.shared_phone, second.shared_own) == ('+14125550829', False)
    assert third.shared_phone is None and third.text == ''


def test__parse_webhook__referral_reactions_statuses__source_ignored_failures():
    ad = {
        'from': '1',
        'id': 'r1',
        'type': 'text',
        'text': {'body': 'info?'},
        'referral': {'source_type': 'ad', 'source_id': '120', 'ctwa_clid': 'X'},
    }
    reaction = {'from': '1', 'id': 'r2', 'type': 'reaction', 'reaction': {'emoji': '👍', 'message_id': 'w'}}
    image = {'from': '1', 'id': 'r3', 'type': 'image', 'image': {'id': '1'}}
    no_id = {'from': '1', 'type': 'text', 'text': {'body': 'x'}}
    statuses = [
        {'id': 'out1', 'status': 'delivered', 'recipient_id': '1'},
        {'id': 'out2', 'status': 'failed', 'errors': [{'code': 131047, 'title': 'Re-engagement message'}]},
        {'id': 'out3', 'status': 'failed'},
    ]
    messages, failures = parse_webhook(webhook(ad, reaction, image, no_id, statuses=statuses))
    assert [m.message_id for m in messages] == ['r1', 'r2', 'r3']
    assert messages[0].referral == 'ad' and messages[1].ignored and not messages[2].ignored
    assert [(f.message_id, f.code) for f in failures] == [('out2', 131047), ('out3', 0)]


def test__parse_webhook__other_fields_and_empty_values__skipped():
    raw = {
        'object': 'whatsapp_business_account',
        'entry': [
            {
                'changes': [
                    {'field': 'account_update', 'value': {'messages': [{'id': 'x', 'type': 'text'}]}},
                    {'field': 'messages'},
                ]
            }
        ],
    }
    payload = msgspec.json.decode(msgspec.json.encode(raw), type=WebhookPayload)
    assert parse_webhook(payload) == ([], [])
