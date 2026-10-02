"""Install the pinned free multilingual reranker for local-only inference."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import snapshot_download


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--revision", default="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
    parser.add_argument("--output", default="models/bge-reranker-v2-m3")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.repo,
        revision=args.revision,
        local_dir=output,
        ignore_patterns=["onnx/*", "*.jpg", "*.webp", "*.msgpack", "*.h5"],
    )
    print(f"Reranker model ready at {output}")


if __name__ == "__main__":
    main()
