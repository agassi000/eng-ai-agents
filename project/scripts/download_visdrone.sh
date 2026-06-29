#!/usr/bin/env bash
# Download and unpack VisDrone2019-VID-val for scripts/11_raw_visdrone.py.
#
# Official source (VisDrone-Dataset repo):
#   https://github.com/VisDrone/VisDrone-Dataset
# Val split (~1.5 GiB) Google Drive file id: 1xuG7Z3IhVfGGKMe3Yj6RnrFHqo_d2a1B
#
# Usage (on the HOST, from the project root):
#   ./scripts/download_visdrone.sh
#
# After unpack, this layout must exist (also visible in the lab container):
#   local-store/visdrone/sequences/<seq_name>/0000001.jpg ...
#   local-store/visdrone/annotations/<seq_name>.txt
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/local-store/visdrone"
ZIP="${ROOT}/local-store/VisDrone2019-VID-val.zip"
GDRIVE_ID="1xuG7Z3IhVfGGKMe3Yj6RnrFHqo_d2a1B"

mkdir -p "${ROOT}/local-store"

if [[ -d "${DEST}/sequences" && -d "${DEST}/annotations" ]]; then
  echo "VisDrone already unpacked at ${DEST}"
  exit 0
fi

if [[ ! -f "${ZIP}" ]]; then
  echo "Downloading VisDrone2019-VID-val (~1.5 GiB) ..."
  if command -v gdown >/dev/null 2>&1; then
    gdown --id "${GDRIVE_ID}" -O "${ZIP}"
  elif python3 -m pip show gdown >/dev/null 2>&1 || python3 -c "import gdown" 2>/dev/null; then
    python3 -m gdown --id "${GDRIVE_ID}" -O "${ZIP}"
  else
    echo "Install gdown first, then re-run:"
    echo "  pip install gdown"
    echo "  ./scripts/download_visdrone.sh"
    echo
    echo "Or download manually in a browser:"
    echo "  https://drive.google.com/file/d/${GDRIVE_ID}/view"
    echo "Save as: ${ZIP}"
    exit 1
  fi
else
  echo "Using existing zip: ${ZIP}"
fi

echo "Unpacking to ${DEST} ..."
rm -rf "${DEST}"
mkdir -p "${DEST}"
unzip -q "${ZIP}" -d "${DEST}"

# Zip usually contains VisDrone2019-VID-val/{sequences,annotations}
if [[ -d "${DEST}/VisDrone2019-VID-val" ]]; then
  mv "${DEST}/VisDrone2019-VID-val"/* "${DEST}/"
  rmdir "${DEST}/VisDrone2019-VID-val"
fi

if [[ ! -d "${DEST}/sequences" || ! -d "${DEST}/annotations" ]]; then
  echo "ERROR: expected sequences/ and annotations/ under ${DEST}"
  echo "Contents:"
  ls -la "${DEST}"
  exit 1
fi

echo "Done. Sequences:"
ls "${DEST}/sequences" | head -5
echo "..."
echo
echo "Next:"
echo "  docker compose exec lab python scripts/11_raw_visdrone.py"
echo "  docker compose exec lab python scripts/run_sql.py sql/20_silver.sql sql/30_gold.sql"
