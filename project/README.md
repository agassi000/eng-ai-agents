# AI Lakehouse (DuckLake + RustFS)

A small but complete, versioned **medallion lakehouse**: a SQL catalog (DuckLake)
separated from immutable Parquet data files in a self-hosted S3 object store
(RustFS), with DuckDB as the query/transform engine. Data moves raw -> silver ->
gold for two datasets (COCO images, VisDrone video) and round-trips with the
Hugging Face Hub.

Project brief: <https://aegean.ai/aiml-common/projects/lakehouse>

## Architecture

```
 Hugging Face Hub                         (sources + one sink)
        |  load_dataset / push_to_hub
        v
 +-------------------- docker compose --------------------+
 |  lab (DuckDB + Python)        rustfs (S3 :9000/:9001)  |
 +--------------------------------------------------------+
        |  ATTACH (SQL)                  ^   data files (Parquet)
        v                                |   + media blobs (assets/)
 metadata.ducklake  ----- DATA_PATH s3://lakehouse/ ------+
 (the CATALOG: schemas, tables, snapshots)
```

- **Catalog** = `metadata.ducklake` (a DuckDB file): what tables/schemas exist and
  every immutable snapshot.
- **Storage** = `s3://lakehouse/` on RustFS: the Parquet bytes plus image/video
  blobs under `assets/`.
- **Engine** = DuckDB, the only component that understands both, reached via
  `DuckDB -> ATTACH (SQL) -> DuckLake -> data files -> RustFS`.

Heavy bytes (pixels, video) live in object storage; the tables hold only URIs +
metadata. See `REPORT.md` for the design rationale.

## Prerequisites

- Docker Engine + Compose v2.
- **One-time: let your user talk to Docker without sudo.** This repo's daemon
  socket is owned by `root:docker`, so add yourself to the `docker` group and
  start a new shell:

  ```bash
  sudo usermod -aG docker $USER
  newgrp docker          # or log out and back in
  docker ps              # should now work without sudo
  ```

- The `rustfs-data/` host directory must be owned by UID `10001` (RustFS runs
  non-root). It already is in this repo; if you recreate it:

  ```bash
  mkdir -p rustfs-data rustfs-logs && sudo chown 10001 rustfs-data rustfs-logs
  ```

## Configuration

Copy the env template and fill in values (`.env` is gitignored):

```bash
cp .env.example .env
# edit .env: set HF_TOKEN (for push_to_hub) and GOLD_HF_REPO when you push.
```

Defaults: RustFS credentials `rustfsadmin` / `rustfsadmin` (they must match the
S3 secret in `sql/00_attach.sql`), COCO source `detection-datasets/coco`, 200
images.

## Quickstart

Build everything from an empty bucket in one command:

```bash
./rebuild.sh
```

This builds the images, resets the bucket + catalog, runs raw -> silver -> gold,
then the demonstration and versioning demos. The RustFS console is at
<http://localhost:9001>.

## Manual run (step by step)

```bash
docker compose up -d --build
docker compose exec lab python scripts/00_create_bucket.py        # create bucket
docker compose exec lab python scripts/10_raw_coco.py             # RAW: COCO
docker compose exec lab python scripts/11_raw_visdrone.py         # RAW: VisDrone (optional data)
docker compose exec lab python scripts/run_sql.py sql/10_raw.sql  # Week-1 checkpoint
docker compose exec lab python scripts/list_objects.py assets/    # raw objects in RustFS
docker compose exec lab python scripts/run_sql.py sql/20_silver.sql
docker compose exec lab python scripts/run_sql.py sql/30_gold.sql
docker compose exec lab python scripts/30_demo_queries.py         # crowded scenes + fragment materialization
docker compose exec lab python scripts/40_versioning_demo.py      # snapshots, time travel, rollback
docker compose exec lab python scripts/50_incremental_ingest.py   # incremental raw snapshot
docker compose exec lab python scripts/60_push_gold_to_hub.py     # push gold to the Hub (needs HF_TOKEN + GOLD_HF_REPO)
```

To run SQL interactively:

```bash
docker compose exec lab python -c "from scripts.common import connect; con=connect(); print(con.sql(\"FROM ducklake_snapshots('lake')\"))"
```

## Datasets

- **COCO (images)** — defaults to `detection-datasets/coco` (clean
  `image + objects{category, bbox}` schema with `person` among the class names,
  so the crowded-scene query works). Override with `COCO_DATASET`, `COCO_SPLIT`,
  `COCO_NUM_IMAGES`. Images are uploaded to `s3://lakehouse/assets/coco/`.
