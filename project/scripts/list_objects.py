"""List objects in the RustFS bucket under a prefix (raw-objects checkpoint).

    python scripts/list_objects.py                 # default prefix: assets/
    python scripts/list_objects.py assets/coco/
    python scripts/list_objects.py assets/visdrone/
"""
from __future__ import annotations

import sys

from common import S3_BUCKET, s3_client


def main(argv: list[str]) -> int:
    prefix = argv[1] if len(argv) > 1 else "assets/"
    s3 = s3_client()
    count = 0
    total = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if count < 20:
                print(f"  {obj['Key']}  ({obj['Size']} bytes)")
            count += 1
            total += obj["Size"]
    if count > 20:
        print(f"  ... ({count} objects total)")
    print(f"{count} objects under s3://{S3_BUCKET}/{prefix}, {total} bytes total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
