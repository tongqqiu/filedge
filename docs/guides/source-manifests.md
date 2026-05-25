# Source Manifests

Filedge's ingestion boundary is the **File**: complete bytes in a Watched Directory, identified by Content Hash and processed through the audit state machine. ADR-0005 keeps SFTP transfer mechanics out of scope, ADR-0006 keeps API fetching out of scope, ADR-0007 keeps queue consumption out of scope.

Source Manifests are an **optional** layer on top of that boundary: a JSON sidecar produced by your Fetcher, sync job, or Queue Materializer that tells Filedge what upstream range a File represents. The manifest is read at pipeline registration, stored on the File's Audit Record, and surfaced through `filedge lineage` and `filedge status --json`.

Filedge does not expand into source mechanics. It expands the audit spine.

See ADR-0011 for the architectural reasoning.

---

## Responsibility boundary

| Concern | Filedge | Upstream tool (Fetcher / Queue Materializer / sync job) |
| --- | --- | --- |
| API auth, pagination, rate limits, incremental cursor management | — | ✅ |
| SFTP transfer completion, partial-transfer detection, partner acknowledgement | — | ✅ |
| Kafka consumer groups, offset commits, rebalances, poison messages | — | ✅ |
| Materialization cadence, retry of failed source pulls | — | ✅ |
| Writing a **complete** data File to the Watched Directory | — | ✅ |
| Writing a Source Manifest sidecar next to the data File | — | ✅ |
| Discovering and validating the Source Manifest | ✅ | — |
| Content Hash deduplication, Strict Mode, retry, commit | ✅ | — |
| Storing manifest metadata on the Audit Record | ✅ | — |
| Exposing manifest metadata through `filedge lineage` and `status --json` | ✅ | — |

Filedge reads the manifest. It does not run an OpenLineage event receiver and does not integrate with Marquez, DataHub, OpenMetadata, or other lineage backends. Per ADR-0011, that asymmetry — consume the OpenLineage shape, do not emit — is deliberate.

---

## Sidecar convention

A Source Manifest is a JSON file placed adjacent to the data File with a `.manifest.json` suffix:

```
landing/
  2026-05-25-stripe-charges.ndjson
  2026-05-25-stripe-charges.ndjson.manifest.json
```

Filedge discovers the manifest at pipeline registration. It does not open or parse the data File to find the manifest. `*.manifest.json` files are excluded from the Watched Directory scan.

---

## Schema (OpenLineage-shaped)

