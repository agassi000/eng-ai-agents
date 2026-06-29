-- 00_attach.sql ---------------------------------------------------------------
-- Extensions + S3 secret + ATTACH the DuckLake catalog.
-- Run this first in every DuckDB session (scripts/common.py does this for you).
--
-- The separation that defines a lakehouse:
--   * metadata.ducklake  -> the SQL CATALOG (what tables/schemas/snapshots exist)
--   * s3://lakehouse/     -> the DATA (immutable Parquet files in RustFS)

INSTALL ducklake;  LOAD ducklake;
INSTALL httpfs;    LOAD httpfs;

-- Talk to RustFS over the S3 protocol. ENDPOINT uses the in-container hostname
-- 'rustfs:9000'; switch to 'localhost:9000' if you connect from the host.
CREATE OR REPLACE SECRET rustfs (
    TYPE s3,
    KEY_ID 'rustfsadmin',
    SECRET 'rustfsadmin',
    ENDPOINT 'rustfs:9000',
    URL_STYLE 'path',
    USE_SSL false
);

-- Catalog (metadata) in a local DuckDB file; data files live on the RustFS bucket.
ATTACH IF NOT EXISTS 'ducklake:metadata.ducklake' AS lake (DATA_PATH 's3://lakehouse/');
USE lake;

-- Medallion layers.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
