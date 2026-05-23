# SFTP is out of scope — use a dedicated sync layer

SFTP is not supported as a Watched Directory source. Operators who receive files via SFTP should use a dedicated sync tool (rclone, lftp, AWS Transfer Family, or a custom script) to land files in a local directory or cloud bucket (GCS, S3), then point the Watched Directory at that landing zone.

Two reasons drove this decision:

**Partial-transfer race.** Unlike GCS and S3 (where object writes are atomic), SFTP has no atomicity guarantee. If the ETL polls while a partner is mid-upload, it reads a partial file, hashes it, and ingests corrupt data. A sync layer can detect transfer completion (file-size stability, `.part`-file rename pattern) before moving files to the landing zone; the ETL cannot.

**Acknowledgment complexity.** Many enterprise SFTP workflows require the receiver to explicitly acknowledge receipt — moving the file to a `processed/` folder or deleting it — so the sender knows it landed. Acknowledgment is a source-file-management responsibility, not an ingestion responsibility. Adding it to the pipeline couples the two concerns and has no clean home in the current architecture (the Connector abstraction covers the destination, not the source).

The alternative — supporting `sftp://` as a File Source via fsspec/sshfs — is technically straightforward but ignores both problems above. A separate sync process handles them cleanly without touching pipeline code.

**Recommended pattern:** run rclone as a dedicated Cloud Run job or Lambda function on its own schedule. It syncs from the SFTP server to a cloud bucket (S3 or GCS), handling partial-transfer detection via `--min-age` and optional acknowledgment via `--sftp-ask-password` / move-on-success. A second Cloud Run job / Lambda runs `filedge run` with `--watched-dir` pointed at that bucket. The two jobs are scheduled, scaled, and monitored independently — a slow SFTP partner only affects the sync step.

```
Cloud Scheduler
    ├── rclone job (Cloud Run / Lambda)
    │       rclone move sftp-partner:/inbox/ s3:bucket/landing/ --min-age 30s
    │
    └── etl run job (Cloud Run / Lambda)
            etl run --watched-dir s3://bucket/landing/ --audit-db-url $ETL_DB_URL
```
