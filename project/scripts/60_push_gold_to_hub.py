"""Close the loop: publish a gold table back to the Hugging Face Hub.

Exports gold.coco_train to Parquet in the local staging area, then pushes it as
a Hub dataset. Requires GOLD_HF_REPO (e.g. your-user/coco-gold) and HF_TOKEN;
if either is unset the script explains what to do and exits without error.
"""
from __future__ import annotations

import os

from common import connect

GOLD_HF_REPO = os.environ.get("GOLD_HF_REPO", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
OUT_PARQUET = os.environ.get("GOLD_PARQUET_PATH", "/data/local/coco_gold.parquet")


def main() -> int:
    if not GOLD_HF_REPO:
        print("GOLD_HF_REPO is unset. Set it (e.g. your-user/coco-gold) in .env to push. Skipping.")
        return 0
    if not HF_TOKEN:
        print("HF_TOKEN is empty. Add your token to .env to push. Skipping.")
        return 0

    con = connect()
    con.execute(
        f"""
        COPY (
            SELECT image_id, image_uri, split, primary_label, labels, n_objects, caption
            FROM gold.coco_train
        ) TO '{OUT_PARQUET}' (FORMAT parquet)
        """
    )
    con.close()
    print(f"Wrote gold table to {OUT_PARQUET}")

    import datasets

    ds = datasets.Dataset.from_parquet(OUT_PARQUET)
    ds.push_to_hub(GOLD_HF_REPO, token=HF_TOKEN)
    print(f"Pushed gold.coco_train -> https://huggingface.co/datasets/{GOLD_HF_REPO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
