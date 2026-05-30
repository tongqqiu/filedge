# Healthcheck

`filedge healthcheck` probes the Audit DB and destination connector without creating tables or writing rows. Use it for Kubernetes liveness/readiness probes, uptime monitors, or a quick operator check before a scheduled Run.

```bash
filedge healthcheck --config pipeline.yaml --audit-db-url sqlite:///filedge.db
```

The command exits `0` when both dependencies are reachable. It exits `1` when any check fails.

`filedge run` performs the same preflight before scanning files. If a dependency is unreachable, the Run exits before audit tables, destination tables, or audit rows are created:

```text
Healthcheck failed: destination unreachable: unable to open database file
```

## JSON Output

Use `--json` for probes and monitors:

```bash
filedge healthcheck --config pipeline.yaml --audit-db-url sqlite:///filedge.db --json
```

The command writes one JSON object to stdout:

```json
{
  "healthy": true,
  "checks": [
    {"name": "audit_db", "ok": true, "error": null, "latency_ms": 1.234},
    {"name": "destination", "ok": true, "error": null, "latency_ms": 2.345}
  ]
}
```

Check names are stable:

| Name | Probe |
|---|---|
| `audit_db` | `SELECT 1` against the Audit DB URL |
| `destination` | Connector-specific read-only round trip, such as `SELECT 1` |

## Kubernetes Example

```yaml
readinessProbe:
  exec:
    command:
      - filedge
      - healthcheck
      - --config
      - /config/pipeline.yaml
      - --audit-db-url
      - "$(FILEDGE_AUDIT_DB_URL)"
      - --json
  initialDelaySeconds: 5
  periodSeconds: 30

livenessProbe:
  exec:
    command:
      - filedge
      - healthcheck
      - --config
      - /config/pipeline.yaml
      - --audit-db-url
      - "$(FILEDGE_AUDIT_DB_URL)"
  initialDelaySeconds: 10
  periodSeconds: 60
```

## Related

- [Run a pipeline](run.md) — the command healthcheck guards
- [Observability](observability.md) — logs, metrics, and tracing
