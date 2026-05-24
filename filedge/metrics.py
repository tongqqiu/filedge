"""OpenTelemetry metrics setup for filedge.

Mirror of `filedge.tracing`: the OTel SDK is an optional extra
(`pip install filedge[otel]`). All `opentelemetry.*` imports live inside the
public functions so the base install never imports them.
"""
import os
from typing import Optional

_TRUE_VALUES = {"true", "1", "yes", "on"}


def should_enable_metrics(
    cli_flag: Optional[bool] = None,
    env_value: Optional[str] = None,
) -> bool:
    """Resolve the metrics-enabled decision (same precedence as tracing).

    CLI flag wins; env var enables when set to true/1/yes/on; default off.
    """
    if cli_flag is not None:
        return bool(cli_flag)
    if env_value is None:
        return False
    return env_value.strip().lower() in _TRUE_VALUES


def configure_metrics(
    enabled: bool,
    audit_db_url: Optional[str] = None,
    stale_minutes: int = 30,
    service_name: str = "filedge",
) -> None:
    """Install a global OpenTelemetry MeterProvider when enabled.

    Honors standard OTel env vars: OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_PROTOCOL, OTEL_SERVICE_NAME, OTEL_METRIC_EXPORT_INTERVAL.

    Also registers the audit-DB backlog gauges if `audit_db_url` is provided.
    """
    if not enabled:
        return
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    effective_service = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({"service.name": effective_service})
    exporter = _make_otlp_metric_exporter()
    reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    if audit_db_url is not None:
        meter = metrics.get_meter("filedge")
        register_audit_gauges(meter, audit_db_url=audit_db_url, stale_minutes=stale_minutes)


def register_audit_gauges(meter, audit_db_url: str, stale_minutes: int = 30) -> None:
    """Register observable gauges that sample the Audit DB on collection.

    Sampling at collection time (not at Run start) lets long-lived collectors
    track backlog between Runs even though `filedge run` is short-lived — the
    final collection on meter shutdown captures the end-of-Run state.
    """
    from opentelemetry.metrics import Observation
    from filedge.db import Database, count_stale_processing, get_status_summary

    def _pending_callback(_options):
        db = Database(audit_db_url)
        try:
            return [Observation(get_status_summary(db).get("PENDING", 0))]
        finally:
            db.close()

    def _stale_callback(_options):
        db = Database(audit_db_url)
        try:
            return [Observation(count_stale_processing(db, stale_minutes=stale_minutes))]
        finally:
            db.close()

    meter.create_observable_gauge(
        name="filedge.audit.pending_count",
        callbacks=[_pending_callback],
        unit="files",
        description="Files currently in PENDING state in the Audit DB.",
    )
    meter.create_observable_gauge(
        name="filedge.audit.stale_processing_count",
        callbacks=[_stale_callback],
        unit="files",
        description="PROCESSING rows whose lock is older than the stale threshold.",
    )


def _make_otlp_metric_exporter():
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()
    if protocol.startswith("http"):
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        return OTLPMetricExporter()
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    return OTLPMetricExporter()
