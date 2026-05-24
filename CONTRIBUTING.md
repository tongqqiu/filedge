# Contributing to Filedge

Thanks for your interest in contributing! This guide covers how to set up a
development environment, run the test suite, and submit a change.

## Development setup

Filedge uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management. Install uv first, then:

```bash
git clone https://github.com/tongqqiu/filedge.git
cd filedge
uv sync --extra dev
```

For connector-specific work, add the matching extra:

```bash
uv sync --extra dev --extra postgres   # Postgres connector
uv sync --extra dev --extra bigquery   # BigQuery connector
uv sync --extra dev --extra databricks # Databricks connector
uv sync --extra dev --extra duckdb     # DuckDB / Parquet
```

## Running checks locally

Before opening a PR, run the same checks CI does:

```bash
uv run ruff check .                              # lint
uv run pytest --cov=filedge --cov-report=term-missing  # tests + coverage
```

The Postgres test suite requires a running Postgres instance. Either start one
locally and export `DATABASE_URL`, or rely on CI to cover that path.

```bash
export DATABASE_URL=postgresql://postgres:etl@localhost/etldb
```

Live BigQuery / Databricks integration tests are opt-in via env flags — see
[README.md](README.md#bigquery) for the variables.

## Submitting a change

1. **Open an issue first** for anything beyond a small fix. It's faster to align
   on approach in an issue than to redo a PR.
2. **Branch from `main`** — name branches like `fix/...`, `feat/...`,
   `docs/...`, `chore/...`.
3. **Write or update tests** alongside the change. We expect new code paths to
   have coverage; the suite runs `--cov` in CI.
4. **Keep PRs focused.** One concern per PR. Mechanical refactors should be
   separate from behavioural changes.
5. **Update docs.** If you change CLI flags, config keys, or connector
   behaviour, update the relevant page in `docs/` and the README.
6. **Add an ADR** for architectural decisions. See `docs/adr/` for the format —
   each ADR is a short markdown file with Context / Decision / Consequences.
7. **Run the full check suite locally** (lint + pytest) before pushing.

All changes go through pull request review — no direct pushes to `main`.

## Commit messages

Short, imperative subject lines (`Add ...`, `Fix ...`, `Refactor ...`), wrapped
at ~72 chars. The body explains *why*, not *what* — the diff already shows the
what.

## Code style

- Python 3.11+. Type hints on public functions.
- `ruff` for lint and format. Default ruleset; do not disable rules without
  justification.
- Prefer explicit over clever. This is a reliability-focused codebase — clarity
  beats brevity.
- No new top-level dependencies without discussion in an issue. Optional
  features go behind an extra in `pyproject.toml`.

## Reporting bugs

Use the bug issue template. Include:

- Filedge version / commit SHA
- Python version
- Connector type (sqlite / postgres / bigquery / databricks)
- Minimal `pipeline.yaml` and sample input that reproduces the issue
- Full traceback or audit-DB state if relevant

## Security issues

**Do not file security issues in the public tracker.** See
[SECURITY.md](SECURITY.md) for the private reporting process.

## License

By contributing, you agree that your contributions will be licensed under the
Apache License 2.0 — the same license as the rest of the project.
