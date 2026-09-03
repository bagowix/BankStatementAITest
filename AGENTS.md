## Project Overview

`bankstatement` is a bank statement extraction agent: one PDF in, one structured result out —
periods, their totals, and every transaction, each reconciled against the totals printed in the
document itself.

Main responsibilities:

- split a bound PDF into statement periods (a single file may hold nine of them)
- read the statement header: bank, account, period boundaries, printed totals and counts
- extract transactions with page/bbox provenance for every value
- reconcile each period arithmetically and report exactly which ones failed and by what margin
- never emit an unreconciled period as if it were verified

## Tech Stack

- Python 3.12, uv + poe, cyclopts for the CLI (plain annotations, no click layer underneath)
- PyMuPDF for page inventory / text layer / rendering; pytesseract + Tesseract 5 for OCR
- msgspec Structs for the result schema and for JSON; `Decimal` everywhere money is involved
- stdlib logging; the failure text a user sees never carries a server path
- pytest, Ruff, mypy

## Useful commands

- `uv sync` — install dependencies
- `uv run poe` — list all tasks
- `uv run poe extract <pdf>` — extract a single statement, print JSON
- `uv run poe evaluate` — run the eval harness over `samples/` and print the reconciliation table
- `uv run poe tests` — full test suite
- `uv run pytest tests/<path> -s` — single file / directory
- `uv run poe check` — ruff + mypy
- `uv run poe lint` / `uv run poe fmt` — ruff check / ruff format
- `uv run poe hooks` — install pre-commit (`pre-commit` + `commit-msg` stages)

`mypy` type-checks the web and queue code too, so the dev group pulls both extras in: a plain
`uv sync` gives a complete development environment. The extras stay optional for anyone
*installing* the package — that is a different question from developing it.

Tesseract is an external binary (`brew install tesseract`); tests that touch OCR are marked
`ocr` and skipped when it is absent.

CI (`.github/workflows/ci.yml`) runs `ruff format --check`, `ruff check`, `mypy src` and the test
suite with the coverage gate on Python 3.12 and 3.13, with Tesseract installed so the `ocr` tests
actually run there. The sample PDFs are not in the repository (real statements), so `samples` tests
skip in CI; the web stand and the CLI are exercised on the synthetic statement from `conftest.py`.

## Codebase Map

- src/bankstatement/models.py — result schema (`ExtractionResult`, `StatementPeriod`, `Transaction`,
  `Summary`, `Reconciliation`, `Guarantee`, `Source`, `Losses`, `UnplacedRow`); `to_payload()` is the
  only place Decimal becomes float
- src/bankstatement/money.py — money/date token parsing and `reading_variants` (the inverse problem:
  what a token could have been misread from)
- src/bankstatement/recognize/ — recognition behind one interface
    - `base.py` — `Word`, the `Recognizer` protocol, `render_page` (the measured OCR preprocessing)
    - `textlayer.py` — embedded PDF text layer, zero cost
    - `tesseract.py` — local OCR with the configuration chosen by measurement
- src/bankstatement/ingest.py — the document plus its ladder of recognizers; caches by (page, level)
- src/bankstatement/layout.py — words → visual lines → money columns (clustering, not caption matching);
  also re-joins numbers and dates that OCR split apart, by distance rather than by shape
- src/bankstatement/header.py — statement header: totals, account, bank, period; two layouts, and the
  header invariant picks between candidates
- src/bankstatement/segment.py — one PDF may hold many statements; a statement starts where an
  opening balance is declared, and account details are decided by a vote across its pages
- src/bankstatement/rows.py — candidate rows shared by both readings: a line that starts with a date,
  its description and its money tokens; `Hypothesis`, the result shape of a reading
- src/bankstatement/parse.py — the `chain` reading (running balance as an error-correcting code) and
  `hypotheses()`, which lists every reading; this module never chooses between them
- src/bankstatement/sections.py — the `sections` reading: no balance column, the sign comes from the
  block heading
