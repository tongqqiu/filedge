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


def should_enable_logs(
    cli_flag: Optional[bool] = None,
    env_value: Optional[str] = None,
) -> bool:
    """Resolve the OTel log bridge decision with the same precedence as traces."""
    return should_enable_tracing(cli_flag=cli_flag, env_value=env_value)


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


def configure_otel_logs(
    enabled: bool,
    service_name: str = "filedge",
    log_exporter=None,
) -> None:
    """Attach an OTel log handler to the `filedge` logger when enabled.

    The existing stderr handler remains installed by `filedge.log.configure_logging`;
    this bridge is purely additive and imports the OTel SDK only when enabled.
    """
    if not enabled:
        return

    import logging
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import (
        BatchLogRecordProcessor,
        SimpleLogRecordProcessor,
    )
    from opentelemetry.sdk.resources import Resource

    effective_service = os.environ.get("OTEL_SERVICE_NAME", service_name)
    resource = Resource.create({"service.name": effective_service})
    provider = LoggerProvider(resource=resource)

    exporter = log_exporter or _make_otlp_log_exporter()
    processor_cls = SimpleLogRecordProcessor if log_exporter is not None else BatchLogRecordProcessor
    provider.add_log_record_processor(processor_cls(exporter))

    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    handler._filedge_otel_handler = True

    logger = logging.getLogger("filedge")
    logger.handlers = [
        existing for existing in logger.handlers
        if not getattr(existing, "_filedge_otel_handler", False)
    ]
    logger.addHandler(handler)


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


def _make_otlp_log_exporter():
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower()
    if protocol.startswith("http"):
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter,
        )
        return OTLPLogExporter()
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter,
    )
    return OTLPLogExporter()
