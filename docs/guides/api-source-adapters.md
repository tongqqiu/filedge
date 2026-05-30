# How to add an API Source adapter

The Reference Fetcher keeps API-specific behavior behind an API Source adapter.
The Fetcher orchestration only knows how to:

1. load a Sources Config entry into a plan;
2. read the stored cursor;
3. ask the adapter for records;
4. publish one complete File with a Source Manifest;
5. advance the cursor after promotion.

That seam is the extension point. A new API Source should add behavior in one
adapter instead of adding conditionals across the Fetcher client and
orchestrator.

## Adapter responsibilities

An adapter owns:

- request URLs and query parameters;
- request headers and optional bearer credentials;
- pagination or single-document fetch shape;
- record extraction path;
- cursor advancement;
- Source Manifest range metadata.

It should not own:

- writing NDJSON files;
- emitting Source Manifests;
- holding the Fetch Lock;
- promoting into the Watched Directory;
- advancing the cursor store.

Those reliability rules live in the shared companion publish module.

## Files to touch

For a new source type, expect a small change set:

- `filedge/fetch/source_adapters.py` — add the adapter;
- `filedge/fetch/sources_config.py` — parse the source-specific config into the adapter;
- `tests/test_fetch_sources_config.py` — prove config parsing and validation;
- `tests/test_fetch_source_client.py` or a source-specific client test — prove URL/header/cursor behavior with a fake transport;
- `tests/test_fetch_orchestrator.py` — only if the end-to-end manifest or ingest behavior changes.

The Fetcher orchestrator should not need a new `if source_type == ...` branch.

## Tiny generic adapter example

This example is intentionally not a supported source. It shows the shape for an
API that returns one JSON document with records under `data` and needs
client-side cursor filtering by `updated_at`.

```python
from dataclasses import dataclass
from typing import List, Optional

from filedge.fetch.source_adapters import HttpApiSource, dotted_get


@dataclass(frozen=True)
class AcmeEventsSource(HttpApiSource):
    account_id: str = ""

    def fetch_records(self, client, cursor: Optional[str]) -> List[dict]:
        records = client.request_records(
            f"{self.url}/accounts/{self.account_id}/events",
            headers=self.request_headers(),
            record_path="data",
        )
        return [
            record for record in records
            if cursor is None or str(dotted_get(record, self.cursor_field)) > cursor
        ]

    def source_range(
        self, from_cursor: Optional[str], to_cursor: Optional[str]
    ) -> dict:
        return {
            "cursor_param": self.cursor_param,
            "cursor_field": self.cursor_field,
            "from": from_cursor,
            "to": to_cursor,
            "account_id": self.account_id,
        }
```

Then parse the matching Sources Config entry into that adapter:

```python
source=AcmeEventsSource(
    source_name=raw["name"],
    source_type="acme",
    url=raw["url"],
    cursor_param=cursor.get("param", cursor["field"]),
    cursor_field=cursor["field"],
    query=query,
    headers=headers,
    credential_env=raw.get("credential_env"),
    account_id=str(raw["account_id"]),
)
```

## Test the seam

Use a fake transport and assert the adapter-facing behavior:

- expected URL was requested;
- expected headers were sent;
- records are extracted from the expected JSON path;
- cursor filtering includes only records newer than the stored cursor;
- `next_cursor` advances to the largest cursor field in emitted records;
- `source_range` contains the source-specific range metadata.

Avoid network calls in unit tests. A real upstream smoke test can be added later
behind an integration marker when the source is important enough to support.

## Keep the boundary clear

Adding an adapter does not make Filedge the loader of record. The Reference
Fetcher is still an external companion: it materializes complete Files and
`filedge run` ingests those Files through the normal audited path.