- src/bankstatement/recognize/vision.py — the paid L2 rung: `VisionClient` contract, page budget,
  synthetic layout of the model's line-by-line transcript; `openai_client.py` / `anthropic_client.py`
  are the two SDK implementations
- src/bankstatement/reconcile.py — the oracle: chain / totals / counts, plus delta diagnosis
- src/bankstatement/extract.py — `extract(pdf_path) -> dict`, the public entry point; also where a
  rejected period is escalated to the next rung of the ladder
- src/bankstatement/evaluate.py — the eval harness and the continuity check across statements
- src/bankstatement/report.py — human-readable verdict per period
- src/bankstatement/cli.py — cyclopts CLI (`extract`, `evaluate`); commands stay plain callables
- src/bankstatement/web/ — the stand. `factory.py` builds the app, `providers.py` declares what it
  is made of (dishka, Scope.APP), `settings.py` reads the environment once, `routes.py` takes every
  dependency as a parameter. Nothing is constructed at import time: two apps with different settings
  coexist in one process, and a test swaps a dependency by passing its own container.
  Also here: the job registry (dedup by file digest, bounded concurrency, counters), a token-bucket
  limiter, and a crop of the source page around a row (provenance you can see). No build step, no
  CDN — htmx is vendored.
- src/bankstatement/worker/ — where the work actually runs: `JobStore` (memory or Redis) holds the
  state both sides read, `broker.py` picks the mode from one env var, `tasks.py` is the taskiq task.
  Without `BANKSTATEMENT_REDIS` everything stays in one process and nothing needs installing.
- tests/ — mirrors the package layout, `test_<module>.py`; `conftest.py` builds synthetic pages
- samples/ — local folder for the sample statements (git-ignored: real documents); file names must
  match `evaluation/truth/*.json` for the header column of the eval
- evaluation/latest.json — the harness output; the README table is generated from it, never typed
- docs/ — INTERNALS.md (why each decision), OVERVIEW.md (non-technical)

## Architecture Conventions

- **The oracle decides, nothing else.** `reconcile.py` is the only place that rules a period correct.
  It is deterministic, it never repairs anything, and no other module may declare success. A period
  that does not reconcile is reported as failed with its delta — never silently emitted as good.
- **Recognition is swappable.** Everything above `recognize/` consumes `Word` objects with
  coordinates and must not care which engine produced them. A new engine implements the `Recognizer`
  protocol and changes nothing else.
- **Parse by hypothesis, not by bank.** Layout is resolved by generating candidate interpretations
  (which column is the balance, which block is activity) and letting the arithmetic pick the one that
  reconciles. Never branch on the bank's name, and never hard-code a coordinate: it wins the four
  sample files and loses every unseen one.
- **A transaction is a row that moves the balance.** Where a running-balance column exists, that is
  the definition — it is what excludes Fulton's Check Summary duplicates and the
  `ENDING BALANCE FROM PRIOR STATEMENT` marker rows without naming them.
- **Money is `Decimal`.** No floats in arithmetic, ever. Rounding happens once, via `quantize`, in
  `to_payload()`.
- **Provenance is not optional.** Every extracted value carries `Source(page, bbox, engine,
  confidence)`, including the summary fields (totals, balances, period dates). An audit product cannot
  use a number whose origin it cannot show. A value the parser repaired says so: `Source.repaired`
  plus `Source.printed`, the reading the repair overrode — a corrected figure that looks like a read
  one is the one nobody re-checks. Counts the bank does not print are filled only for a proven period
  and carry `engine='computed'`; an unproven period keeps them `None`.
- **A row that was seen is never dropped.** A candidate row the parser could not place goes into
  `StatementPeriod.losses.unplaced` with its reason and its provenance, and a period holding one
  cannot be reported `clean`. Silence about a row is worse than a visible gap.
- **Guarantees are explicit.** `Guarantee.CHAIN` (every row confirmed by its neighbours) and
  `Guarantee.AGGREGATE` (only the totals match) are different claims and must not be merged into one
  "extracted successfully".
