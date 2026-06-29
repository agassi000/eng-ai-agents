# AI Lakehouse - Design Report

This report answers the six design-principle questions for the DuckLake + RustFS
lakehouse built in this repository. Concrete references point at the actual
catalog (`metadata.ducklake`), the RustFS bucket (`s3://lakehouse/`), and the
SQL/scripts that implement each layer.

## 1. Separating a SQL catalog from Parquet data files

A self-contained file (a single `.duckdb`/`.sqlite`, or one big Parquet) couples
three concerns that a lakehouse deliberately pulls apart: **compute**, the
**catalog** (what exists), and the **storage** of bytes. Separating them buys:

- **Independent scaling and cost.** Bytes sit in cheap, durable object storage
  (`s3://lakehouse/`), while the catalog (`metadata.ducklake`) stays small and
  transactional. Many engines/clients can read the same Parquet without copying
  it into a proprietary container.
- **Shared, multi-client access.** The catalog is the single source of truth for
  schemas and snapshots; any DuckDB client that `ATTACH`es it sees the same
  tables. Data files are immutable, so readers never block writers.
- **Evolvability.** New tables/snapshots are catalog rows plus new Parquet
  objects; you do not rewrite a monolithic file.

**Consistency when several clients read at once.** Reads are *snapshot-isolated*:
a query resolves the current snapshot in the catalog, gets a fixed set of
immutable data files, and reads them. Because files are never mutated in place,
concurrent readers are consistent and lock-free, and a reader is unaffected by a
writer producing a newer snapshot. The atomic moment is the **catalog commit**
that publishes a new snapshot id; before it commits, no reader can see the
in-flight files. The flip side is that strong write-write serialization lives in
the SQL catalog, not in object storage - object stores alone only give you
eventual/limited guarantees, which is exactly why the catalog is a real database.

## 2. Snapshots, time travel, and rollback (and what they cost)

Every change writes **new immutable Parquet files** and records a **new snapshot**
in the catalog; nothing is edited in place. An update is a **delete-plus-insert**:
the old rows are marked deleted (via delete files / tombstones) and new rows are
written, all referenced by the new snapshot.

- **Time travel** is just "resolve the table against an older snapshot id instead
  of the latest." The catalog knows which data files (and delete files) belonged
  to snapshot *n*, so `... AT (VERSION => n)` or `AT (TIMESTAMP => t)` reconstructs
  exactly that state by reading those files. `scripts/40_versioning_demo.py`
  shows this: it reads row counts at a "good" version and at a "bad" version from
  the same table.
- **Rollback** makes an old state current again. Because the old files still
  exist, you publish a new snapshot whose contents equal the old one. In this
  repo the demo does it engine-agnostically with
  `CREATE OR REPLACE TABLE gold.demo_labels AS
   SELECT * FROM gold.demo_labels AT (VERSION => v_good)`,
  which reads the trusted snapshot and commits it as the new head - the bad
  `DELETE` is undone without ever mutating data.
- **Cost over time.** Keeping all snapshots means keeping all the data and delete
  files they reference. Storage grows monotonically, and many small commits
  produce many small files, which hurts scan/list performance. Production
  systems mitigate this with **snapshot expiration**, **compaction/vacuum** of
  small files, and pruning unreferenced files. The trade is storage + maintenance
  in exchange for auditability and instant rollback.

## 3. Uniqueness and quality without primary keys or constraints

DuckLake has no PK/FK/`UNIQUE`/`CHECK`, so quality is enforced **by
construction** in the transforms, primarily in `sql/20_silver.sql`:

- **Deduplication = grouping/distinct.** `silver.coco_annotations` collapses
  identical `(image_id, category, bbox)` rows with `GROUP BY`, aggregating
  `area`/`caption`; `silver.coco_images` uses `SELECT DISTINCT` per `image_id`.
  `silver.visdrone_detections` uses `SELECT DISTINCT` over the detection key.
- **Validity filters replace CHECK constraints.** Rows with missing categories or
  malformed boxes are dropped (`category IS NOT NULL`, `length(bbox)=4`); VisDrone
  drops `ignored`/`others` and degenerate boxes (`bbox[3] > 0 AND bbox[4] > 0`).
- **Type discipline at landing.** The raw tables are built from explicit PyArrow
  schemas (`scripts/coco_ingest.py`, `scripts/11_raw_visdrone.py`), so types are
  pinned before they ever hit the catalog.
