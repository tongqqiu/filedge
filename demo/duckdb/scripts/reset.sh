#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

rm -f demo/duckdb/out/audit.db
rm -f demo/duckdb/out/warehouse.duckdb
rm -rf demo/duckdb/out/audit-site
rm -f demo/duckdb/incoming/orders_bad.csv
mkdir -p demo/duckdb/out

echo "Reset demo/duckdb/out and restored the good-only incoming set."
