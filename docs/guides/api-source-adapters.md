# How to add an API Source

The Reference Fetcher keeps API-specific behavior at a small seam. The Fetcher
orchestration (`filedge/fetch/orchestrator.py`) only knows how to:

1. load a Sources Config entry into a `FetchPlan`;
2. read the stored cursor;
3. ask the source client for records for that cursor window;
4. publish one complete File with a Source Manifest under the Fetch Lock;
5. advance the cursor after promotion.

Steps 4 and 5 — the reliability rules — are shared and never change per source.
Adding a new API Source means describing its request/pagination/cursor shape; it
does **not** mean reimplementing staging, manifests, the Fetch Lock, or cursor
advancement.

## What a source owns

- request URL and query parameters;
- request headers and an optional bearer credential (resolved from an env var,
  never stored in the config);
- its pagination shape (or single-document fetch);
- the JSON path its records live under;
- the field the incremental cursor is derived from;
- its Source Manifest range metadata.

It does **not** own writing NDJSON, emitting Source Manifests, holding the Fetch
Lock, promoting into the Watched Directory, or advancing the cursor store.

## The three touch points

A source is expressed entirely through the `FetchPlan` dataclass, so adding one
is a small, predictable change:

1. **`filedge/fetch/sources_config.py`** — add a `_parse_<type>_source(raw)`
   that validates the entry and returns a `FetchPlan`, and dispatch to it in
   `_parse_source`. The plan's `cursor_mode`, `record_path`, `cursor_field`,
   `cursor_param`, and any type-specific fields encode the behavior.
2. **`filedge/fetch/source_client.py`** — only if the API needs a *new
   pagination shape*. The client has three `cursor_mode`s today:
     - `server` — the cursor is a query param and the API returns only newer
       records, paged by page number (the generic HTTP / GitHub default);
     - `client` — one document is fetched and records are filtered by
       `cursor_field` locally (EDGAR);
     - `stripe` — a cursor-paginated list: walk `starting_after` while
       `has_more` is true, taking records from `data`.
   If your API fits an existing mode, you write **no client code** — just set
   `cursor_mode` in the parser. A genuinely new shape adds one `_fetch_<mode>`
   method plus its dispatch line.
3. **`filedge/fetch/orchestrator.py`** — add a branch to `_source_range` if the
   manifest should carry type-specific provenance (EDGAR records CIK/taxonomy;
   Stripe records the resource). Omit it and the generic `cursor_param`/`from`/
   `to` range is used.

## Worked example: the Stripe source

Stripe was added as exactly this change set — a new `stripe` pagination dialect.

The Sources Config entry only needs the resource and the env var holding the
secret key; `api_base` defaults to the live API but can point at a mock:

```yaml
version: 1
sources:
  - name: stripe-charges
    type: stripe
    resource: charges
    credential_env: STRIPE_API_KEY
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    # api_base: http://localhost:12111   # e.g. stripe-mock, for credential-free runs
```

The parser turns that into a `FetchPlan` with `cursor_mode="stripe"`,
`record_path="data"`, `cursor_field="created"`, and `cursor_param="created[gt]"`
(the incremental filter). The client's `_fetch_stripe` walks `starting_after`
across pages until `has_more` is false; `_source_range` records the `resource`.
Nothing in staging, manifest emission, the Fetch Lock, promotion, or cursor
advancement changed.

## Testing without a real account

Source clients are testable without network or credentials, because the HTTP
transport is injectable:

- **Unit-test the fetch shape with a fake transport.** `HttpSourceClient(transport=...)`
  takes a `(url, headers) -> (status, headers, body)` callable. Feed it
  canned, source-shaped JSON and assert the URL/query, headers/bearer, record
  extraction, pagination, and `next_cursor`. This is how the Stripe pagination,
  `created[gt]` incremental filter, and bearer auth are all tested — no Stripe
  account involved.
- **Integration-smoke against a mock when one exists.** Stripe publishes
  [`stripe-mock`](https://github.com/stripe/stripe-mock), a server that replays
  responses from its OpenAPI spec. Point `api_base` at it for a credential-free
  end-to-end run; a real test-mode key stays optional and never required in CI.

What to assert in the unit test:

- the expected URL/query parameters were requested;
- headers (and bearer credential, when configured) were sent;
- records are extracted from the expected JSON path;
- pagination terminates correctly and merges all pages;
- `next_cursor` advances to the largest `cursor_field` value seen;
- `source_range` carries the source-specific metadata.

## Keep the boundary clear

Adding a source does not make Filedge the loader of record. The Reference
Fetcher is still an external companion (ADR-0018): it materializes complete
Files, and `filedge run` ingests those Files through the normal audited path.

## Related

- [API sources](api-sources.md) — the Fetcher pattern and reference companion
- [Source manifests](source-manifests.md) — the provenance every source emits
- [EDGAR demo](edgar-demo.md) — the `client`-mode source end to end
