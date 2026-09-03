# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See AGENTS.md for the full conventions; this file is the short operational summary.

## Project Overview

A bank statement extraction agent, built as a take-home for an AI startup working on agents for
financial audit. One PDF in, one structured result out: periods, totals, and all transactions —
each period reconciled against the totals printed in the document.

The grading is what shapes the architecture:

1. summary fields — exact match
2. transactions — must reconcile with the totals
3. generalisation — same scoring on 2–3 unseen statements

Criterion 2 means the document carries its own oracle: `beginning + Σdeposits − Σwithdrawals = ending`
is printed right there. That oracle is the centre of the design, not a final check.

## Common Commands

Package manager is **uv**; task runner is **poe** (`uv run poe` to list all tasks). There is no Makefile.

```bash
# Install dependencies
uv sync

# External OCR binary (macOS)
brew install tesseract

# Extract one statement
uv run poe extract "samples/Great Lakes Commerce Bank 2025.pdf"

# Eval harness over all samples — the real regression test
uv run poe evaluate

# Web stand and HTTP API (optional dependency group)
uv sync --extra ui
uv run poe ui

# Same thing with a separate worker: queue and shared state move to Redis
docker compose up -d redis
export BANKSTATEMENT_REDIS=redis://localhost:6379
uv run poe worker

# Tests
uv run poe tests
uv run pytest tests/test_reconcile.py -s

# Lint and types
uv run poe check

# Pre-commit hooks: те же проверки плюс формат Conventional Commits
uv run poe hooks
```

## Architecture

**Entry point:** `bankstatement.extract:extract(pdf_path) -> dict`; the CLI and the web stand are
thin wrappers. The stand (`bankstatement.web`) is FastAPI + htmx with no build step and no CDN, and
it shows the same thing the reports do: the verdict first, the provenance one click away.

**Pipeline:**

```
ingest → recognize → layout → segment → header → parse (hypotheses) → reconcile → report
```

**Cost cascade.** Each stage escalates only for pages the oracle rejected, so cost is a function of
document difficulty rather than page count:

| Stage | Engine | Cost | Covers |
|---|---|---|---|
| L0 | PDF text layer | $0 | Great Lakes |
| L1 | Tesseract, measured preprocessing (300 dpi ×2, `--psm 4`) | $0 | Fulton, Renasant, Ixonia |
| L1b | Tesseract, different settings (400 dpi, `--psm 6`) | $0 | periods the oracle rejected |
| L2 | vision model (OpenAI or Anthropic), `recognize/vision.py` | paid | pages of periods the free rungs left unreconciled, within a page budget |

Measured: every sample is read at L0/L1; L1b re-reads only the periods that failed. L2 sits behind
the `VisionClient` contract (one method: page image + prompt → text) and is enabled only by
`BANKSTATEMENT_VISION=openai|anthropic` plus the provider key. On the binder the model re-reads the
same 40 pages of the eight unreconciled statements: gpt-5.4-mini (default) closes 7 of 10 for $0.35,
gpt-5.6-sol 9 of 10 for ≈ $1.8, gpt-5.6-terra 5 of 10, gpt-4o refuses table pages. Several models
(`BANKSTATEMENT_VISION_MODEL=a,b`) are several rungs, each re-reading only what the previous one left:
mini then sol closes 10 of 10 for $0.79. A page a rung cannot read keeps its previous reading; a rung
with no page available is skipped.

**Key abstractions:**

- `Recognizer` protocol (`recognize/base.py`) — the only seam between pixels and logic; produces
  `Word(text, bbox, page, confidence, engine)`
- `reconcile()` (`reconcile.py`) — the oracle; the sole authority on whether a period is correct
- `Guarantee` — `CHAIN` (per-row, running balance) vs `AGGREGATE` (totals only, no balance column)
- `DeltaDiagnosis` — the shape of a discrepancy names its cause (duplicate row, wrong column,
  digit transposition, count-only)
- `reading_variants()` (`money.py`) — the inverse of OCR error, used for targeted repair
- `Source.repaired` / `Source.printed` — a value the parser chose rather than read, and the reading
  it overrode
- `Losses` / `UnplacedRow` (`models.py`) — rows the parser saw but could not place, with reason and
  provenance; `Losses.proven` marks a period whose composition is proven: the chain confirmed every
  row, or both printed totals matched and every unplaced row is explained (class B alone never hides
  a `lost` row)

**Escalation is driven by the verdict, not by the file.** `extract._escalate` re-reads only the
pages of a period the oracle rejected, and only while it stays rejected; a re-read result is
accepted solely when its residual is smaller. That keeps cost proportional to difficulty and makes
the added ladder rung safe to extend with a paid engine later.

## Code Style & Conventions

- **Commit format:** Conventional Commits (`feat(reconcile): описание`)
- **Line length:** 120 characters (Ruff), single quotes
- **Type checking:** mypy strict (`uv run poe mypy`)
- **Language:** Russian in commit messages and code comments is normal here
- **Money:** `Decimal` in all arithmetic; `float` appears only in `to_payload()`
- **Schema:** msgspec Structs, frozen; `msgspec.structs.replace` instead of a copy-with-update method
- **Constants:** module-level constants carry `Final[...]`
- **Docstrings:** Google style (`Args:` / `Returns:`), never NumPy sections
- **No import-time singletons:** the app is built by `create_app(settings, container)`; dependencies
  come from a dishka container, so nothing is constructed as a side effect of an import. The one
  documented exception is `worker/broker.py`: taskiq's CLI takes the broker as `module:name`, so the
  broker and its store live at module level there
- **No `from __future__ import annotations`:** PEP 695 type aliases are lazy on their own, and the
  only forward reference we had is now `Self`

## Non-negotiables

- The oracle is the only thing that may declare a period correct.
- An unreconciled period is reported as failed, with its delta and a diagnosis. Never emit it as good:
  for an audit product a silent wrong number is far worse than an honest gap.
- Never branch on a bank's name and never hard-code a coordinate — that wins four files and loses
  every unseen one, and generalisation is a third of the grade.
- Every value carries its provenance, and a repaired value admits the repair.
- A row the parser saw but could not place is reported, never dropped.

## Testing

Unit tests run on a bare checkout. Tests needing the Tesseract binary or the sample PDFs are marked
`ocr` / `samples` and skip when those are missing. Test names follow
`test__<что>__<сценарий>__<результат>`. Reconciliation rules are tested against real document traps
(Check Summary duplication, `$` read as `S`, swallowed decimal point, December/January boundary,
a section continuing onto the next page, a running header carrying a credit word, an empty parse) —
those traps are the specification. Coverage gate is 95%.
