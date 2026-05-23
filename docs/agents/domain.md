# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the canonical domain glossary (File, Content Hash, Commit, Run, Strict Mode, Partial Load Corruption, etc.)
- **`docs/adr/`** — read ADRs that touch the area you're about to work in:
  - `0001-single-transaction-commit.md` — why records and audit marker live in the same DB
  - `0002-content-hash-as-idempotency-key.md` — why SHA-256 hash, not filename, is the identity
  - `0003-strict-mode-validation.md` — why whole-file failure, not partial commit

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   │   ├── 0001-single-transaction-commit.md
│   │   ├── 0002-content-hash-as-idempotency-key.md
│   │   └── 0003-strict-mode-validation.md
│   └── PRD.md
└── etl/          ← Python package (to be created)
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

Key terms to use precisely: **File** (not "file"), **Content Hash** (not "checksum" or "fingerprint"), **Commit** (the ETL operation, not git), **Run** (one execution of `filedge run`), **Strict Mode**, **Partial Load Corruption**, **Watched Directory**, **Pipeline Config**.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (single-transaction commit) — but worth reopening because…_
