"""Shared COCO ingestion logic used by the initial load (10) and the
incremental load (50). Pulls a slice of a COCO-style HF dataset, uploads each
image to RustFS, and returns metadata-only rows for the raw tables.

Uses *streaming* by default so Hugging Face does not download the entire COCO
archive (~38 GiB for detection-datasets/coco) before taking the first N rows.
The HF cache is mounted at /data/local/hf-cache (on your /home partition) via
docker-compose, not on Docker's small root filesystem.
"""
from __future__ import annotations

import io
import os
import re

import pyarrow as pa

from common import S3_BUCKET, s3_client, s3_uri

# val[:200] or val[200:250]
_SPLIT_SLICE = re.compile(r"^(\w+)(?:\[(\d+)?:(\d+)?\])?$")

ANN_SCHEMA = pa.schema(
    [
        ("image_id", pa.int64()),
        ("image_uri", pa.string()),
        ("category", pa.string()),
        ("bbox", pa.list_(pa.float64())),
        ("area", pa.float64()),
        ("caption", pa.string()),
    ]
)
IMG_SCHEMA = pa.schema(
    [
        ("image_id", pa.int64()),
        ("image_uri", pa.string()),
        ("width", pa.int64()),
        ("height", pa.int64()),
        ("split", pa.string()),
        ("source_split", pa.string()),
    ]
)


def parse_split_expr(split_expr: str) -> tuple[str, int, int | None]:
    """Parse 'val[:200]' or 'val[200:250]' into (split, start, end)."""
    m = _SPLIT_SLICE.match(split_expr.strip())
    if not m:
        return split_expr, 0, None
    split = m.group(1)
    start = int(m.group(2) or 0)
    end = int(m.group(3)) if m.group(3) is not None else None
    return split, start, end


def category_names(ds):
    try:
        return ds.features["objects"].feature["category"].names
    except Exception:  # noqa: BLE001
        return None


def ml_split(image_id: int) -> str:
    """Deterministic 80/20 train/val split for the gold layer."""
    return "train" if (int(image_id) % 5 != 0) else "val"


def to_jpeg_bytes(image) -> bytes:
    if image.mode != "RGB":
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _row_image_id(row: dict) -> int:
    """Support detection-datasets/coco and HuggingFaceM4/COCO-style rows."""
    for key in ("image_id", "id", "cocoid", "imgid"):
        if key in row and row[key] is not None:
            return int(row[key])
    raise KeyError(f"no image id in row keys: {list(row.keys())}")


def _row_objects(row: dict) -> dict:
    if "objects" in row and row["objects"] is not None:
        return row["objects"]
    return {}


def category_names_for_dataset(dataset: str):
    """Resolve COCO category id -> name without downloading the full dataset."""
    try:
        import datasets

        builder = datasets.load_dataset_builder(dataset)
        return builder.info.features["objects"].feature["category"].names
    except Exception:  # noqa: BLE001
        return None


def fetch_and_upload(dataset: str, split_expr: str, source_split: str, s3=None):
    """Stream a COCO slice, upload images to RustFS, return (img_rows, ann_rows)."""
    import datasets

    split, start, end = parse_split_expr(split_expr)
    use_streaming = os.environ.get("COCO_STREAMING", "1").lower() not in {"0", "false", "no"}
    max_rows = (end - start) if end is not None else None
    names = category_names_for_dataset(dataset)

    print(
        f"Loading {dataset} split={split} rows[{start}:{end if end is not None else ''}] "
        f"(streaming={use_streaming}) ..."
    )

    if use_streaming:
        ds = datasets.load_dataset(dataset, split=split, streaming=True)
        rows = []
        for i, row in enumerate(ds):
            if i < start:
                continue
            if end is not None and i >= end:
                break
            rows.append(row)
            if max_rows is not None and len(rows) >= max_rows:
                break
        if not rows:
            raise RuntimeError(f"no rows returned for {dataset} split={split_expr}")
    else:
        ds = datasets.load_dataset(dataset, split=split_expr)
        names = category_names(ds) or names
        rows = list(ds)

    s3 = s3 or s3_client()
    img_rows: list[dict] = []
    ann_rows: list[dict] = []

    for row in rows:
        image_id = _row_image_id(row)
        key = f"assets/coco/{image_id}.jpg"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=to_jpeg_bytes(row["image"]),
            ContentType="image/jpeg",
        )
        uri = s3_uri(key)

        img_rows.append(
            {
                "image_id": image_id,
                "image_uri": uri,
                "width": int(row.get("width") or row["image"].width),
                "height": int(row.get("height") or row["image"].height),
                "split": ml_split(image_id),
                "source_split": source_split,
            }
        )

        objects = _row_objects(row)
        categories = objects.get("category", []) or []
        bboxes = objects.get("bbox", []) or []
        areas = objects.get("area", []) or []
        for i, cat in enumerate(categories):
            cat_name = names[cat] if (names is not None and isinstance(cat, int)) else str(cat)
            ann_rows.append(
                {
                    "image_id": image_id,
                    "image_uri": uri,
                    "category": cat_name,
                    "bbox": [float(v) for v in bboxes[i]] if i < len(bboxes) else None,
                    "area": float(areas[i]) if i < len(areas) and areas[i] is not None else None,
                    "caption": None,
                }
            )

    print(f"Uploaded {len(img_rows)} images; built {len(ann_rows)} annotation rows.")
    return img_rows, ann_rows


def arrow_images(img_rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(img_rows, schema=IMG_SCHEMA)


def arrow_annotations(ann_rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(ann_rows, schema=ANN_SCHEMA)
