import time
from dataclasses import dataclass
from typing import Callable

from filedge.config import PipelineConfig
from filedge.connectors import get_connector
from filedge.db import Database


@dataclass
class HealthCheck:
    name: str
    ok: bool
    error: str | None
    latency_ms: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


class HealthcheckError(RuntimeError):
    def __init__(self, check: HealthCheck):
        self.check = check
        super().__init__(f"Healthcheck failed: {check.name} unreachable: {check.error}")


def check_audit_db(audit_db_url: str) -> HealthCheck:
    db = None
    try:
        def probe() -> None:
            nonlocal db
            db = Database(audit_db_url)
            db.execute("SELECT 1").fetchone()

        return _measure("audit_db", probe)
    finally:
        if db is not None:
            db.close()


def check_destination(config: PipelineConfig) -> HealthCheck:
    connector = None
    try:
        def probe() -> None:
            nonlocal connector
            connector = get_connector(config)
            connector.healthcheck()

        return _measure("destination", probe)
    finally:
        if connector is not None:
            connector.close()


def run_healthchecks(config: PipelineConfig, audit_db_url: str) -> dict:
    checks = [
        check_audit_db(audit_db_url),
        check_destination(config),
    ]
    return {
        "healthy": all(check.ok for check in checks),
        "checks": [check.as_dict() for check in checks],
    }


def assert_healthy(config: PipelineConfig, audit_db_url: str) -> None:
    for check in run_healthchecks(config, audit_db_url)["checks"]:
        if not check["ok"]:
            raise HealthcheckError(
                HealthCheck(
                    name=check["name"],
                    ok=check["ok"],
                    error=check["error"],
                    latency_ms=check["latency_ms"],
                )
            )


def _measure(name: str, probe: Callable[[], None]) -> HealthCheck:
    started = time.perf_counter()
    try:
        probe()
    except Exception as e:
        return HealthCheck(
            name=name,
            ok=False,
            error=str(e) or type(e).__name__,
            latency_ms=_elapsed_ms(started),
        )
    return HealthCheck(
        name=name,
        ok=True,
        error=None,
        latency_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
