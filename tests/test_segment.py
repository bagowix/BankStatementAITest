from datetime import date
from decimal import Decimal

from bankstatement.models import Account, Period, Summary
from bankstatement.segment import Segment, merge_accounts, reread, segment, starts_statement, vote_bank
from tests.conftest import page


class FakeIngestion:
    """Ingestion без PDF: сегментации нужны только строки страниц."""

    def __init__(self, pages, workers=1):
        self._pages = pages
        self.workers = workers

    @property
    def page_count(self):
        return len(self._pages)

    def prefetch(self, pages, *, min_level=0):
        """Настоящий Ingestion греет кэш параллельно; здесь греть нечего."""

    def lines(self, index, *, min_level=0):
        return self._pages[index]


def test__starts_statement__page_declares_opening_balance__true():
    # Нужны 2+ поля для блока сводки, не одинокий beginning_balance
    assert starts_statement(
        page('Beginning Balance as of 04/01/2025 $597,068.70', 'Ending Balance as of 04/30/2025 $620,000.00')
    )
    assert not starts_statement(page('Date Description Deposits Withdrawals Balance'))


def test__segment__bundle_of_statements__split_at_each_opening_balance():
    """Binder — подшивка: новая выписка начинается там, где заявлен остаток."""
    pages = [
        page('Beginning Balance as of 04/01/2025 $100.00', 'Ending Balance as of 04/30/2025 $120.00'),
        page('04/01 SOMETHING 20.00 120.00', page_index=1),
        page('Beginning Balance as of 05/01/2025 $120.00', 'Ending Balance as of 05/31/2025 $130.00', page_index=2),
    ]
    parts = segment(FakeIngestion(pages))

    assert [p.pages for p in parts] == [(0, 1), (2,)]
    assert parts[0].summary.beginning_balance == Decimal('100.00')


def test__segment__pages_before_first_opening_balance__joined_to_first():
    pages = [
        page('COVER PAGE'),
        page('Beginning Balance as of 04/01/2025 $100.00', page_index=1),
    ]
    parts = segment(FakeIngestion(pages))

    assert len(parts) == 1
    assert parts[0].pages == (0, 1)


def test__segment__no_opening_balance_anywhere__single_part():
    parts = segment(FakeIngestion([page('04/01 SOMETHING 20.00 120.00')]))

    assert len(parts) == 1


def test__segment__header_repeated_on_every_page__one_statement():
    """Повторённая на каждой странице сводка не открывает новую выписку."""
    header = ('Opening balance 100.00', 'Closing balance 120.00')
    first = page(*header, '04/01 DEPOSIT 50.00 150.00')
    second = page(*header, '04/02 PAYMENT 30.00 120.00', page_index=1)
    other = page('Opening balance 120.00', 'Closing balance 130.00', '05/01 DEPOSIT 10.00 130.00', page_index=2)

    parts = segment(FakeIngestion([first, second, other]))

    assert [p.pages for p in parts] == [(0, 1), (2,)]


def test__segment__two_empty_months_with_identical_totals__still_two_statements():
    """Одинаковые остатки и нулевые обороты различает только период."""
    totals = ('Opening balance 100.00', 'Closing balance 100.00', 'Deposits 0.00', 'Withdrawals 0.00')
    april = page('Statement period 04/01/2025 - 04/30/2025', *totals)
    may = page('Statement period 05/01/2025 - 05/31/2025', *totals, page_index=1)

    parts = segment(FakeIngestion([april, may]))

    assert [p.pages for p in parts] == [(0,), (1,)]


def test__merge_accounts__misread_on_one_page__outvoted():
    """Логотип печатается на каждой странице: большинство исправляет скан."""
    accounts = [
        Account(bank='bslxonia Bank', account_last4=None),
        Account(bank='Ixonia Bank', account_last4='4664'),
        Account(bank='Ixonia Bank', account_last4='4664'),
    ]
    merged = merge_accounts(accounts)

    assert merged.bank == 'Ixonia Bank'
    assert merged.account_last4 == '4664'


