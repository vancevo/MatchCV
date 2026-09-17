#!/usr/bin/env bash
set -euo pipefail

REPO_ID="${1:?Usage: bash scripts/upload_talentflow_model_to_hf.sh <hf_user_or_org/repo_name> [source_dir]}"
SOURCE_DIR="${2:-/Users/Vinh/Downloads/output_Phase3_export_model/talentflow_export/v1/merged_model}"

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "huggingface-cli is not installed. Install it with: python -m pip install -U huggingface_hub[cli]" >&2
  exit 1
fi

if [ -z "${HF_TOKEN:-}" ]; then
  echo "HF_TOKEN is not set. Create a Hugging Face token and export it before running this script." >&2
  exit 1
fi

if [ ! -f "${SOURCE_DIR}/config.json" ] || [ ! -f "${SOURCE_DIR}/model.safetensors.index.json" ]; then
  echo "Source directory does not look like a sharded Hugging Face model: ${SOURCE_DIR}" >&2
  exit 1
fi

huggingface-cli upload "${REPO_ID}" "${SOURCE_DIR}" . --repo-type model --private
