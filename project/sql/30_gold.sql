-- 30_gold.sql ------------------------------------------------------------------
-- silver -> gold: curated, ML-ready tables, plus the two required demonstration
-- queries. Run with:  python scripts/run_sql.py sql/30_gold.sql
-- (For the VisDrone "materialize only selected fragments" proof, see
--  scripts/30_demo_queries.py.)

-- COCO training table: one row per image = image_uri + label(s) + caption + split.
CREATE OR REPLACE TABLE gold.coco_train AS
WITH labeled AS (
    SELECT i.image_id, i.image_uri, i.ml_split AS split, a.category, a.caption
    FROM silver.coco_images i
    LEFT JOIN silver.coco_annotations a ON i.image_id = a.image_id
),
cat_counts AS (
    SELECT image_id, category, count(*) AS cnt
    FROM labeled
    WHERE category IS NOT NULL
    GROUP BY image_id, category
),
primary_label AS (
    SELECT image_id, arg_max(category, cnt) AS primary_label
    FROM cat_counts
    GROUP BY image_id
)
SELECT
    l.image_id,
    any_value(l.image_uri)                                   AS image_uri,
    any_value(l.split)                                       AS split,
    coalesce(any_value(p.primary_label), 'background')       AS primary_label,
    list(DISTINCT l.category) FILTER (WHERE l.category IS NOT NULL) AS labels,
    count(l.category)                                        AS n_objects,
    any_value(l.caption)                                     AS caption
FROM labeled l
LEFT JOIN primary_label p USING (image_id)
GROUP BY l.image_id;

-- VisDrone training table: one row per fragment = URI + per-fragment features +
-- a "busy" training label, with the dominant object class.
CREATE OR REPLACE TABLE gold.visdrone_train AS
WITH cls AS (
    SELECT seq_name, fragment_id, category, count(*) AS c
    FROM silver.visdrone_detections
    GROUP BY seq_name, fragment_id, category
),
dom AS (
    SELECT seq_name, fragment_id, arg_max(category, c) AS dominant_class
    FROM cls
    GROUP BY seq_name, fragment_id
)
SELECT
    f.clip_uri, f.seq_name, f.fragment_id, f.fragment_uri,
    f.start_frame, f.end_frame, f.start_time, f.end_time,
    f.n_frames, f.n_objects, f.classes,
    d.dominant_class,
    (f.n_objects >= 20) AS is_busy
FROM silver.visdrone_fragments f
LEFT JOIN dom d USING (seq_name, fragment_id);

-- ---------------------------------------------------------------------------
-- Required demonstration query #1: COCO crowded scenes (>= 5 people).
-- Pure metadata query over URIs/labels -- no pixels are touched.
SELECT image_uri, count(*) AS n_people
FROM silver.coco_annotations
WHERE category = 'person'
GROUP BY image_uri
HAVING count(*) >= 5
ORDER BY n_people DESC
LIMIT 20;

-- Required demonstration query #2: VisDrone busy fragments.
-- This selects which (clip_uri, fragment_id) a loader should fetch; only those
-- fragment objects are then read from RustFS (see scripts/30_demo_queries.py).
SELECT clip_uri, fragment_id, fragment_uri, start_frame, end_frame, n_objects
FROM silver.visdrone_fragments
WHERE n_objects > 20
ORDER BY n_objects DESC
LIMIT 20;
