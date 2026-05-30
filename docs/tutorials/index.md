# Tutorials

Start here if you want to *see* Filedge work end to end before wiring it into
your own data. Each tutorial is a self-contained walkthrough you can run on a
laptop in a few minutes — no warehouse, no credentials.

| Tutorial | What it shows | You'll end with |
|----------|---------------|-----------------|
| [EDGAR API to SQLite](../guides/edgar-demo.md) | An API pull becomes the same audited File contract as every other ingestion path | Loaded rows in SQLite plus a full audit trail and lineage you can query |
| [Crash-safe retry](../guides/crash-retry.md) | A pipeline that dies mid-load recovers without double-writing or corruption | A re-run that reclaims the failed file and commits exactly once |

Once a tutorial clicks, move on to the [How-to guides](../guides/index.md) for
task-focused recipes, or the [Reference](../reference/pipeline-yaml.md) for every
config option.