def test__vote_bank__glued_logo_garbage__votes_for_the_clean_name():
    """Значок логотипа прилипает к первому слову: 'EslIxonia Bank' — голос за 'Ixonia Bank'."""
    assert vote_bank(['EslIxonia Bank', 'Lxonia Bank', 'Ixonia Bank']) == 'Ixonia Bank'
    assert vote_bank(['EslIxonia Bank', 'Lslxonia Bank', 'Ixonia Bank', 'Ixonia Bank', 'Lxonia Bank']) == 'Ixonia Bank'


def test__vote_bank__separate_extra_word__plain_majority():
    """Отдельное слово — не мусор по определению: по одной выписке это не решить."""
    assert vote_bank(['Onla Ixonia Bank', 'Onla Ixonia Bank', 'Ixonia Bank']) == 'Onla Ixonia Bank'
    assert vote_bank([None, None]) is None


def test__merge_accounts__period_taken_from_first_page_that_has_one():
    merged = merge_accounts([Account(), Account(period=Period(start=date(2025, 4, 1), end=date(2025, 4, 30)))])

    assert merged.period is not None


def test__merge_accounts__nothing_recognised__empty_account():
    assert merge_accounts([Account(), Account()]) == Account()


def test__reread__only_suspect_pages__others_kept_as_read():
    """Эскалация адресная: за страницы, сошедшиеся с первого раза, не платят.

    Нетронутая страница берётся из самой выписки, а не с нулевой ступени: если
    предыдущий перечит уже принят, его чтение и есть текущее.
    """
    accepted = [page('принятая стр 0'), page('принятая стр 1', page_index=1)]
    cheap = [page('дешёвая стр 0'), page('дешёвая стр 1', page_index=1)]
    costly = [page('дорогая стр 0'), page('дорогая стр 1', page_index=1)]
    ingestion = FakeIngestion(cheap)
    ingestion._levels = {0: cheap, 1: costly}
    ingestion.lines = lambda index, *, min_level=0: ingestion._levels[min_level][index]
    ingestion.prefetch = lambda pages, *, min_level=0: None

    part = Segment(pages=(0, 1), summary=Summary(), account=Account(), lines=tuple(accepted[0] + accepted[1]))
    result = reread(ingestion, part, level=1, pages={1})

    text = ' '.join(line.text for line in result.lines)
    assert 'принятая стр 0' in text
    assert 'дорогая стр 1' in text
    assert 'дешёвая стр 0' not in text
    assert 'дорогая стр 0' not in text


def test__reread__rung_reads_nothing__page_kept_from_previous_reading():
    """Отказ модели или пустой ответ не стирает страницу: остаётся прежнее чтение."""
    accepted = [page('принятая стр 0'), page('принятая стр 1', page_index=1)]
    costly = [page('дорогая стр 0'), []]
    ingestion = FakeIngestion(accepted)
    ingestion.lines = lambda index, *, min_level=0: costly[index]
    ingestion.prefetch = lambda pages, *, min_level=0: None

    part = Segment(pages=(0, 1), summary=Summary(), account=Account(), lines=tuple(accepted[0] + accepted[1]))
    result = reread(ingestion, part, level=1)

    text = ' '.join(line.text for line in result.lines)
    assert 'дорогая стр 0' in text
    assert 'принятая стр 1' in text


def test__starts_statement__balance_forward_only__false():
    """Одинокий Balance forward не стартует выписку: это продолжение, не начало."""
    assert not starts_statement(page('Balance forward 1,500.00'))


def test__starts_statement__totals_without_opening_balance__false():
    """Постраничный колонтитул с остатком на конец и оборотом — не начало выписки.

    Замер по подшивке Ixonia: ровно такая пара итогов встречается внутри каждой
    из десяти выписок, и по критерию «два итога из четырёх» документ распадался
    на девятнадцать периодов, из которых девять оставались без эталона.
    """
    assert not starts_statement(page('Ending Balance $50.00', 'Deposits Total $30.00'))


def test__starts_statement__full_summary__true():
    """Четыре итога — полный блок сводки."""
    assert starts_statement(
        page(
            'Beginning Balance as of 04/01/2025 $100.00',
            'Deposits Total $50.00',
            'Withdrawals Total $30.00',
            'Ending Balance as of 04/30/2025 $120.00',
        )
    )


