"""Optional download helper. Run through your own approved network route."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="Qwen/Qwen3.5-9B")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    from huggingface_hub import snapshot_download

    snapshot_download(
        args.repo,
        local_dir=args.output.expanduser(),
        revision=args.revision,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.jinja",
            "*.txt",
            "*.bin",
            "LICENSE",
            "README.md",
        ],
        max_workers=4,
    )


if __name__ == "__main__":
    main()
