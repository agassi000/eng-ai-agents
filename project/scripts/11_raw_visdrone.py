"""Land VisDrone-VID (video) into the raw layer as fragments + an index.

Video is the case that forces the lakehouse idea: we never store whole clips in
a table. Instead each sequence (clip) is cut into short MP4 *fragments* written
to RustFS under ``assets/visdrone/<seq>/<fragment>.mp4``, and the lakehouse holds:

  * raw.visdrone_fragments   one row per fragment (clip_uri, fragment_id,
                             start/end frame+time, per-fragment object stats)
  * raw.visdrone_detections  one row per per-frame bounding box

So "give me the busy fragments" becomes SQL over the index, and a loader fetches
only the matching fragment objects from RustFS.

Input: an unpacked VisDrone-VID directory (VISDRONE_DIR) laid out as
    <dir>/sequences/<seq_name>/0000001.jpg ...
    <dir>/annotations/<seq_name>.txt   (frame,id,x,y,w,h,score,cat,trunc,occ)
Download VisDrone2019-VID-val (~1.5 GB) from the official repo (see README) and
unpack it under local-store/visdrone. If the data is absent this script prints
instructions and exits 0 so the rest of the pipeline still runs.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile

import pyarrow as pa

from common import S3_BUCKET, connect, s3_client, s3_uri

VISDRONE_DIR = pathlib.Path(os.environ.get("VISDRONE_DIR", "/data/local/visdrone"))
FRAGMENT_FRAMES = int(os.environ.get("VISDRONE_FRAGMENT_FRAMES", "30"))
NUM_SEQUENCES = int(os.environ.get("VISDRONE_NUM_SEQ", "3"))
FPS = float(os.environ.get("VISDRONE_FPS", "25"))

CATEGORIES = {
    0: "ignored",
    1: "pedestrian",
    2: "people",
    3: "bicycle",
    4: "car",
    5: "van",
    6: "truck",
    7: "tricycle",
    8: "awning-tricycle",
    9: "bus",
    10: "motor",
    11: "others",
}
# Categories that count as real foreground objects for "busy" statistics.
REAL_OBJECT_IDS = set(CATEGORIES) - {0, 11}

FRAG_SCHEMA = pa.schema(
    [
        ("clip_uri", pa.string()),
        ("seq_name", pa.string()),
        ("fragment_id", pa.int64()),
        ("fragment_uri", pa.string()),
        ("start_frame", pa.int64()),
        ("end_frame", pa.int64()),
        ("start_time", pa.float64()),
        ("end_time", pa.float64()),
        ("n_frames", pa.int64()),
        ("n_objects", pa.int64()),
        ("n_detections", pa.int64()),
        ("classes", pa.list_(pa.string())),
    ]
)
DET_SCHEMA = pa.schema(
    [
        ("clip_uri", pa.string()),
        ("seq_name", pa.string()),
        ("fragment_id", pa.int64()),
        ("frame_index", pa.int64()),
        ("target_id", pa.int64()),
        ("category", pa.string()),
        ("bbox", pa.list_(pa.float64())),
        ("score", pa.float64()),
        ("truncation", pa.int64()),
        ("occlusion", pa.int64()),
    ]
)


def find_root(base: pathlib.Path) -> pathlib.Path | None:
    """Locate the dir that holds both sequences/ and annotations/."""
    if not base.exists():
        return None
    candidates = [base, *[p for p in sorted(base.glob("*")) if p.is_dir()]]
    for cand in candidates:
        if (cand / "sequences").is_dir() and (cand / "annotations").is_dir():
            return cand
    return None


def parse_annotations(path: pathlib.Path) -> dict[int, list[dict]]:
    """Parse a VisDrone-VID annotation file into {frame_index: [detections]}."""
    by_frame: dict[int, list[dict]] = {}
    if not path.exists():
        return by_frame
    for line in path.read_text().splitlines():
        parts = [p for p in line.strip().split(",") if p != ""]
        if len(parts) < 8:
            continue
        try:
            frame_index = int(parts[0])
            target_id = int(parts[1])
            x, y, w, h = (float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5]))
            score = float(parts[6])
            cat = int(parts[7])
            trunc = int(parts[8]) if len(parts) > 8 else 0
            occ = int(parts[9]) if len(parts) > 9 else 0
        except ValueError:
            continue
        by_frame.setdefault(frame_index, []).append(
            {
                "target_id": target_id,
                "category": CATEGORIES.get(cat, str(cat)),
                "category_id": cat,
                "bbox": [x, y, w, h],
                "score": score,
                "truncation": trunc,
                "occlusion": occ,
            }
        )
    return by_frame


def encode_fragment(frames: list[pathlib.Path], out_path: pathlib.Path) -> bool:
    """Encode an ordered list of JPEG frames into an MP4 fragment via ffmpeg."""
    with tempfile.TemporaryDirectory() as tmp:
        for idx, frame in enumerate(frames, start=1):
            shutil.copy(frame, pathlib.Path(tmp) / f"{idx:06d}.jpg")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(FPS),
            "-i", str(pathlib.Path(tmp) / "%06d.jpg"),
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"  ffmpeg failed for {out_path.name}: {exc}")
            return False


def print_missing_data_help() -> None:
    print("=" * 72)
    print("VisDrone-VID data not found at", VISDRONE_DIR)
    print("SKIPPING VisDrone ingestion (the rest of the pipeline still runs).")
    print("To ingest it, download VisDrone2019-VID-val (~1.5 GB) from the")
    print("official repo: https://github.com/VisDrone/VisDrone-Dataset")
    print("and unpack so that this layout exists:")
    print(f"  {VISDRONE_DIR}/sequences/<seq_name>/0000001.jpg ...")
    print(f"  {VISDRONE_DIR}/annotations/<seq_name>.txt")
    print("=" * 72)


def main() -> int:
    root = find_root(VISDRONE_DIR)
    if root is None:
        print_missing_data_help()
        return 0

    seq_dirs = sorted(p for p in (root / "sequences").glob("*") if p.is_dir())[:NUM_SEQUENCES]
    if not seq_dirs:
        print_missing_data_help()
        return 0

    s3 = s3_client()
    frag_rows: list[dict] = []
    det_rows: list[dict] = []

    for seq_dir in seq_dirs:
        seq = seq_dir.name
        clip_uri = s3_uri("assets/visdrone", seq)
        frames = sorted(seq_dir.glob("*.jpg"), key=lambda p: int(p.stem))
        ann = parse_annotations(root / "annotations" / f"{seq}.txt")
        print(f"{seq}: {len(frames)} frames, {sum(len(v) for v in ann.values())} detections")

        for frag_id, start in enumerate(range(0, len(frames), FRAGMENT_FRAMES)):
            chunk = frames[start : start + FRAGMENT_FRAMES]
            start_frame = int(chunk[0].stem)
            end_frame = int(chunk[-1].stem)

            out_uri = None
            with tempfile.TemporaryDirectory() as tmp:
                out_path = pathlib.Path(tmp) / f"{frag_id:04d}.mp4"
                if encode_fragment(chunk, out_path):
                    key = f"assets/visdrone/{seq}/{frag_id:04d}.mp4"
                    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=out_path.read_bytes(), ContentType="video/mp4")
                    out_uri = s3_uri(key)

            classes: set[str] = set()
            n_objects = 0
            n_detections = 0
            for fidx in range(start_frame, end_frame + 1):
                for det in ann.get(fidx, []):
                    n_detections += 1
                    if det["category_id"] in REAL_OBJECT_IDS:
                        n_objects += 1
                        classes.add(det["category"])
                    det_rows.append(
                        {
                            "clip_uri": clip_uri,
                            "seq_name": seq,
                            "fragment_id": frag_id,
                            "frame_index": fidx,
                            "target_id": det["target_id"],
                            "category": det["category"],
                            "bbox": det["bbox"],
                            "score": det["score"],
                            "truncation": det["truncation"],
                            "occlusion": det["occlusion"],
                        }
                    )

            frag_rows.append(
                {
                    "clip_uri": clip_uri,
                    "seq_name": seq,
                    "fragment_id": frag_id,
                    "fragment_uri": out_uri,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_time": (start_frame - 1) / FPS,
                    "end_time": (end_frame - 1) / FPS,
                    "n_frames": len(chunk),
                    "n_objects": n_objects,
                    "n_detections": n_detections,
                    "classes": sorted(classes),
                }
            )

    print(f"Built {len(frag_rows)} fragments; {len(det_rows)} detections.")

    con = connect()
    con.register("vd_frag_df", pa.Table.from_pylist(frag_rows, schema=FRAG_SCHEMA))
    con.register("vd_det_df", pa.Table.from_pylist(det_rows, schema=DET_SCHEMA))
    con.execute("CREATE OR REPLACE TABLE raw.visdrone_fragments AS SELECT * FROM vd_frag_df")
    con.execute("CREATE OR REPLACE TABLE raw.visdrone_detections AS SELECT * FROM vd_det_df")
    n_frag = con.execute("SELECT count(*) FROM raw.visdrone_fragments").fetchone()[0]
    n_det = con.execute("SELECT count(*) FROM raw.visdrone_detections").fetchone()[0]
    print(f"raw.visdrone_fragments={n_frag} rows, raw.visdrone_detections={n_det} rows")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
