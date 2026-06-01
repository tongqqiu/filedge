# Tutorials

Start here if you want to *see* Filedge work end to end before wiring it into
your own data. Each tutorial is a self-contained walkthrough you can run on a
laptop in a few minutes.

| Tutorial | What it shows | You'll end with |
|----------|---------------|-----------------|
| [Stripe API to DuckDB](../guides/stripe-duckdb-demo.md) | A fintech-style API pull becomes an audited File load with Source Manifest, lineage, and Audit Export | Loaded rows in DuckDB plus an audit site you can hand to a reviewer |
| [EDGAR API to SQLite](../guides/edgar-demo.md) | An API pull becomes the same audited File contract as every other ingestion path | Loaded rows in SQLite plus a full audit trail and lineage you can query |
| [Crash-safe retry](../guides/crash-retry.md) | A pipeline that dies mid-load recovers without double-writing or corruption | A re-run that reclaims the failed file and commits exactly once |

Once a tutorial clicks, move on to the [How-to guides](../guides/index.md) for
task-focused recipes, or the [Reference](../reference/pipeline-yaml.md) for every
config option.
