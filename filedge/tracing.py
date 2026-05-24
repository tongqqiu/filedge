"""OpenTelemetry tracing setup for filedge.

The OTel SDK is an optional extra (`pip install filedge[otel]`). All
`opentelemetry.*` imports inside `configure_tracing` are deferred so the
base install never imports them and pays zero cost when tracing is off.
"""
import os
from typing import Optional

_TRUE_VALUES = {"true", "1", "yes", "on"}


def should_enable_tracing(
    cli_flag: Optional[bool] = None,
    env_value: Optional[str] = None,
) -> bool:
    """Resolve the tracing-enabled decision.

    Precedence:
    - If the CLI flag is set (True or False), it wins.
    - Otherwise, the env var enables when set to one of true/1/yes/on (case-insensitive).
    - Default off.
    """
    if cli_flag is not None:
        return bool(cli_flag)
    if env_value is None:
        return False
    return env_value.strip().lower() in _TRUE_VALUES


def configure_tracing(
    enabled: bool,
    service_name: str = "filedge",
) -> None:
    """Install a global OpenTelemetry TracerProvider when enabled.

    Honors standard OTel env vars: OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_PROTOCOL, OTEL_SERVICE_NAME. No-op when disabled.
    """
    if not enabled:
        return
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    effective_service = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({"service.name": effective_service})
    provider = TracerProvider(resource=resource)

    exporter = _make_otlp_exporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def _make_otlp_exporter():
    """Build an OTLP exporter using the OTEL_EXPORTER_OTLP_PROTOCOL env var.

    Defaults to gRPC, matching the OTel spec default.
    """
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()
    if protocol.startswith("http"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        return OTLPSpanExporter()
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    return OTLPSpanExporter()