The manifest body is an OpenLineage `RunEvent` JSON object. Filedge reads a defined subset of its fields and stores the full raw payload for source-specific audit details.

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T10:30:00Z",
  "producer": "https://github.com/dlt-hub/dlt",
  "run": {
    "runId": "dlt-run-2026-05-25-1430",
    "facets": {
      "_filedgeManifest": {
        "manifest_version": "1",
        "started_at": "2026-05-25T10:00:00Z",
        "record_count": 1500
      }
    }
  },
  "job": {
    "namespace": "api",
    "name": "stripe.charges"
  },
  "inputs": [
    {
      "name": "https://api.stripe.com/v1/charges",
      "facets": {
        "_sourceRange": {
          "cursor_start": "ch_aaa",
          "cursor_end": "ch_zzz",
          "endpoint": "/v1/charges"
        }
      }
    }
  ],
  "outputs": []
}
```

### Common fields Filedge extracts

| Field | Source in RunEvent | Required | Stored as |
| --- | --- | --- | --- |
| `manifest_version` | `run.facets._filedgeManifest.manifest_version` (default `"1"`) | optional | column |
| `source_type` | `job.namespace` | **required** | column |
| `source_name` | `job.name` | **required** | column |
| `producer` | top-level `producer` URI | optional | column |
| `external_run_id` | `run.runId` | optional | column |
| `started_at` | `run.facets._filedgeManifest.started_at` | optional | column |
| `finished_at` | top-level `eventTime` | optional | column |
| `record_count` | `run.facets._filedgeManifest.record_count` | optional | column |
| `source_range` | first `inputs[].facets._sourceRange` (object) | optional | JSON column |
| **Raw payload** | entire JSON document | always | text column |

Producers can include arbitrary additional facets — they survive in the raw payload column. Filedge does not enforce ownership over the `facets` namespace; following OpenLineage's convention, custom facets should be namespaced with a prefix that identifies your tool (e.g. `_acmeRegulatoryReport`).

---

## Manifest policy

`pipeline.yaml` accepts a `source_manifest:` field:

```yaml
source_manifest: optional  # disabled | optional | required
```

| Mode | Behavior |
| --- | --- |
| `disabled` | Parser is not invoked. No source metadata is attached. |
| `optional` (default) | Parser is invoked. Valid manifests are recorded. Missing or invalid manifests do not fail the File — direct file drops continue to work. |
| `required` | Parser is invoked. Missing or invalid manifests fail the File **before destination write**, with the error category and manifest path captured in the Audit Record. |

Required mode is for regulated pipelines that cannot silently lose audit coverage. Optional mode preserves direct file-drop workflows.

---

## Validation error categories

When a manifest is present but invalid, the parser emits one of five typed error categories:

| Category | When it fires |
| --- | --- |
| `manifest_missing` | No sidecar at the expected path |
| `manifest_malformed_json` | Sidecar exists but is not valid JSON |
| `manifest_unsupported_version` | `manifest_version` is present but Filedge does not support it |
| `manifest_missing_required_field` | `job.namespace` or `job.name` is absent |
| `manifest_invalid_source_range` | `_sourceRange` facet is present but is not a JSON object |

In required mode, the Audit Record's `error_message` carries both the category and the manifest path so the upstream tool owner can find and repair the artifact:

```
manifest_missing: /landing/stripe/2026-05-25-charges.ndjson.manifest.json
```

---

## Inspecting lineage

For a single File, drill in by Content Hash or filename:

```bash
filedge lineage <content-hash> --audit-db-url $FILEDGE_AUDIT_DB_URL
filedge lineage stripe-charges.ndjson --audit-db-url $FILEDGE_AUDIT_DB_URL
```

For machine-readable output (dashboards, scripts):

```bash
filedge lineage <content-hash> --json --audit-db-url $FILEDGE_AUDIT_DB_URL
```

When a filename maps to multiple Content Hashes, `filedge lineage` prints both and exits non-zero — re-run with `--hash <content-hash>` to drill into one.

`filedge status --json` also surfaces a concise source metadata block on each recent-failure entry (`source_type`, `source_name`, `producer`, `external_run_id`), so monitoring systems can route failures to the right upstream owner without a separate audit-DB query.

---

## Examples by source type

### API source (Stripe via dlt)

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T10:30:00Z",
  "producer": "https://github.com/dlt-hub/dlt",
  "run": {
    "runId": "dlt-run-2026-05-25-1430",
    "facets": {
      "_filedgeManifest": {
        "manifest_version": "1",
        "started_at": "2026-05-25T10:00:00Z",
        "record_count": 8421
      }
    }
  },
  "job": {"namespace": "api", "name": "stripe.charges"},
  "inputs": [{
    "name": "https://api.stripe.com/v1/charges",
    "facets": {"_sourceRange": {
      "cursor_start": "ch_3OXa8aLkdIwH...",
      "cursor_end":   "ch_3OYzABKjkdIwH...",
      "endpoint": "/v1/charges"
    }}
  }]
}
```

### Queue source (Kafka via Kafka Connect)

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T10:30:00Z",
  "producer": "https://github.com/apache/kafka-connect",
  "run": {
    "runId": "kc-orders-2026-05-25T10:00",
    "facets": {"_filedgeManifest": {"manifest_version": "1", "record_count": 5000}}
  },
  "job": {"namespace": "queue", "name": "kafka.orders"},
  "inputs": [{
    "name": "kafka://broker/orders",
    "facets": {"_sourceRange": {
      "topic": "orders",
      "partition": 3,
      "start_offset": 1450000,
      "end_offset": 1455000
    }}
  }]
}
```

### SFTP source (rclone)

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T03:15:00Z",
  "producer": "https://rclone.org",
  "run": {"runId": "rclone-acme-2026-05-25T03:00"},
  "job": {"namespace": "sftp", "name": "acme-partner"},
  "inputs": [{
    "name": "sftp://acme.partner.com/inbox/transactions_20260525.csv",
    "facets": {"_sourceRange": {
      "partner": "acme",
      "remote_path": "/inbox/transactions_20260525.csv"
    }}
  }]
}
```

### Vendor export (Salesforce bulk export)

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-05-25T11:00:00Z",
  "producer": "https://salesforce.com",
  "run": {"runId": "sf-export-2026-05-25-Account"},
  "job": {"namespace": "vendor_export", "name": "salesforce.account"},
  "inputs": [{
    "name": "salesforce://Account",
    "facets": {"_sourceRange": {"export_job_id": "750xx0000004C92"}}
  }]
}
```

---

## Related

- [ADR-0011: Source Manifest is an OpenLineage-Shaped Sidecar](../architecture/decisions.md) — architectural reasoning, including why emission is deliberately out of scope
- [ADR-0005, ADR-0006, ADR-0007](../architecture/decisions.md) — the boundary decisions Source Manifests reinforce
- [API sources](api-sources.md) — landing API responses as Files
- [Queue sources](queue-sources.md) — landing queue records as Files
