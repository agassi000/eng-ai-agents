#!/usr/bin/env bash
# Rebuild the entire lakehouse from an empty bucket.
#
# Brings up RustFS + the lab container, resets the bucket and catalog, then runs
# raw -> silver -> gold and the demos. COCO runs always; VisDrone runs if its
# data is present under VISDRONE_DIR (otherwise that step self-skips).
#
# Usage:  ./rebuild.sh
set -euo pipefail

cd "$(dirname "$0")"

# --- preflight: docker must be usable without sudo (see README) ---------------
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: cannot talk to the Docker daemon."
  echo "Add yourself to the docker group once, then start a new shell:"
  echo "    sudo usermod -aG docker \$USER && newgrp docker"
  exit 1
fi

EXEC="docker compose exec -T lab"

echo "==> Removing old DuckLake catalog (clean rebuild)"
rm -f metadata.ducklake metadata.ducklake.wal
rm -rf metadata.ducklake.files

echo "==> Building and starting the stack"
docker compose up -d --build

echo "==> Resetting the lakehouse bucket (empty start)"
$EXEC python scripts/00_create_bucket.py --reset

echo "==> RAW: COCO"
$EXEC python scripts/10_raw_coco.py
echo "==> RAW: VisDrone (skips if data absent)"
$EXEC python scripts/11_raw_visdrone.py

echo "==> RAW checkpoint"
$EXEC python scripts/run_sql.py sql/10_raw.sql
$EXEC python scripts/list_objects.py assets/

echo "==> SILVER transforms (+ schema evolution)"
$EXEC python scripts/run_sql.py sql/20_silver.sql

echo "==> GOLD tables + demonstration queries"
$EXEC python scripts/run_sql.py sql/30_gold.sql
$EXEC python scripts/30_demo_queries.py

echo "==> Version-control demo (snapshots, time travel, rollback)"
$EXEC python scripts/40_versioning_demo.py

cat <<'EOF'

Rebuild complete.

Optional next steps (need configuration):
  * Incremental ingest:  docker compose exec lab python scripts/50_incremental_ingest.py
  * Push gold to the Hub: set GOLD_HF_REPO + HF_TOKEN in .env, then
                          docker compose exec lab python scripts/60_push_gold_to_hub.py

RustFS console: http://localhost:9001  (rustfsadmin / rustfsadmin)
EOF
