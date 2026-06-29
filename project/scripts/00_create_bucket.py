"""Create (and optionally reset) the `lakehouse` bucket on RustFS.

    python scripts/00_create_bucket.py            # create if missing
    python scripts/00_create_bucket.py --reset    # empty + recreate (rebuild)

This is the "from an empty bucket" starting point used by rebuild.sh. It waits
for RustFS to come up, so it is safe to run immediately after `compose up`.
"""
from __future__ import annotations

import argparse

from botocore.exceptions import ClientError

from common import S3_BUCKET, S3_ENDPOINT, wait_for_s3


def bucket_exists(client, name: str) -> bool:
    try:
        client.head_bucket(Bucket=name)
        return True
    except ClientError:
        return False


def empty_bucket(client, name: str) -> int:
    """Delete every object in the bucket. Returns the count removed."""
    removed = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=name):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=name, Delete={"Objects": objects})
            removed += len(objects)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/reset the lakehouse bucket on RustFS")
    parser.add_argument("--reset", action="store_true", help="empty and recreate the bucket")
    parser.add_argument("--bucket", default=S3_BUCKET, help=f"bucket name (default: {S3_BUCKET})")
    args = parser.parse_args()

    print(f"Connecting to RustFS at {S3_ENDPOINT} ...")
    client = wait_for_s3()

    exists = bucket_exists(client, args.bucket)
    if exists and args.reset:
        n = empty_bucket(client, args.bucket)
        client.delete_bucket(Bucket=args.bucket)
        print(f"reset: emptied ({n} objects) and dropped bucket '{args.bucket}'")
        exists = False

    if not exists:
        client.create_bucket(Bucket=args.bucket)
        print(f"created bucket '{args.bucket}'")
    else:
        print(f"bucket '{args.bucket}' already exists")

    buckets = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    print(f"buckets now on RustFS: {buckets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
