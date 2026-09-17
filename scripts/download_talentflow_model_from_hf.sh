#!/usr/bin/env bash
set -euo pipefail

REPO_ID="${1:?Usage: bash scripts/download_talentflow_model_from_hf.sh <hf_user_or_org/repo_name> [target_dir]}"
TARGET_DIR="${2:-artifacts/phase3-export/merged_model}"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "huggingface-cli is not installed. Install it with: python -m pip install -U huggingface_hub[cli]" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"

huggingface-cli download "${REPO_ID}" \
  --repo-type model \
  --local-dir "${TARGET_DIR}" \
  --local-dir-use-symlinks False
