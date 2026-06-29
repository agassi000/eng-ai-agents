-- 20_silver.sql ----------------------------------------------------------------
-- raw -> silver: fix types, drop invalid rows, deduplicate, and perform a
-- schema evolution. DuckLake has no PK/UNIQUE/CHECK constraints, so quality and
-- uniqueness are enforced HERE, by construction. Each statement that mutates a
-- table records a new immutable snapshot.
--
-- Run with:  python scripts/run_sql.py sql/20_silver.sql

-- COCO images: keep one row per image_id, require sane dimensions.
CREATE OR REPLACE TABLE silver.coco_images AS
SELECT DISTINCT image_id, image_uri, width, height, split
FROM raw.coco_images
WHERE width > 0 AND height > 0;

-- COCO annotations: drop rows with no category or malformed bbox, and collapse
-- exact duplicate (image, category, bbox) annotations into one (uniqueness by
-- construction). bbox is a 4-element list [x, y, w, h].
CREATE OR REPLACE TABLE silver.coco_annotations AS
SELECT
    image_id,
    image_uri,
    category,
    bbox,
    max(area)        AS area,
    any_value(caption) AS caption
FROM raw.coco_annotations
WHERE category IS NOT NULL AND category <> ''
  AND bbox IS NOT NULL AND length(bbox) = 4
GROUP BY image_id, image_uri, category, bbox;

-- SCHEMA EVOLUTION #1 (add column): tag rows with a data-quality flag, then
-- populate it. The ADD COLUMN and the UPDATE each create a new snapshot.
ALTER TABLE silver.coco_annotations ADD COLUMN IF NOT EXISTS quality_flag VARCHAR;
UPDATE silver.coco_annotations
SET quality_flag = CASE WHEN area IS NULL OR area <= 0 THEN 'check_area' ELSE 'ok' END;

-- SCHEMA EVOLUTION #2 (rename column): split -> ml_split. The gold layer reads
-- the new name, so this demonstrates evolution flowing downstream.
ALTER TABLE silver.coco_images RENAME COLUMN split TO ml_split;

-- VisDrone fragments: keep only fragments that actually have a stored MP4 and
-- at least one frame; dedupe by (sequence, fragment).
CREATE OR REPLACE TABLE silver.visdrone_fragments AS
SELECT DISTINCT clip_uri, seq_name, fragment_id, fragment_uri,
       start_frame, end_frame, start_time, end_time,
       n_frames, n_objects, n_detections, classes
FROM raw.visdrone_fragments
WHERE fragment_uri IS NOT NULL AND n_frames > 0;

-- VisDrone detections: drop ignored/others classes and degenerate boxes
-- (width=bbox[3], height=bbox[4]; DuckDB lists are 1-indexed); dedupe.
CREATE OR REPLACE TABLE silver.visdrone_detections AS
SELECT DISTINCT clip_uri, seq_name, fragment_id, frame_index, target_id,
       category, bbox, score, truncation, occlusion
FROM raw.visdrone_detections
WHERE category NOT IN ('ignored', 'others')
  AND bbox IS NOT NULL AND length(bbox) = 4
  AND bbox[3] > 0 AND bbox[4] > 0;

-- Verify the new snapshots produced by the transforms + schema evolution.
FROM ducklake_snapshots('lake') ORDER BY snapshot_id DESC LIMIT 8;
