#!/usr/bin/env bash
# Copy VisDrone2019-VID-val from a remote machine with scp.
#
# Edit REMOTE_USER, REMOTE_HOST, and REMOTE_ZIP below, then run from the project root:
#   ./scripts/scp_visdrone_from_remote.sh
#
# If the zip is already on the remote under a different path, change REMOTE_ZIP.
# If sequences/annotations are already unpacked on the remote, use the second block
# at the bottom of this file instead.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/local-store"
ZIP="${DEST}/VisDrone2019-VID-val.zip"

# --- edit these ---
REMOTE_USER="${REMOTE_USER:-your_username}"
REMOTE_HOST="${REMOTE_HOST:-course-server.example.edu}"
REMOTE_ZIP="${REMOTE_ZIP:-/data/datasets/VisDrone2019-VID-val.zip}"
# ------------------

mkdir -p "${DEST}"

echo "Copying ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_ZIP}"
echo "            -> ${ZIP}"
scp "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_ZIP}" "${ZIP}"

echo "Unpacking ..."
rm -rf "${DEST}/visdrone"
mkdir -p "${DEST}/visdrone"
unzip -q "${ZIP}" -d "${DEST}/visdrone"
if [[ -d "${DEST}/visdrone/VisDrone2019-VID-val" ]]; then
  mv "${DEST}/visdrone/VisDrone2019-VID-val"/* "${DEST}/visdrone/"
  rmdir "${DEST}/visdrone/VisDrone2019-VID-val"
fi

echo "Done. Run:"
echo "  docker compose exec lab python scripts/11_raw_visdrone.py"

# --- Alternative: copy an already-unpacked tree (uncomment and edit) ---
# REMOTE_DIR="/data/datasets/VisDrone2019-VID-val"
# scp -r "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/sequences" "${DEST}/visdrone/"
# scp -r "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/annotations" "${DEST}/visdrone/"
