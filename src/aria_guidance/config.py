"""Runtime configuration; machine paths belong in environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    model_path: str = field(default_factory=lambda: os.getenv("ARIA_MODEL_PATH", "Qwen/Qwen3.5-9B"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("ARIA_DATA_DIR", ".runtime")))
    device: str = field(default_factory=lambda: os.getenv("ARIA_DEVICE", "cuda:0"))
    # Per frame; 307200 keeps the bridge's full 640x480 so small objects stay visible.
    max_pixels: int = field(default_factory=lambda: int(os.getenv("ARIA_MAX_PIXELS", "307200")))
    # "video": the 20 frames as one 2 fps clip; "images": 20 separate pictures (2x the tokens).
    input_mode: str = field(default_factory=lambda: os.getenv("ARIA_INPUT_MODE", "video"))
    # Live frames older than this on arrival are rejected; 0 disables the check.
    max_frame_age_ms: int = field(
        default_factory=lambda: int(os.getenv("ARIA_MAX_FRAME_AGE_MS", "5000"))
    )
    # aria_node's session folders, where guidance_node writes trials (also .runtime/guidance).
    sessions_dir: Path = field(
        default_factory=lambda: Path(os.getenv("ARIA_SESSIONS_DIR", "../aria2_ros2/data/aria"))
    )
    # What the console's System row starts: aria2_ros2's workspace, its DDS profile, the test image.
    ros_workspace: Path = field(
        default_factory=lambda: Path(os.getenv("ARIA_ROS_WORKSPACE", "../aria2_ros2"))
    )
    fastdds_profile: str = field(default_factory=lambda: os.getenv("ARIA_FASTDDS_PROFILE", ""))
    fake_image: str = field(default_factory=lambda: os.getenv("ARIA_FAKE_IMAGE", ""))
    # Four lines (EVIDENCE, MODE, SAY, EXPECT) run about 100 tokens.
    max_new_tokens: int = 192
    asr_model_path: str = field(default_factory=lambda: os.getenv("ARIA_ASR_MODEL_PATH", ""))
    api_token: str = field(default_factory=lambda: os.getenv("ARIA_API_TOKEN", ""))
    save_frames: bool = field(default_factory=lambda: os.getenv("ARIA_SAVE_FRAMES", "1") == "1")
    max_request_bytes: int = 24 * 1024 * 1024

    def __post_init__(self):
        if self.max_pixels < 65536 or self.max_pixels > 1048576:
            raise ValueError("ARIA_MAX_PIXELS must be between 65536 and 1048576")
        if self.input_mode not in {"video", "images"}:
            raise ValueError("ARIA_INPUT_MODE must be video or images")
        if self.max_frame_age_ms < 0:
            raise ValueError("ARIA_MAX_FRAME_AGE_MS must be 0 (disabled) or positive")
        self.data_dir = self.data_dir.expanduser().resolve()
        self.sessions_dir = self.sessions_dir.expanduser().resolve()
        self.ros_workspace = self.ros_workspace.expanduser().resolve()
