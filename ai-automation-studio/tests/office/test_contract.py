from pathlib import Path

import msgspec
import pytest

from autostudio.errors import ConfigError
from autostudio.office.contract import (
    TEMPLATES,
    AgreementInfo,
    ContractIncompleteError,
    ContractParty,
    ContractRequest,
    classify,
    contract_html,
    fields_skeleton,
    load_contract_request,
    load_template,
    merge_requests,
    normalize_key,
    operator_fields,
    output_name,
    render_contract,
    scan_placeholders,
    semantic_keys,
    strip_template_box,
    template_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGAL = REPO_ROOT / 'docs' / 'legal'

TEMPLATE = """# Software Services Agreement

> **Template. Not legal advice.** Fill the fields in `{{double braces}}` once and
> `[square brackets]` for each client. Delete every `[LAWYER: …]` note.

**Agreement No.** [number] **dated** [date]

1. **{{NAME}}**, identification number {{IE_ID}}[, direct clients only: email {{EMAIL}}] [, working
   name "AutoStudio"] (the **Developer**); and
2. **[Client's legal name]**, [legal form], address [address].

1.3. The Services are not consulting. [ACCOUNTANT: confirm that this wording describes software
development and not "consulting".]

4.3. The Developer shows the system on real data [or on test data agreed with the Client].

7.3. Pay to {{BANK_DETAILS}}. Keep `[code, not a field]` and [a link](https://example.com).

- [ ] a checkbox stays

18.3. Disputes go to [the courts of Georgia]
[arbitration under the rules of [institution], in English]. [LAWYER: choose.]

| Item | Value |
|---|---|
| Hosting | [Shared server in {{SERVER_LOCATION}} / Dedicated: [details]] |
| Backups | [VERIFY: backups.] Daily copies. |

Date: [date]
"""

FULL_FIELDS = {
    ', direct clients only: email {{EMAIL}}': ', email {{EMAIL}}',
    ', working name "AutoStudio"': '',
    'or on test data agreed with the Client': '',
    'the courts of Georgia': 'the courts of Georgia',
    'arbitration under the rules of [institution], in English': '',
    'Shared server in {{SERVER_LOCATION}} / Dedicated: [details]': 'Shared server in {{SERVER_LOCATION}}',
    'VERIFY: backups.': '',
}


def _request(**fields: str) -> ContractRequest:
    return ContractRequest(
        client=ContractParty(name='Example LLC', legal_form='LLC', address='Tbilisi'),
        agreement=AgreementInfo(number='SA-2026-001', date='2026-10-20'),
        fields={**FULL_FIELDS, **fields},
    )


def _render(operator, request: ContractRequest, **kwargs: bool):
    return render_contract(
        TEMPLATE, template='service-agreement', language='en', operator=operator, request=request, **kwargs
    )


def test__scan__nested_code_links_checkboxes__top_level_fields_only():
    found = scan_placeholders(strip_template_box(TEMPLATE))
    keys = [item.key for item in found]
    assert 'arbitration under the rules of [institution], in English' in keys
    assert 'institution' not in keys
    assert 'code, not a field' not in keys
    assert 'a link' not in keys
    assert '' not in keys and ' ' not in keys
    # Перенос строки внутри скобки нормализуется в пробел.
    assert ', working name "AutoStudio"' in keys
    dates = [item for item in found if item.key == 'date']
    assert [(d.occurrence, d.line) for d in dates] == [(1, 6), (2, 29)]


def test__classify__review_verify_and_fields():
    assert classify('LAWYER: choose.') == 'review'
    assert classify('ACCOUNTANT/LAWYER: this follows') == 'review'
    assert classify('ЮРИСТ: проверить') == 'review'
    assert classify('БУХГАЛТЕР/ЮРИСТ: x') == 'review'
    assert classify('VERIFY: backups.') == 'verify'
    assert classify('ПРОВЕРИТЬ: копии.') == 'verify'
    assert classify('Inbox: mailboxes, categories') == 'field'
    assert classify('USD 40 / GEL 100') == 'field'


def test__normalize_key__newlines_and_quote_markers__single_spaces():
    assert normalize_key('For clinics:\n> Please   do not') == 'For clinics: Please do not'


def test__strip_template_box__removed_and_line_numbers_kept():
    stripped = strip_template_box(TEMPLATE)
    assert 'Template. Not legal advice' not in stripped
    assert stripped.count('\n') == TEMPLATE.count('\n')
    assert strip_template_box('# T\n\nNo box here.') == '# T\n\nNo box here.'


def test__render__everything_filled__no_placeholders_left(operator):
    rendered = _render(operator, _request(), drop_review_notes=True)
    text = rendered.text
    assert '**Jane Example**, identification number 000000000, email jane@example.com (the **Developer**)' in text
    assert '**Example LLC**, LLC, address Tbilisi.' in text
    assert 'on real data.' in text  # пустая скобка не оставляет « .»
    assert 'not consulting.\n\n4.3.' in text
    assert 'Disputes go to the courts of Georgia.' in text
    assert '| Hosting | Shared server in Example Country |' in text
    assert '| Backups | Daily copies. |' in text
    assert 'IBAN GE07 XX00 0000 0000 0000 00, JSC Example Bank, SWIFT/BIC XXXXGE22' in text
    assert '`[code, not a field]`' in text and '[a link](https://example.com)' in text
    assert '{{' not in text and '[LAWYER' not in text and '[VERIFY' not in text
    assert rendered.dropped_notes == 2
    assert '\n\n\n' not in text


def test__render__missing_placeholders__error_lists_every_one(operator):
    request = ContractRequest(client=ContractParty(name='Example LLC'))
    with pytest.raises(ContractIncompleteError) as caught:
        _render(operator, request)
    error = caught.value
    message = str(error)
    assert 'файл не записан' in message
    for expected in ('стр. 6, 29: [date]', '[legal form]', '[address]', '[number]', '[LAWYER: choose.]'):
        assert expected in message
    assert 'VERIFY/ПРОВЕРИТЬ' in message
    assert '--drop-review-notes' in message
    assert 'date' in error.missing and 'VERIFY: backups.' in error.missing
    assert "Client's legal name" not in error.missing


def test__render__verify_notes__never_dropped_by_flag(operator):
    fields = dict(FULL_FIELDS)
    del fields['VERIFY: backups.']
    request = msgspec.structs.replace(_request(), fields=fields)
    with pytest.raises(ContractIncompleteError) as caught:
        _render(operator, request, drop_review_notes=True)
    assert caught.value.missing == ['VERIFY: backups.']


def test__render__missing_operator_field__points_to_operator_toml(operator):
    bare = msgspec.structs.replace(operator, contract_fields={}, telegram=None)
    with pytest.raises(ContractIncompleteError) as caught:
        _render(bare, _request(), drop_review_notes=True)
    assert '{{SERVER_LOCATION}} → [contract_fields] SERVER_LOCATION' in str(caught.value)
    assert caught.value.missing == ['{{SERVER_LOCATION}}']


def test__render__client_text__inserted_as_data_and_escaped_in_html(operator):
    request = msgspec.structs.replace(
        _request(), client=ContractParty(name='<script>alert(1)</script>', legal_form='LLC', address='{{IE_ID}}')
    )
    rendered = _render(operator, request, drop_review_notes=True)
    page = contract_html(rendered.text, language='en')
    assert '<script>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    # Поля оператора подставляются и во вписанные значения: так вариант «сервер в {{SERVER_LOCATION}}» работает.
    assert 'address 000000000' in rendered.text
    assert page.startswith('<!doctype html><html lang="en">')
    assert '<title>Software Services Agreement</title>' in page
    assert '@page { size: A4' in page


def test__render__occurrence_override_and_notices(operator):
    rendered = _render(operator, _request(**{'date#2': '21 October 2026', 'unused key': 'x'}), drop_review_notes=True)
    assert '**dated** 2026-10-20' in rendered.text
    assert 'Date: 21 October 2026' in rendered.text
    assert any('не найден в шаблоне' in notice for notice in rendered.notices)
    single = _render(operator, _request(), drop_review_notes=True)
    assert any('[date] ×2' in notice for notice in single.notices)


def test__render__blank_semantic_value__refused(operator):
    request = msgspec.structs.replace(_request(), client=ContractParty(name='  '))
    with pytest.raises(ConfigError, match='name: пустое'):
        _render(operator, request)


def test__semantic_keys__address_means_client_address_only_in_agreement():
    assert semantic_keys('service-agreement', 'en')['address'] == 'address'
    assert 'address' not in semantic_keys('dpa', 'en')
    assert semantic_keys('dpa', 'ru')['название бизнеса'] == 'business_name'
    assert semantic_keys('support-sla', 'ru')['наименование Заказчика'] == 'name'


def test__operator_fields__builtin_and_overrides(operator):
    fields = operator_fields(operator, 'ru')
    assert fields['NAME'] == 'Джейн Пример'
    assert fields['TELEGRAM'] == 'jane_example'
    assert fields['BANK_DETAILS'].startswith('IBAN GE07 XX00') and 'банк JSC Example Bank' in fields['BANK_DETAILS']
    overridden = msgspec.structs.replace(operator, contract_fields={'NAME': 'J. Example'})
    assert operator_fields(overridden, 'en')['NAME'] == 'J. Example'


def test__request_files__merge_and_toml_date(tmp_path):
    defaults = tmp_path / 'defaults.toml'
    defaults.write_text('[fields]\n"LAWYER: choose." = ""\n"English" = "English"\n', encoding='utf-8')
    client = tmp_path / 'client.toml'
    client.write_text(
        '[client]\nname = "Example LLC"\n[agreement]\nnumber = "SA-1"\ndate = 2026-10-20\n[fields]\n"English" = "Russian"\n',
        encoding='utf-8',
    )
    merged = merge_requests([load_contract_request(defaults), load_contract_request(client)])
    assert merged.agreement == AgreementInfo(number='SA-1', date='2026-10-20')
    assert merged.fields == {'LAWYER: choose.': '', 'English': 'Russian'}
    assert merged.client.name == 'Example LLC'
    assert merged.semantic()['business_name'] == 'Example LLC'


def test__request_files__errors(tmp_path):
    with pytest.raises(ConfigError, match='не найден'):
        load_contract_request(tmp_path / 'nope.toml')
    bad = tmp_path / 'bad.toml'
    bad.write_text('[client\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='битый TOML'):
        load_contract_request(bad)
    bad.write_text('[client]\nnickname = "x"\n', encoding='utf-8')
    with pytest.raises(ConfigError, match='nickname'):
        load_contract_request(bad)


def test__output_name__slug_from_number_or_client():
    assert output_name(_request(), 'dpa', 'ru', 'html') == 'SA-2026-001-dpa.ru.html'
    by_client = ContractRequest(client=ContractParty(name='Acme / Barbers'))
    assert output_name(by_client, 'support-sla', 'en', 'md') == 'Acme-Barbers-support-sla.en.md'
    assert output_name(ContractRequest(), 'dpa', 'en', 'md') == 'contract-dpa.en.md'


def test__fields_skeleton__all_keys_commented():
    skeleton = fields_skeleton(TEMPLATE, template='service-agreement', language='en', label='t.md')
    assert '# name = ""  # [Client\'s legal name] ×1' in skeleton
    assert '# date = ""  # [date] ×2' in skeleton
    assert '# "arbitration under the rules of [institution], in English" = ""' in skeleton
    assert '# "LAWYER: choose." = ""' in skeleton
    assert '# "VERIFY: backups." = ""' in skeleton
    # Ни одной незакомментированной строки с значением: пустое не пройдёт молча.
    assert not [line for line in skeleton.splitlines() if '=' in line and not line.startswith('#')]


def test__load_template__missing__config_error(tmp_path):
    with pytest.raises(ConfigError, match='шаблон не найден'):
        load_template(template_path(tmp_path, 'dpa', 'en'))


@pytest.mark.parametrize('language', ['en', 'ru'])
@pytest.mark.parametrize('name', TEMPLATES)
def test__real_templates__unfilled__error_and_filled__clean(operator, name, language):
    # Шаблоны правит другой человек: проверяем свойства, а не текст. Пустой запрос — ошибка,
    # а заполненный каждой скобкой — договор без единой скобки-поля, пометки и {{…}}.
    text = load_template(template_path(LEGAL, name, language))
    with pytest.raises(ContractIncompleteError):
        render_contract(text, template=name, language=language, operator=operator, request=ContractRequest())
    keys = {item.key for item in scan_placeholders(strip_template_box(text))}
    filled = ContractRequest(fields=dict.fromkeys(keys, 'FILLED'))
    full_operator = msgspec.structs.replace(
        operator,
        contract_fields={
            'SERVER_LOCATION': 'Example Country',
            'HOSTING_PROVIDER': 'Example Hosting',
            'BACKUP_PROVIDER': 'Example Storage',
            'BACKUP_LOCATION': 'Example Country',
        },
    )
    rendered = render_contract(text, template=name, language=language, operator=full_operator, request=filled)
    for marker in ('{{', '[LAWYER', '[VERIFY', '[ЮРИСТ', '[ПРОВЕРИТЬ', '[ACCOUNTANT', '[БУХГАЛТЕР'):
        assert marker not in rendered.text
    assert 'Template. Not legal advice' not in rendered.text
    assert 'Шаблон. Не юридическая консультация' not in rendered.text
    assert contract_html(rendered.text, language=language).count('<h1>') == 1