- **VisDrone (video)** — VisDrone-VID is **not** a clean HF loader dataset; the
  official `VisDrone2019-VID-val` archive (~1.5 GB) is on the project's GitHub:
  <https://github.com/VisDrone/VisDrone-Dataset>.

  **Option A — download script (Google Drive, on the host):**

  ```bash
  pip install gdown
  chmod +x scripts/download_visdrone.sh
  ./scripts/download_visdrone.sh
  ```

  **Option B — scp from a course/remote server:** edit `REMOTE_USER`,
  `REMOTE_HOST`, and `REMOTE_ZIP` in
  [`scripts/scp_visdrone_from_remote.sh`](scripts/scp_visdrone_from_remote.sh), then:

  ```bash
  chmod +x scripts/scp_visdrone_from_remote.sh
  ./scripts/scp_visdrone_from_remote.sh
  ```

  **Option C — manual scp one-liner** (replace user/host/path):

  ```bash
  scp user@server:/path/to/VisDrone2019-VID-val.zip local-store/
  unzip local-store/VisDrone2019-VID-val.zip -d local-store/visdrone
  ```

  Required layout (`VISDRONE_DIR` defaults to `./local-store/visdrone`):

  ```
  local-store/visdrone/sequences/<seq_name>/0000001.jpg ...
  local-store/visdrone/annotations/<seq_name>.txt
  ```

  Then ingest:

  ```bash
  docker compose exec lab python scripts/11_raw_visdrone.py
  docker compose exec lab python scripts/run_sql.py sql/20_silver.sql sql/30_gold.sql
  ```

  If the data is absent, the VisDrone step self-skips so the COCO path still completes.

## Hugging Face token (for push_to_hub)

1. Create a token at <https://huggingface.co/settings/tokens> (read + write).
2. Copy `.env.example` to `.env` if needed: `cp .env.example .env`
3. Set in `.env`:
   ```
   HF_TOKEN=hf_xxxxxxxxxxxxxxxx
   GOLD_HF_REPO=your-username/coco-gold
   ```
4. Restart the lab container: `docker compose up -d`
5. Push gold:
   ```bash
   docker compose exec lab python scripts/60_push_gold_to_hub.py
   ```

`HF_TOKEN` is also recommended for faster COCO downloads (higher rate limits).

## Deliverables

- **Design report** (six design-principle questions): [`REPORT.md`](REPORT.md)
- **Gold dataset on Hugging Face**: <https://huggingface.co/datasets/agassi000/coco-gold>

## Repository layout

```
docker-compose.yml      RustFS (bind mounts) + lab (build: .)
Dockerfile              python:3.12-slim + ffmpeg + requirements
requirements.txt        duckdb>=1.3, datasets, huggingface_hub, boto3, pyarrow, pillow
.env / .env.example     secrets + knobs (.env gitignored)
sql/
  00_attach.sql         extensions + S3 secret + ATTACH DuckLake + schemas
  10_raw.sql            Week-1 checkpoint queries
  20_silver.sql         raw -> silver: clean, dedupe, schema evolution
  30_gold.sql           silver -> gold tables + the two required queries
scripts/
  common.py             config, DuckDB connect(), S3 client, SQL runner
  coco_ingest.py        shared COCO load+upload logic
  00_create_bucket.py   create/reset the lakehouse bucket
  10_raw_coco.py        RAW: COCO
  11_raw_visdrone.py    RAW: VisDrone fragments + index
  run_sql.py            run a .sql file, print query results
  list_objects.py       list RustFS objects under a prefix
  30_demo_queries.py    gold queries + fragment materialization proof
  40_versioning_demo.py snapshots / time travel / compare / rollback
  50_incremental_ingest.py  incremental raw snapshot
  60_push_gold_to_hub.py    publish gold dataset to the Hub
rebuild.sh              full rebuild from an empty bucket
REPORT.md               design-principle answers (the graded report)
```

## How the layers map to the brief

- **raw**: data exactly as ingested (URIs + metadata; blobs in `assets/`).
- **silver**: cleaned/typed/deduplicated; `sql/20_silver.sql` also performs two
  schema evolutions (an `ADD COLUMN` and a `RENAME COLUMN`), each a new snapshot.
- **gold**: ML-ready `gold.coco_train` (image_uri + label(s) + caption + split)
  and `gold.visdrone_train` (per-fragment features + busy label).

## Troubleshooting

- **`Not enough disk space` during COCO download** — two things stack:
  1. Your SSD is split: `/` is only ~34 GiB (where Docker stores images/containers
     and the default HF cache), while `/home` has hundreds of GiB free. The lab
     container now mounts `./local-store/hf-cache` and sets `HF_HOME` there so
     cache lands on `/home`.
  2. `detection-datasets/coco` is ~38 GiB if downloaded whole. Ingestion now
     uses **streaming** (`COCO_STREAMING=1`, default) and only fetches the N
     rows you need. Recreate the lab container after pulling this change:
     `docker compose up -d --build`
- **`ModuleNotFoundError: No module named 'pytz'`** when calling
  `ducklake_snapshots` — rebuild the lab image (`docker compose build lab &&
  docker compose up -d`); `pytz` is listed in `requirements.txt` for DuckLake
  snapshot timestamps.
- **`permission denied ... /var/run/docker.sock`** — do the docker-group step
  above (then `newgrp docker` / re-login).
- **Connecting to RustFS from the host** instead of from `lab`: use endpoint
  `localhost:9000` (the `sql/00_attach.sql` secret uses the in-container name
  `rustfs:9000`).
- **RustFS auth errors** — credential env-var names can vary by image tag; if
  `rustfsadmin/rustfsadmin` is rejected, check the RustFS docs for your tag and
  keep `docker-compose.yml`, `.env`, and `sql/00_attach.sql` in sync.
- **DuckLake function/syntax differences** — DuckLake is young; if
  `ducklake_snapshots` columns or `AT (VERSION/TIMESTAMP => ...)` differ, confirm
  against the docs for the DuckDB/DuckLake version that `pip` installed.
- **COCO `person` query empty** — increase `COCO_NUM_IMAGES`, or if you changed
  `COCO_DATASET`, confirm its class name for people.
