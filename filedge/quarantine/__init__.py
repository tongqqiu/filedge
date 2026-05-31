"""Dead-Letter Quarantine (ADR-0019): opt-in, threshold-gated partial commits.

When a Pipeline enables quarantine, rows that fail Transform/Field Encryption
are set aside in an NDJSON sidecar instead of failing the whole File — provided
the bad-row count stays under the configured threshold. This package holds the
quarantine-specific pieces:

- ``sink`` — buffer bad rows and flush them to an NDJSON quarantine sidecar
- ``processor`` — threshold-gated row processor that routes bad rows to the sink
- ``redrop`` — unwrap a quarantine sidecar back into a clean, re-droppable NDJSON File
"""
