# ADR-0019: Dead-Letter Quarantine is an Opt-In Partial Commit with a Failure Threshold

**Status:** Accepted

## Context

ADR-0003 made whole-File failure the validation policy: if any row fails schema validation, the entire File fails and nothing is committed. That guarantee is load-bearing — a `FAILED` File unambiguously means *nothing landed*, retry is safe, and a `COMMITTED` File landed everything. It is exactly right for a broken schema or a truncated file.

But for a partner File where a handful of rows out of thousands are malformed, whole-File failure is operationally painful: good data is held hostage to a few bad records, and the operator's only options are to hand-edit the partner's file or build a pre-cleaning step outside Filedge. ADR-0003 itself anticipated this and named the resolution as future work: "A dead-letter quarantine for bad rows is a future addition, not a default — adding it later does not require changing the strict-mode guarantee for well-formed files."

The open question: how to commit the good rows and set the bad rows aside *without* losing the completeness reasoning Strict Mode provides — i.e. without making a partial commit look like a clean one.

## Decision

Dead-Letter Quarantine is an **opt-in, per-Pipeline** policy, off by default. When disabled (the default, and the state of every existing Pipeline), behavior is unchanged: the first bad row fails the whole File (Strict Mode, ADR-0003).

When enabled on a Pipeline, rows that fail Transform or Field Encryption are not fatal:

- **Good rows commit** to the Destination as usual.
- **Bad rows are quarantined** — each written (with its row number, offending column, error, and raw row) to an NDJSON **quarantine sidecar** File in a configured location, for inspection and re-drop after a fix. Quarantined rows never reach the Destination.
- The File's Audit Record stays in the terminal **`COMMITTED`** state but records both `committed_row_count` and a new `quarantined_row_count`. `committed + quarantined = total`, so the partial is explicit and accounted for, never silent. No new state is introduced; Content Hash dedup and "terminal, not re-admitted" semantics are unchanged.

Quarantine is gated by a **failure threshold** (a maximum invalid fraction and/or invalid row count). Even with quarantine enabled, a File whose bad rows exceed the threshold **fails wholesale** — nothing committed, no sidecar left behind — exactly like Strict Mode. A few stragglers quarantine; a systemically bad File (broken schema, wrong format) still fails loudly and is retried as one unit.

The threshold is enforced **inside the existing commit-at-end stream** (ADR-0001): rows stream through Transform into the Connector, which commits only when the stream completes. The quarantining processor routes bad rows to the sink and continues; at end-of-stream, if the threshold is exceeded it **raises**, which propagates out of the Connector write *before* commit and rolls the whole File back. No new transaction semantics are added.

## Considered Options

- **Opt-in + failure threshold (chosen).** Preserves ADR-0003 as the default and for well-formed Files; partial commits happen only where explicitly enabled, are always counted on the Audit Record, are recoverable from the sidecar, and a grossly-bad File still fails loudly. "A FAILED File means nothing landed" stays true; "a COMMITTED File landed everything" becomes "landed `committed_row_count`, quarantined `quarantined_row_count`, summing to the File's rows."
- **Opt-in, no threshold.** Simpler, but a 90%-bad File would "succeed" with a huge quarantine — a weak completeness signal that undermines the reason Strict Mode exists.
- **Lenient mode as a global default.** Rejected by ADR-0003 and not reconsidered here: silent partial commits make destination completeness unreasonable.
- **Destination quarantine table instead of a sidecar.** More queryable in-warehouse, but couples quarantine to each Connector's schema and write path and complicates the one-File-one-table model. The sidecar is Destination-agnostic and re-droppable through the normal ingestion path; a Destination table can be layered later without changing this decision.
- **A new terminal Audit state (`COMMITTED_WITH_QUARANTINE`).** Clearer at a glance, but touches the state machine and everything that reasons about states. Recording a count on the existing `COMMITTED` state carries the same information with no state-machine churn.

## Consequences

- Operators can let good partner data land while bad rows are set aside for inspection — without losing audit-grade completeness reasoning.
- The default is unchanged; Strict Mode remains the guarantee for every Pipeline that does not opt in.
- Quarantine catches the row-level failures Strict Mode catches (type coercion, required/empty, Field Encryption). Whole-File structural failures (a missing declared column, an unreadable File, a Connector/Destination error, a CDC tie) remain whole-File failures and are not quarantined.
- Fixing quarantined rows is a normal file drop: a corrected sidecar re-dropped under a new Content Hash ingests on the same audited path. No automatic replay loop is built.
