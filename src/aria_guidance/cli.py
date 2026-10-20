import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Run the Qwen3.5-9B Aria guidance service")
    parser.add_argument("--host", default=os.getenv("ARIA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ARIA_PORT", "8785")))
    parser.add_argument("--root-path", default=os.getenv("ARIA_ROOT_PATH", ""))
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(
        "aria_guidance.service:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        root_path=args.root_path.rstrip("/"),
        workers=1,
    )


if __name__ == "__main__":
    main()
