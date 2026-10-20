"""Submit a directory of 20 ordered JPEG/PNG frames as a replay observation."""

import argparse
import base64
import json
from pathlib import Path

from PIL import Image

from aria_guidance.client import GuidanceClient
from aria_guidance.frames import encode_jpeg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8785")
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--session", help="Continue a session, confirming the previous instruction")
    args = parser.parse_args()
    files = sorted(
        p for p in args.frames.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if len(files) != 20:
        raise SystemExit("Supply exactly 20 images, sampled at 2 Hz from a 10-second observation")
    client = GuidanceClient(args.url)
    sid = args.session
    if sid:
        client.feedback(sid, "done")
    else:
        sid = client.create_session(args.goal, source="replay")["id"]
    frames = []
    for i, path in enumerate(files):
        with Image.open(path) as image:
            encoded = base64.b64encode(encode_jpeg(image)).decode()
        frames.append({"timestamp_ms": i * 500, "image_base64": encoded})
    print(json.dumps(client.plan(sid, frames), indent=2, ensure_ascii=False))
    client.close()


if __name__ == "__main__":
    main()
