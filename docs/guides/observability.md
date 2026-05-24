# Observability

Filedge ships two observability layers that compose:

- **Tier 1 (built in)** — structured JSON logs on stderr, machine-readable Run summary on stdout, `run_id` correlation across both and the Audit DB. See [Run a pipeline](run.md).
- **Tier 2 (opt-in)** — OpenTelemetry tracing for Runs and per-File spans, exported to any OTLP-compatible backend (Jaeger, Tempo, Grafana Cloud, Datadog, etc.). This page covers Tier 2.

## Install

OTel ships as an optional extra so the base install stays lean. Users who don't enable tracing never import the SDK and pay zero cost.

```bash
pip install 'filedge[otel]'
```

## Enable

Off by default. Enable with either:

```bash
filedge run --otel-traces …
```

or via environment:

```bash
export FILEDGE_OTEL_TRACES=true
filedge run …
```

The CLI flag wins when both are set.

## Configure the exporter

Filedge respects the standard OpenTelemetry environment variables — no filedge-specific endpoint config:

| Env var | Purpose | Default |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector URL | `http://localhost:4317` (gRPC) |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` or `http/protobuf` | `grpc` |
| `OTEL_SERVICE_NAME` | Service name on emitted spans | `filedge` |

Example — send spans to a local Jaeger collector over gRPC:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export FILEDGE_OTEL_TRACES=true
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

When neither `--otel-traces` nor `FILEDGE_OTEL_TRACES=true` is set, `opentelemetry.*` is never imported. The base `pip install filedge` (no `[otel]` extra) works exactly the same — there are no dangling import errors, no startup overhead, and no runtime cost on the hot path.

## Metrics

Filedge also emits OpenTelemetry **metrics** — counters, histograms, and observable gauges that drive dashboards and SLO alerts. Metrics are independent of tracing: enable either, both, or neither.

### Enable

```bash
filedge run --otel-metrics …
```

or env:

```bash
export FILEDGE_OTEL_METRICS=true
```

Same precedence as tracing — CLI flag wins. Honors the standard `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, and `OTEL_METRIC_EXPORT_INTERVAL` env vars.

### Metrics surface

| Name | Type | Unit | Source |
|---|---|---|---|
| `filedge.files.committed` | counter | files | +1 per COMMITTED file |
| `filedge.files.failed` | counter | files | +1 per FAILED file |
| `filedge.bytes.ingested` | counter | bytes (`By`) | sum of bytes for COMMITTED files |
| `filedge.file.processing.duration_seconds` | histogram | s | per-File load duration |
| `filedge.audit.pending_count` | observable gauge | files | sampled from Audit DB at collection time |
| `filedge.audit.stale_processing_count` | observable gauge | files | sampled from Audit DB at collection time |

Every counter/histogram point carries the `filedge.run_id` attribute — group by it in PromQL or your OTel backend to see per-Run throughput, failure rate, or latency.

Observable gauges sample the Audit DB on demand (at OTel collection time), not at Run start. For a short-lived `filedge run` process the final sample is taken on meter shutdown, capturing end-of-Run state. For a long-running collector scraping a sidecar Audit DB, the gauges track backlog continuously.

### Sample dashboards

```promql
# Throughput (committed files/sec, last 5 min)
rate(filedge_files_committed_total[5m])

# Failure rate (per-Run)
sum by (filedge_run_id) (filedge_files_failed_total)
  / sum by (filedge_run_id) (filedge_files_committed_total + filedge_files_failed_total)

# p95 per-File latency (last 1 hour)
histogram_quantile(0.95, sum by (le) (rate(filedge_file_processing_duration_seconds_bucket[1h])))

# Pending backlog SLO ("page if pending > 1000 for 10 min")
filedge_audit_pending_count > 1000
```

## Roadmap

An OTel log bridge (#80) — forward existing JSON logs via OTLP for unified traces/metrics/logs correlation — and a `filedge healthcheck` subcommand (#81) for K8s liveness/readiness probes are tracked as separate issues.
