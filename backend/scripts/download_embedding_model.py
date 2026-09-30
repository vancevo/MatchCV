"""Install the pinned semantic-search model before starting the API.

Runtime requests only read local files and never call Hugging Face.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Direct HTTP writes a resumable partial file. The optional Xet transport can discard a large
# partial checkpoint after an interrupted connection on some local macOS environments.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import snapshot_download


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="BAAI/bge-m3")
    parser.add_argument("--revision", default="5617a9f61b028005a4858fdac845db406aefb181")
    parser.add_argument("--output", default="models/bge-m3")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.repo,
        revision=args.revision,
        local_dir=output,
        # SentenceTransformer only needs the dense PyTorch checkpoint. The repository also
        # publishes multi-gigabyte ONNX exports and demo images that are not used here.
        ignore_patterns=["onnx/*", "imgs/*", "*.jpg", "*.webp", "*.pt"],
    )
    print(f"Embedding model ready at {output}")


if __name__ == "__main__":
    main()
