"""Build a portable runtime source archive from an explicit file allowlist."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".runtime/artifacts"))
    parser.add_argument(
        "--forbid", action="append", default=[], help="Reject a machine-specific literal in source"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = [
        "README.md",
        "LICENSE",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        ".gitmodules",
        "requirements-model.txt",
    ]
    paths = [root / name for name in names if (root / name).is_file()]
    for name in [
        "src/aria_guidance",
        "ros2_ws/src/aria_planner_bridge",
        "scripts",
        "deploy",
        "docs",
    ]:
        for path in (root / name).rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if any(
                part.startswith(".")
                or part in {"__pycache__", "tests", "test"}
                or part.endswith(".egg-info")
                for part in path.relative_to(root).parts
            ):
                continue
            if path.suffix in {".pyc", ".pyo"} or path.name.startswith("test_"):
                continue
            paths.append(path)
    forbidden = [str(root), *args.forbid]
    entries = []
    for path in sorted(set(paths)):
        data = path.read_bytes()
        if any(value.encode() in data for value in forbidden):
            raise SystemExit(f"Machine-specific literal found in {path.relative_to(root)}")
        entries.append((path.relative_to(root).as_posix(), data))
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "aria-guidance-source.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(f"aria-guidance/{name}", data)
    shutil.copyfile(root / "README.md", args.output / "README.md")
    manifest = {
        "archive": target.name,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "files": [name for name, _ in entries],
        "local_configuration_included": False,
        "verification_tools_included": False,
        "infrastructure": {
            "url": "https://github.com/sparklabutah/aria2_ros2.git",
            "commit": "1af73bdd179f5c31ce7dbbfa6d78bfc0727a99a6",
            "source_included": False,
        },
    }
    (args.output / "release-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"file_count": len(entries), "sha256": manifest["sha256"]}))


if __name__ == "__main__":
    main()
