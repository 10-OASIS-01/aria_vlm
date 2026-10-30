import math
from itertools import pairwise
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionCreate(StrictModel):
    goal: str = Field(min_length=3, max_length=1500)
    source: Literal["aria", "webcam", "replay"] = "aria"
    task_id: str = Field(default="", max_length=100)


class Frame(StrictModel):
    timestamp_ms: float = Field(ge=0, allow_inf_nan=False)
    image_base64: str = Field(min_length=32, max_length=2 * 1024 * 1024)


class Window(StrictModel):
    frames: list[Frame] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def validate_window(self):
        times = [f.timestamp_ms for f in self.frames]
        if any(b <= a for a, b in pairwise(times)):
            raise ValueError("Frame timestamps must be strictly increasing")
        span = times[-1] - times[0]
        if not 9000 <= span <= 10000.1:
            raise ValueError("20 frames must span 9–10 seconds (normally 9.5 seconds at 2 Hz)")
        if max(b - a for a, b in pairwise(times)) > 1200:
            raise ValueError("Observation has a gap greater than 1.2 seconds")
        return self


class PlanRequest(Window):
    request_id: UUID


class Turn(StrictModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=1500)


class IntentRequest(StrictModel):
    """One utterance to label; the caller (guidance_node) owns the task state."""

    text: str = Field(min_length=1, max_length=1500)
    goal: str = Field(default="", max_length=1500)
    state: str = Field(default="", max_length=40)
    instruction: str = Field(default="", max_length=1500)


class PlanV1(Window):
    """Stateless plan: the caller owns the goal and dialogue history."""

    goal: str = Field(min_length=3, max_length=1500)
    history: list[Turn] = Field(default=[], max_length=24)
    # Live frames are checked for age; recorded ones are not.
    live: bool = True


class LiveCommand(StrictModel):
    """An operator action from the web console, relayed to guidance_node."""

    kind: Literal["goal", "reply", "note", "talk"]
    # The goal, reply or note text; "on" or "off" for talk.
    text: str = Field(default="", max_length=1500)


class Review(StrictModel):
    verdict: Literal["success", "fail"] | None = None
    notes: str = Field(default="", max_length=10_000)


class Feedback(StrictModel):
    event: Literal["done", "next", "feedback", "cancel", "confirm"]
    text: str = Field(default="", max_length=1500)


class ClientEvent(StrictModel):
    event: Literal["tts_published", "tts_suppressed", "tts_repeat", "client_timing", "transcript"]
    request_id: UUID | None = None
    timestamp_ms: float = Field(ge=0, allow_inf_nan=False)
    detail: str = Field(default="", max_length=1500)
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def finite_ms(value: float) -> float:
    return round(value, 2) if math.isfinite(value) else 0.0
