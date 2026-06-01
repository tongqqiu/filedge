# Deploy Filedge

`filedge run` is not a daemon — it scans, commits, and exits, so deploying
Filedge means **scheduling a container**. This guide gives a reference image and
two ways to run it: a local `docker compose` stack you can start in one command,
and the Kubernetes CronJob pattern for production.

The reference deployment lives in [`deploy/`](https://github.com/tongqqiu/filedge/tree/main/deploy)
and runs the open EDGAR → SQLite path, so it works with **zero credentials**.

## The container image

[`deploy/Dockerfile`](https://github.com/tongqqiu/filedge/blob/main/deploy/Dockerfile)
builds `filedge`, `filedge-fetch`, and `filedge-materialize` into a slim image.
The core build carries only the file-ingestion path (SQLite); select
destination/source extras at build time:

```bash
# Core (SQLite)
docker build -f deploy/Dockerfile -t filedge:local .

# With a warehouse / cloud staging
docker build -f deploy/Dockerfile --build-arg EXTRAS="[snowflake]" -t filedge:snowflake .
docker build -f deploy/Dockerfile --build-arg EXTRAS="[bigquery,s3]" -t filedge .
```

Two things the image bakes in that a hand-rolled one usually gets wrong:

- **It runs as a non-root user** (`uid 10001`). Mounted volumes must be writable
  by that user — the image pre-creates and owns `/data` so an *empty* volume
  (a Docker named volume, a k8s `emptyDir`, or a fresh PVC) inherits writable
  ownership. If you mount a pre-populated, root-owned volume instead, set its
  ownership (e.g. a k8s `fsGroup: 10001`).
- **`/data` is the working volume** for staging, the Watched Directory, cursor
  state, and the Audit/Destination SQLite files.

## Local: docker compose

[`deploy/docker-compose.yml`](https://github.com/tongqqiu/filedge/blob/main/deploy/docker-compose.yml)
runs the **two-job pattern** (ADR-0005, ADR-0018): one service materializes
complete Files upstream (`filedge-fetch`), a separate service ingests them
(`filedge run`). Both share a `/data` volume and loop on an interval to stand in
for a scheduler.

```bash
docker compose -f deploy/docker-compose.yml up --build
```

You'll see the fetcher promote a complete NDJSON File and the runner commit it on
its next cycle. Inspect the audit trail in the running container:

```bash
docker compose -f deploy/docker-compose.yml exec run \
  filedge status --audit-db-url sqlite:////data/audit.db
```

`FETCH_INTERVAL` and `RUN_INTERVAL` (seconds) tune the loop cadence.

## Production: Kubernetes CronJobs

In production, schedule **two independent CronJobs** — fetch and run — sharing a
`ReadWriteMany` PVC mounted at `/data`. Keeping them separate lets you scale,
schedule, and monitor upstream and ingestion independently.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: filedge-fetch-apple-revenues
spec:
  schedule: "0 * * * *"          # hourly
  concurrencyPolicy: Forbid       # don't overlap a slow fetch with the next
  jobTemplate:
    spec:
      template:
        spec:
          securityContext:
            runAsUser: 10001
            fsGroup: 10001          # make the PVC writable by the non-root user
          restartPolicy: OnFailure
          containers:
            - name: filedge-fetch
              image: your-registry/filedge:latest
              command: ["filedge-fetch", "--config", "/config/sources.yaml",
                        "--source", "apple-revenues"]
              volumeMounts:
                - { name: data, mountPath: /data }
                - { name: config, mountPath: /config, readOnly: true }
          volumes:
            - { name: data, persistentVolumeClaim: { claimName: filedge-data } }
            - { name: config, configMap: { name: filedge-config } }
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: filedge-run
spec:
  schedule: "*/15 * * * *"        # every 15 min
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          securityContext:
            runAsUser: 10001
            fsGroup: 10001
          restartPolicy: OnFailure
          containers:
            - name: filedge-run
              image: your-registry/filedge:latest
              command: ["filedge", "run", "--dir", "/data/landing",
                        "--config", "/config/pipeline.yaml", "--no-progress"]
              env:
                - name: FILEDGE_AUDIT_DB_URL
                  valueFrom:
                    secretKeyRef: { name: filedge-secrets, key: audit-db-url }
              volumeMounts:
                - { name: data, mountPath: /data }
                - { name: config, mountPath: /config, readOnly: true }
          volumes:
            - { name: data, persistentVolumeClaim: { claimName: filedge-data } }
            - { name: config, configMap: { name: filedge-config } }
```

Schedulers should key off the **exit code** (`0` = clean, non-zero = failures);
see [Run a pipeline](run.md#scheduling).

!!! note "Ensure the Watched Directory exists on cold start"
    `filedge run --dir` requires the directory to exist. On a brand-new volume
    the runner can fire before the first fetch has created it. The compose
    reference runs `mkdir -p /data/landing` before its loop; in Kubernetes,
    create the path in an init step or seed it on the PVC.

## Credentials

Credentials are always supplied at runtime — never baked into the image or
`pipeline.yaml`. Mount them as environment variables from a Secret:
`SNOWFLAKE_PRIVATE_KEY_PATH`, `DATABASE_URL`, `GOOGLE_APPLICATION_CREDENTIALS`,
`DATABRICKS_TOKEN`, etc. See [Connectors](../reference/connectors.md).

## Related

- [Run a pipeline](run.md) — exit codes, write modes, scheduling
- [Scale ingestion](scale.md) — large files, many files, parallel workers
- [API sources](api-sources.md) — the Fetcher half of the two-job pattern
- [Connectors](../reference/connectors.md) — destination config and credentials