- **Quality flags.** Silver adds a `quality_flag` column marking suspect rows
  (e.g. non-positive area) rather than silently trusting input.

Because there is no engine-enforced uniqueness, these invariants are
re-established every time the layer is rebuilt - they are properties of the SQL,
not of the storage.

## 4. Tracing one INSERT to bytes

Take `INSERT INTO raw.coco_images SELECT * FROM inc_img_df`
(`scripts/50_incremental_ingest.py`). The state lands in four distinct places:

1. **Engine (DuckDB, in the `lab` container).** Reads the registered Arrow batch
   and serializes the new rows to Parquet.
2. **Parquet data file.** A new immutable Parquet object is produced for the
   inserted rows (existing files are untouched - append, not edit).
3. **S3 object in RustFS.** That Parquet file is written under the table's path in
   `s3://lakehouse/` via the `httpfs` S3 secret; the bytes physically live in
   `./rustfs-data` on the host (bind mount).
4. **Catalog entry + new snapshot.** `metadata.ducklake` records the new
   snapshot, links the table to the added data file, and advances the head
   version. Only after this commit is the INSERT visible to other readers.

So: **schema/snapshot/file-list** live in the catalog file; **the row bytes** live
in a Parquet object in RustFS; **the engine** is the only component that touched
both. `scripts/list_objects.py` shows the resulting objects, and
`FROM ducklake_snapshots('lake')` shows the new snapshot.

## 5. Why a SQL database for the catalog (vs files-only formats)

File-only table formats (e.g. metadata/manifest files written next to the data)
avoid running a database, but push concurrency control onto the object store. A
SQL catalog instead gives you:

**Makes easy**
- **Transactional, serializable commits** for multi-statement, multi-table
  changes - the catalog DB provides ACID, so publishing a snapshot is atomic.
- **Cheap, rich metadata queries** - listing tables, snapshots, and lineage is a
  SQL query (`information_schema`, `ducklake_snapshots`), not an object-store
  crawl over many manifest files.
- **Fewer small metadata objects** and no slow "list a prefix to discover the
  latest manifest" step, which is where file-only formats get expensive.

**Makes harder**
- **An extra moving part to run/back up** - the catalog database must be
  available and durable; lose `metadata.ducklake` and you lose the map to your
  bytes (the data is still there, but you must re-catalog it).
- **A potential central bottleneck / single writer focal point** for commits,
  and a deployment dependency that a purely file-based layout avoids.

This project uses a DuckDB file as the catalog, which is perfect for a
single-node, multi-client-read setup; at larger scale the same role is played by
a shared transactional database.

## 6. Why bytes never enter a table, and how the fragment index works

Pixels and video frames are large and opaque; putting them in table rows would
bloat Parquet, wreck scan performance, and force every query to drag bytes it
does not need. So the rule is: **heavy bytes in object storage, references +
metadata in the lakehouse.** Concretely:

- COCO images are uploaded to `s3://lakehouse/assets/coco/<id>.jpg`; the tables
  hold `image_uri`, `category`, `bbox`, `caption`, `split`.
- VisDrone clips are cut into short MP4 **fragments** in
  `s3://lakehouse/assets/visdrone/<seq>/<frag>.mp4`; the tables hold a fragment
  index and per-frame detections.

**The VisDrone fragment index** (`raw`/`silver.visdrone_fragments`) has one row
per fragment with `clip_uri`, `fragment_id`, `fragment_uri`, `start_frame`/`end_frame`,
`start_time`/`end_time`, and **per-fragment statistics** (`n_objects`, `classes`).
"Give me the busy fragments" is then pure SQL over that index:

```sql
SELECT clip_uri, fragment_id, fragment_uri, n_objects
FROM silver.visdrone_fragments
WHERE n_objects > 20
ORDER BY n_objects DESC;
```

DuckDB answers this by scanning a tiny metadata table - **no video is decoded**.
Only the selected `(clip_uri, fragment_id)` rows point at MP4 objects, and the
loader (`scripts/30_demo_queries.py`) fetches **only those** fragment objects
from RustFS and decodes a frame, reporting bytes read versus total bytes to prove
that whole clips were never scanned. The catalog versions this index like any
other table, so the fragment metadata is itself time-travelable. This is the
small-scale version of how production multimodal data planes (e.g. NVIDIA NeMo
Curator behind Cosmos) curate video: select on metadata, materialize only what a
query needs.
