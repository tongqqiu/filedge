import logging
import sys
import types


def test_configure_otel_logs_uses_deferred_sdk_imports(monkeypatch):
    import filedge.tracing as tracing

    installed = _install_fake_otel_log_sdk(monkeypatch)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "custom-filedge")
    monkeypatch.setattr(tracing, "_make_otlp_log_exporter", lambda: "exporter")

    logger = logging.getLogger("filedge")
    logger.handlers.clear()
    stale_handler = logging.Handler()
    stale_handler._filedge_otel_handler = True
    stderr_handler = logging.Handler()
    logger.addHandler(stderr_handler)
    logger.addHandler(stale_handler)

    tracing.configure_otel_logs(enabled=True)

    assert installed["resource_attrs"] == {"service.name": "custom-filedge"}
    assert installed["provider"].processors[0].exporter == "exporter"
    assert installed["provider"].processors[0].kind == "batch"
    assert logger.handlers[0] is stderr_handler
    assert len(logger.handlers) == 2
    assert getattr(logger.handlers[1], "_filedge_otel_handler", False) is True


def test_configure_otel_logs_with_injected_exporter_uses_simple_processor(monkeypatch):
    import filedge.tracing as tracing

    installed = _install_fake_otel_log_sdk(monkeypatch)
    logging.getLogger("filedge").handlers.clear()

    tracing.configure_otel_logs(enabled=True, log_exporter="in-memory")

    assert installed["provider"].processors[0].exporter == "in-memory"
    assert installed["provider"].processors[0].kind == "simple"


def test_otlp_exporter_factories_choose_protocol_modules(monkeypatch):
    import filedge.tracing as tracing

    _install_fake_otlp_exporters(monkeypatch)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    assert tracing._make_otlp_exporter().protocol == "http-trace"
    assert tracing._make_otlp_log_exporter().protocol == "http-log"

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    assert tracing._make_otlp_exporter().protocol == "grpc-trace"
    assert tracing._make_otlp_log_exporter().protocol == "grpc-log"


def _install_fake_otel_log_sdk(monkeypatch):
    installed = {}

    class FakeResource:
        @staticmethod
        def create(attrs):
            installed["resource_attrs"] = attrs
            return ("resource", attrs)

    class FakeLoggerProvider:
        def __init__(self, resource):
            self.resource = resource
            self.processors = []
            installed["provider"] = self

        def add_log_record_processor(self, processor):
            self.processors.append(processor)

    class FakeLoggingHandler(logging.Handler):
        def __init__(self, level=logging.NOTSET, logger_provider=None):
            super().__init__(level=level)
            self.logger_provider = logger_provider

    class FakeBatchLogRecordProcessor:
        kind = "batch"

        def __init__(self, exporter):
            self.exporter = exporter

    class FakeSimpleLogRecordProcessor:
        kind = "simple"

        def __init__(self, exporter):
            self.exporter = exporter

    _set_module(monkeypatch, "opentelemetry", types.ModuleType("opentelemetry"))
    _set_module(monkeypatch, "opentelemetry.sdk", types.ModuleType("opentelemetry.sdk"))
    _set_module(
        monkeypatch,
        "opentelemetry.sdk._logs",
        types.SimpleNamespace(
            LoggerProvider=FakeLoggerProvider,
            LoggingHandler=FakeLoggingHandler,
        ),
    )
    _set_module(
        monkeypatch,
        "opentelemetry.sdk._logs.export",
        types.SimpleNamespace(
            BatchLogRecordProcessor=FakeBatchLogRecordProcessor,
            SimpleLogRecordProcessor=FakeSimpleLogRecordProcessor,
        ),
    )
    _set_module(
        monkeypatch,
        "opentelemetry.sdk.resources",
        types.SimpleNamespace(Resource=FakeResource),
    )
    return installed


def _install_fake_otlp_exporters(monkeypatch):
    class HttpTraceExporter:
        protocol = "http-trace"

    class GrpcTraceExporter:
        protocol = "grpc-trace"

    class HttpLogExporter:
        protocol = "http-log"

    class GrpcLogExporter:
        protocol = "grpc-log"

    for name in (
        "opentelemetry",
        "opentelemetry.exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.exporter.otlp.proto",
        "opentelemetry.exporter.otlp.proto.http",
        "opentelemetry.exporter.otlp.proto.grpc",
    ):
        _set_module(monkeypatch, name, types.ModuleType(name))

    _set_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        types.SimpleNamespace(OTLPSpanExporter=HttpTraceExporter),
    )
    _set_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
        types.SimpleNamespace(OTLPSpanExporter=GrpcTraceExporter),
    )
    _set_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.http._log_exporter",
        types.SimpleNamespace(OTLPLogExporter=HttpLogExporter),
    )
    _set_module(
        monkeypatch,
        "opentelemetry.exporter.otlp.proto.grpc._log_exporter",
        types.SimpleNamespace(OTLPLogExporter=GrpcLogExporter),
    )


def _set_module(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)
