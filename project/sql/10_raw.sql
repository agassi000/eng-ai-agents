-- 10_raw.sql -------------------------------------------------------------------
-- The raw layer is LANDED BY PYTHON (scripts/10_raw_coco.py and
-- scripts/11_raw_visdrone.py) because it has to extract image/video blobs to
-- RustFS and keep only URIs + metadata in the tables.
--
-- This file is the Week-1 CHECKPOINT: it proves the catalog/storage split and
-- lists what landed. Run it with:  python scripts/run_sql.py sql/10_raw.sql
-- (Statements referencing VisDrone are skipped automatically if that optional
--  dataset was not ingested.)

-- Every change so far, as immutable snapshots in the SQL catalog.
FROM ducklake_snapshots('lake') ORDER BY snapshot_id;

-- What tables exist in the catalog right now.
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('raw', 'silver', 'gold')
ORDER BY table_schema, table_name;

-- Row counts in the raw layer.
SELECT 'raw.coco_images'         AS tbl, count(*) AS n FROM raw.coco_images
UNION ALL SELECT 'raw.coco_annotations',    count(*) FROM raw.coco_annotations
UNION ALL SELECT 'raw.visdrone_fragments',  count(*) FROM raw.visdrone_fragments
UNION ALL SELECT 'raw.visdrone_detections', count(*) FROM raw.visdrone_detections;

-- Sample rows: note these hold URIs + metadata, never pixels/frames.
SELECT image_id, image_uri, category, bbox FROM raw.coco_annotations LIMIT 5;
SELECT clip_uri, fragment_id, fragment_uri, n_objects FROM raw.visdrone_fragments LIMIT 5;