def test__segment__cover_page_then_summary__header_from_summary_page():
    """Шапка берётся со страницы с валидной сводкой, не с первой страницы."""
    pages = [
        page('COVER PAGE'),  # пустая страница
        page(
            'Beginning Balance as of 04/01/2025 $100.00',
            'Deposits Total $50.00',
            'Withdrawals Total $30.00',
            'Ending Balance as of 04/30/2025 $120.00',
            page_index=1,
        ),
    ]
    parts = segment(FakeIngestion(pages))

    assert len(parts) == 1
    assert parts[0].summary.beginning_balance == Decimal('100.00')
    assert parts[0].summary.self_check() == 0


def test__merge_accounts__period_voted_by_majority():
    """Period выбирается голосованием: большинство перевешивает первую страницу."""
    april = Period(start=date(2025, 4, 1), end=date(2025, 4, 30))
    may = Period(start=date(2025, 5, 1), end=date(2025, 5, 31))
    accounts = [
        Account(period=april),  # первая, но неправильная
        Account(period=may),
        Account(period=may),  # большинство
    ]
    merged = merge_accounts(accounts)

    assert merged.period == may


def test__reread__header_from_best_page():
    """При эскалации шапка берётся со страницы с валидной сводкой."""
    cheap = [
        page('COVER PAGE'),  # пустая
        page(
            'Beginning Balance as of 04/01/2025 $100.00',
            'Deposits Total $50.00',
            'Withdrawals Total $30.00',
            'Ending Balance as of 04/30/2025 $120.00',
            page_index=1,
        ),
    ]
    costly = [
        page('Re-read cover', page_index=0),  # переочитано пусто
        page(
            'Re-read summary: Beginning Balance as of 04/01/2025 $100.00',
            'Deposits Total $51.00',  # немного отличается
            'Withdrawals Total $29.00',
            'Ending Balance as of 04/30/2025 $122.00',
            page_index=1,
        ),
    ]
    ingestion = FakeIngestion(cheap)
    ingestion._levels = {0: cheap, 1: costly}
    ingestion.lines = lambda index, *, min_level=0: ingestion._levels[min_level][index]
    ingestion.prefetch = lambda pages, *, min_level=0: None

    part = Segment(pages=(0, 1), summary=Summary(), account=Account(), lines=())
    result = reread(ingestion, part, level=1, pages={1})

    # Шапка должна быть из более лучшей страницы (с self_check)
    assert result.summary.beginning_balance == Decimal('100.00')


def test__reread__bank_known_before__single_page_garbage_does_not_replace_it():
    """Имя, прошедшее голосование по всем страницам, весомее имени с одной перечитанной страницы."""
    accepted = [page('Ixonia Bank Statement', 'Beginning Balance as of 06/01/2024 $100.00')]
    costly = [page('lxonia ia Ban Bank soo numeer', 'Beginning Balance as of 06/01/2024 $100.00')]
    ingestion = FakeIngestion(accepted)
    ingestion.lines = lambda index, *, min_level=0: costly[index]
    ingestion.prefetch = lambda pages, *, min_level=0: None

    part = Segment(pages=(0,), summary=Summary(), account=Account(bank='Ixonia Bank'), lines=tuple(accepted[0]))
    result = reread(ingestion, part, level=2)

    assert result.account.bank == 'Ixonia Bank'


def test__reread__bank_unknown_before__new_reading_fills_it():
    accepted = [page('Beginning Balance as of 06/01/2024 $100.00')]
    costly = [page('Ixonia Bank', 'Beginning Balance as of 06/01/2024 $100.00')]
    ingestion = FakeIngestion(accepted)
    ingestion.lines = lambda index, *, min_level=0: costly[index]
    ingestion.prefetch = lambda pages, *, min_level=0: None

    part = Segment(pages=(0,), summary=Summary(), account=Account(), lines=tuple(accepted[0]))
    result = reread(ingestion, part, level=2)

    assert result.account.bank == 'Ixonia Bank'
