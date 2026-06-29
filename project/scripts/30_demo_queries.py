"""Demonstrate the two gold-layer queries and, crucially, that a video-fragment
query materializes ONLY the selected fragments from RustFS.

  1. COCO: find crowded scenes (>= 5 people) -- pure metadata, no pixels read.
  2. VisDrone: select busy fragments, then fetch ONLY those MP4 objects from
     RustFS and decode their first frame, reporting bytes read vs. total bytes.
"""
from __future__ import annotations

import pathlib
import subprocess
import tempfile

from common import S3_BUCKET, connect, s3_client


def parse_s3_uri(uri: str) -> tuple[str, str]:
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    return bucket, key


def table_exists(con, schema: str, name: str) -> bool:
    return (
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
            [schema, name],
        ).fetchone()[0]
        > 0
    )


def demo_coco(con) -> None:
    print("== COCO crowded scenes (>= 5 people) ==")
    if not table_exists(con, "silver", "coco_annotations"):
        print("  silver.coco_annotations missing; run the silver step first.")
        return
    rows = con.execute(
        """
        SELECT image_uri, count(*) AS n_people
        FROM silver.coco_annotations
        WHERE category = 'person'
        GROUP BY image_uri
        HAVING count(*) >= 5
        ORDER BY n_people DESC
        LIMIT 10
        """
    ).fetchall()
    for r in rows:
        print("  ", r)
    if not rows:
        print("  (no crowded scenes in this subset; increase COCO_NUM_IMAGES)")


def demo_visdrone(con) -> None:
    print("\n== VisDrone busy fragments + materialization ==")
    if not table_exists(con, "silver", "visdrone_fragments"):
        print("  silver.visdrone_fragments missing; VisDrone not ingested. Skipping.")
        return

    selected = con.execute(
        """
        SELECT clip_uri, fragment_id, fragment_uri, n_objects
        FROM silver.visdrone_fragments
        WHERE n_objects > 20
        ORDER BY n_objects DESC
        LIMIT 5
        """
    ).fetchall()
    if not selected:
        print("  (no fragment exceeded 20 objects; showing the top fragments instead)")
        selected = con.execute(
            """
            SELECT clip_uri, fragment_id, fragment_uri, n_objects
            FROM silver.visdrone_fragments
            ORDER BY n_objects DESC
            LIMIT 5
            """
        ).fetchall()
    for r in selected:
        print("  selected:", r)

    s3 = s3_client()
    total_bytes = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="assets/visdrone/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".mp4"):
                total_bytes += obj["Size"]

    fetched_bytes = 0
    with tempfile.TemporaryDirectory() as tmp:
        for _clip, _fid, frag_uri, _n in selected:
            if not frag_uri:
                continue
            bucket, key = parse_s3_uri(frag_uri)
            data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            fetched_bytes += len(data)
            mp4 = pathlib.Path(tmp) / "frag.mp4"
            mp4.write_bytes(data)
            jpg = pathlib.Path(tmp) / "frame.jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(mp4), "-frames:v", "1", str(jpg)],
                    check=True, capture_output=True,
                )
                size = jpg.stat().st_size if jpg.exists() else 0
                print(f"  materialized {key}: {len(data)} bytes; decoded first frame ({size} bytes)")
            except Exception as exc:  # noqa: BLE001
                print(f"  fetched {key}: {len(data)} bytes (frame decode skipped: {exc})")

    print(
        f"\n  read {fetched_bytes} bytes from {len(selected)} selected fragments "
        f"out of {total_bytes} bytes total -> only matching fragments were materialized."
    )


def main() -> int:
    con = connect()
    try:
        demo_coco(con)
        demo_visdrone(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
