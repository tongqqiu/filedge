#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

AUDIT_DB_URL="sqlite:///demo/duckdb/out/audit.db"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/demo/duckdb/out/uv-cache}"

section() {
  printf "\n== %s ==\n" "$1"
}

section "Reset"
./demo/duckdb/scripts/reset.sh

section "Validate the contract"
uv run filedge validate demo/duckdb/incoming/orders_001.csv \
  --config demo/duckdb/pipeline.yaml

section "Load good files"
uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url "$AUDIT_DB_URL"

section "Audit status"
uv run filedge status --audit-db-url "$AUDIT_DB_URL"

section "Destination rows"
uv run python demo/duckdb/scripts/show_rows.py

section "Rerun same files to prove idempotency"
uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url "$AUDIT_DB_URL"
uv run filedge status --audit-db-url "$AUDIT_DB_URL"
uv run python demo/duckdb/scripts/show_rows.py

section "Introduce a bad file"
cp demo/duckdb/bad/orders_bad.csv demo/duckdb/incoming/orders_bad.csv

set +e
uv run filedge run \
  --dir demo/duckdb/incoming \
  --config demo/duckdb/pipeline.yaml \
  --audit-db-url "$AUDIT_DB_URL"
RUN_EXIT=$?
set -e

if [ "$RUN_EXIT" -ne 0 ]; then
  echo "Bad-file run exited $RUN_EXIT, as expected for a live failure demo."
fi

section "Failure is visible in audit status"
uv run filedge status --audit-db-url "$AUDIT_DB_URL"

section "Export audit site"
uv run filedge export-audit \
  --audit-db-url "$AUDIT_DB_URL" \
  --output demo/duckdb/out/audit-site/index.html \
  --title "Filedge DuckDB Demo" \
  --dest-table orders

printf "\nAudit site written to demo/duckdb/out/audit-site/index.html\n"
