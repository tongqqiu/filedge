# ADR-0015: Control and Audit Platform Starts with Local Pipeline Authoring

**Status:** Accepted

Filedge may grow from a CLI into a Control and Audit Platform, but the first UI surface is local Pipeline Authoring rather than a hosted, read-write operations platform. The Authoring UI helps operators create and review Pipeline Configs using preview, Schema Inference, Authoring Validation, connector settings, and Credential Placeholders; it does not run ingestion, store secrets, mutate Audit Records, requeue files, or become a second control plane. The Pipeline Registry is created with the first authored Pipeline and points to independent Pipeline Configs, Watched Directories, Audit DB connection placeholders, and Audit Export destinations, preserving the existing rule that one Audit DB maps to exactly one Pipeline.

## Considered Options

**Hosted read-write platform.** Rejected because it would pull authentication, secret storage, scheduler ownership, and state-changing browser operations into Filedge, conflicting with the short-lived Run model and the Operator CLI as the stable state-changing interface.

**Read-only visibility UI first.** Rejected as the first platform step because it overlaps with Audit Export, which is already deliberately static and read-only for compliance stakeholders.

**Shared multi-pipeline Audit DB.** Rejected because the current Audit DB has pipeline-local identity semantics, including a global Content Hash uniqueness constraint. Combining pipelines in one Audit DB would risk cross-pipeline deduplication and blur audit ownership.
