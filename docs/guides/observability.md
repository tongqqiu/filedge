# Observability

Filedge ships two observability layers that compose:

- **Tier 1 (built in)** — structured JSON logs on stderr, machine-readable Run summary on stdout, `run_id` correlation across both and the Audit DB. See [Run a pipeline](run.md).
- **Tier 2 (opt-in)** — OpenTelemetry tracing for Runs and per-File spans, plus an OpenTelemetry log bridge, exported to any OTLP-compatible backend (Jaeger, Tempo, Grafana Cloud, Datadog, etc.). This page covers Tier 2.

## Install

OTel ships as an optional extra so the base install stays lean. Users who don't enable traces or logs never import the SDK and pay zero cost.

```bash
pip install 'filedge[otel]'
```

## Enable

Tracing is off by default. Enable with either:

```bash
filedge run --otel-traces …
```

or via environment:

```bash
export FILEDGE_OTEL_TRACES=true
filedge run …
```

The CLI flag wins when both are set.

OTel logs are also off by default. Enable the log bridge with either:

```bash
filedge run --otel-logs …
```

or via environment:

```bash
export FILEDGE_OTEL_LOGS=true
filedge run …
```

`--otel-logs` is additive: the existing stderr JSON/text handler remains unchanged, and the same records are also exported through the OTel logs pipeline.

## Configure the exporter

Filedge respects the standard OpenTelemetry environment variables — no filedge-specific endpoint config:

| Env var | Purpose | Default |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector URL | `http://localhost:4317` (gRPC) |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` or `http/protobuf` | `grpc` |
| `OTEL_SERVICE_NAME` | Service name on emitted spans and logs | `filedge` |

Example — send spans to a local Jaeger collector over gRPC:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export FILEDGE_OTEL_TRACES=true
export FILEDGE_OTEL_LOGS=true
filedge run --dir ./incoming --config pipeline.yaml
```

## What you get

Each Run emits a parent span; each File processed within that Run emits a child span. Operators can open one Run in a trace viewer and see exactly which Files were processed, in what order, how long each took, and which failed with what error.

### Spans

| Span name | Emitted | Attributes |
|---|---|---|
| `filedge.run` | once per Run | `filedge.run_id`, `filedge.files_scanned`, `filedge.bytes_processed`, `filedge.rows_committed`, `filedge.committed`, `filedge.failed`, `filedge.skipped`, `filedge.new_files`, `filedge.reclaimed`, `filedge.retried`, `filedge.duration_s` |
| `filedge.file` | once per processed File | `filedge.run_id`, `filedge.file_hash`, `filedge.filename`, `filedge.bytes`, `filedge.rows` |

Failed File spans have `Status = ERROR` with the error message in the status description — searchable in any OTel backend.

### Logs

When `--otel-logs` or `FILEDGE_OTEL_LOGS=true` is enabled, every record emitted on the `filedge` logger is exported as an OTel log record. The log body is the event message, severity is mapped from the Python log level, and structured `extra` fields such as `run_id`, `file_hash`, `path`, `rows`, and `error` are preserved as log attributes.

Logs emitted while a Run or File span is active carry the current `trace_id` and `span_id`, so backends can join log lines to traces. For example, in Loki/Grafana, query logs by `{service_name="filedge"} | json | run_id="..."`, then open the attached trace from the log record's trace context.

### Correlation

The `filedge.run_id` attribute on every span matches the `run_id` in stdout JSON summary (Tier 1), in JSON log lines (Tier 1), and in the `run_id` column of the Audit DB. One identifier reconstructs everything that happened in a Run.

## Verify

Spin up an OTel collector locally and verify spans flow:

```bash
docker run --rm -p 4317:4317 jaegertracing/all-in-one:latest

export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
filedge run --dir ./incoming --config pipeline.yaml --otel-traces
```

Open Jaeger at http://localhost:16686, select service `filedge`, and you should see one `filedge.run` span containing one `filedge.file` child per processed File.

## Cost when disabled

When neither OTel flag nor environment variable is set, `opentelemetry.*` is never imported. The base `pip install filedge` (no `[otel]` extra) works exactly the same — there are no dangling import errors, no startup overhead, and no runtime cost on the hot path.

## Roadmap

Tier 2 also tracks OTel metrics (#79). Tier 3 adds `filedge healthcheck` for K8s liveness/readiness probes.

## Related

- [Run a pipeline](run.md) — where logs and metrics come from
- [Healthcheck](healthcheck.md) — liveness/readiness probes
- [Export an audit site](audit-export.md) — share lineage with stakeholders