- **Measured constants carry their measurement.** The OCR preprocessing parameters in
  `render_page` were chosen by experiment; if you change them, re-run the eval harness and update
  the numbers in README.md.
- **Logging.** Stdlib logging: `logger = logging.getLogger(__name__)` at module level, lazy `%s` args
  (`logger.info('... %s', value)`) — Ruff's `G` rules reject f-strings in log calls.
- **Errors.** Domain errors are named `<Something>Error` and subclass `BankStatementError`.

## Rules For Agents

- Prefer minimal, targeted changes; preserve existing architecture and naming patterns.
- Do not rewrite unrelated files.
- Do not change the public contract of `extract(pdf_path) -> dict` unless the task requires it.
- When touching extraction logic, add or update tests, and re-run `uv run poe evaluate`.
- Never tune a heuristic to a specific sample file. If a change only helps one document, it is a bug.
- New dependencies go through `uv add`, never by hand-editing `pyproject.toml` / `uv.lock`.
- Commit messages follow Conventional Commits: `feat(recognize): описание`.
  Russian in commit messages and code comments is normal here.
- Always validate with `uv run ruff check <filename>` and `uv run poe mypy` before committing.
- When unsure: read the nearby module and its tests, and state your assumptions in the answer.

## Testing Guidance

- Place a test at its module's mirror path under `tests/`, in a file named `test_<module>.py`.
- **Names separate semantic parts with a double underscore:**
  `test__<что тестируем>__<сценарий>[__<ожидаемый результат>]`, e.g.
  `test__parse_money__accounting_parentheses__negative`,
  `test__reconcile__duplicated_row__diagnosed`,
  `test__group_lines__mixed_font_sizes__one_line_per_row`. Single underscores stay inside a part,
  `__` marks the boundary between parts.
- Prefer plain test functions; group into a class only when the module already does.
- Tests that need the Tesseract binary or a sample PDF are marked `@pytest.mark.ocr` /
  `@pytest.mark.samples` and skip cleanly when the dependency is missing — the unit suite must run
  on a bare checkout.
- Every reconciliation rule gets a test built from a real document trap, not from an invented case:
  Check Summary duplication, `$` misread as `S`, a swallowed decimal point, a December/January
  year boundary. The traps are the specification.
- Test data: build `Transaction` objects through a local helper rather than repeating kwargs.
- Coverage gate is 95% (`tool.coverage.report.fail_under`); use `-s` when debugging a single test.
  The gate holds without the sample PDFs (about 96% on a bare clone, 98% with them): tests of the
  stand and the CLI run on the synthetic statement from `conftest.py`, not on real documents.

## Eval Harness

`uv run poe evaluate` is the project's real regression test: it runs extraction over every sample and
prints, per period, whether it reconciled, the delta, the guarantee class and the engine cost, plus
the exact-match score of the header against `evaluation/truth/<file>.json` (hand-verified from the
page image, one file per sample; a field the bank does not print is `null` and not scored). A
change that improves one file and breaks another is visible there immediately, and its output is what
README.md's accuracy table must be regenerated from — never edit those numbers by hand:
`uv run python -m bankstatement.cli evaluate <folder> --readme README.md --json-out evaluation/latest.json`.

Validate generalisation leave-one-out: tune on three documents, measure on the fourth. Anything that
needs the fourth document to be in view while tuning is overfitting, and the grading explicitly
measures unseen statements.

## Style Notes

- Follow Ruff and mypy constraints from pyproject.toml (line length 120, `py312` target)
- Ruff formatter uses single quotes
- Keep type hints strict, do not use `Any` or `object`
- `typing.Optional/Union/List/Dict/Set/Type` are banned — use `X | None`, `X | Y`, `list[]`, `dict[]`,
  `set[]`, `type[]`
- mypy is strict: fix errors, don't suppress them
- Avoid using getattr, setattr or hasattr, prefer direct attribute access
- Comments explain why, never what: a comment that restates the code is deleted on sight
