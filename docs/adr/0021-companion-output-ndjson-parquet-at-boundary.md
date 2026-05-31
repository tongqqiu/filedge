# Reference companions materialize NDJSON; Parquet intermediates are a boundary capability, not a companion feature

The Reference Fetcher (`filedge-fetch`) and Reference Queue Materializer (`filedge-materialize`) materialize **NDJSON** Files only (optionally gzip'd). They are not extended to write **Parquet** by default. Parquet intermediates remain fully supported *at the boundary* — `filedge run` reads Parquet natively (ADR-0007 already frames queue sources as "NDJSON or Parquet files"), so any Parquet-writing materializer can land Files in the Watched Directory. The decision recorded here is that the *reference companions* stay NDJSON.

This ADR exists because "fetch only emits JSON — should we add Parquet?" is a recurring question, and the honest answer distinguishes between the companion's output format and the boundary's accepted formats.

## NDJSON is the right output for a thin materializer

Both companions share one staged writer (`filedge.companion.staging.write_staged_ndjson`). NDJSON is Filedge's canonical interchange format, and it fits the companion role for two structural reasons:

**The input is loosely-typed JSON.** APIs return JSON; Kafka payloads decode to JSON objects. NDJSON is schema-on-read: the companion extracts records and writes them without committing to a column schema. The **type contract lives in `pipeline.yaml`** and is applied at `filedge run`. This keeps the companion thin and keeps one source of truth for types.

**Parquet would force a schema into the fetch/materialize layer.** Writing Parquet requires a column schema and types *at materialization time*. The companion would then either infer types independently — duplicating Schema Inference and `pipeline.yaml` — or carry a second schema declaration. Either way introduces **materialize-time vs. ingest-time schema drift**, a real correctness hazard for what is meant to be a transport-thin example. NDJSON sidesteps this entirely.

## The storage argument is already addressed

Parquet's advantages are columnar compression and typed columnar scans. For the companion's output they are marginal:

- The staged File is a short-lived intermediate that `filedge run` reads **once, row-wise**, then ingests. Columnar layout buys almost nothing for a single full read.
- Compression is already available: `gzip: true` produces `.ndjson.gz`, mirroring Compaction's output economics.

The columnar win only becomes real at large volumes with stable upstream schemas — which is exactly the evidence bar, not the default.

## Decision

- The Reference Fetcher and Reference Queue Materializer materialize **NDJSON** (optionally gzip'd). This is unchanged.
- **Parquet at the boundary is already supported and is the recommended path** when Parquet intermediates are genuinely needed: point a Parquet-writing materializer (Kafka Connect S3/GCS Parquet sink, Flink, Spark) at the Watched Directory; `filedge run` ingests the Parquet Files unchanged.
- Adding Parquet *output to the reference companions* is **deferred** to a concrete, volume-driven target-user case (ADR-0012 bar). If built, the companion must derive the Parquet schema from the Pipeline Config columns — never infer a second, independent schema — so materialize-time and ingest-time types cannot drift.

## Considered Options

- **Keep reference companions NDJSON-only; rely on the boundary for Parquet (chosen).** Keeps companions thin, keeps one type contract in `pipeline.yaml`, and still lets operators use Parquet via mature external sinks.
- **Add Parquet output to the reference companions now.** Rejected as a default: pushes schema/typing into the materialize layer, risks drift, and duplicates `pipeline.yaml`'s job for a marginal storage gain already covered by gzip.
- **Make Parquet the canonical companion format.** Rejected: NDJSON is the canonical interchange format; loosely-typed JSON input maps naturally to schema-on-read NDJSON.

## Consequences

- The companions stay simple and demonstrate the reliability contract (stage → manifest → locked promotion → cursor-after-promotion) without a Parquet writer or a schema decision.
- Operators who need Parquet intermediates have a supported, documented path today (external Parquet sink → Watched Directory → `filedge run`).
- A future Parquet-emitting companion has a defined constraint (reuse the Pipeline Config schema) and a defined bar (measured volume case) before it is built.
