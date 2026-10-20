"""Runtime configuration; machine paths belong in environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    model_path: str = field(default_factory=lambda: os.getenv("ARIA_MODEL_PATH", "Qwen/Qwen3.5-9B"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("ARIA_DATA_DIR", ".runtime")))
    device: str = field(default_factory=lambda: os.getenv("ARIA_DEVICE", "cuda:0"))
    max_pixels: int = field(default_factory=lambda: int(os.getenv("ARIA_MAX_PIXELS", "131072")))
    max_new_tokens: int = 96
    asr_model_path: str = field(default_factory=lambda: os.getenv("ARIA_ASR_MODEL_PATH", ""))
    api_token: str = field(default_factory=lambda: os.getenv("ARIA_API_TOKEN", ""))
    save_frames: bool = field(default_factory=lambda: os.getenv("ARIA_SAVE_FRAMES", "1") == "1")
    max_request_bytes: int = 24 * 1024 * 1024

    def __post_init__(self):
        if self.max_pixels < 65536 or self.max_pixels > 1048576:
            raise ValueError("ARIA_MAX_PIXELS must be between 65536 and 1048576")
        self.data_dir = self.data_dir.expanduser().resolve()
