import asyncio
import hashlib
import json
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .audio import SpeechRecognizer
from .config import Settings
from .engine import ModelBusy, QwenEngine
from .frames import decode_image, encode_jpeg
from .live import LiveRelay
from .schemas import (
    ClientEvent,
    Feedback,
    LiveCommand,
    PlanRequest,
    PlanV1,
    Review,
    SessionCreate,
)
from .store import Conflict, Store
from .system import ProcessManager, ros_services
from .trial import (
    find_trial,
    list_trials,
    read_trial,
    trial_captions,
    trial_video,
    write_review,
)

LOGGER = logging.getLogger(__name__)


class RequestGuard:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PATCH", "PUT", "DELETE"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        token = self.settings.api_token
        if token and not secrets.compare_digest(
            headers.get(b"authorization", b"").decode(errors="replace"), f"Bearer {token}"
        ):
            return await JSONResponse({"detail": "API token required"}, status_code=401)(
                scope, receive, send
            )
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.settings.max_request_bytes:
                return await JSONResponse({"detail": "Request exceeds 24 MiB"}, status_code=413)(
                    scope, receive, send
                )
            chunks.append(body)
            if not message.get("more_body", False):
                break
        complete_body, sent = b"".join(chunks), False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": complete_body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings=None, engine=None, load_model=True, system=None):
    settings = settings or Settings()
    store = Store(settings.data_dir)
    engine = engine or QwenEngine(settings)
    speech = SpeechRecognizer(settings.asr_model_path)
    started_at = time.time()
    web = Path(__file__).parent / "web"
    runs = settings.data_dir / "runs"
    runs.mkdir(exist_ok=True)
    reports = settings.data_dir / "reports"
    reports.mkdir(exist_ok=True)
    trial_roots = [settings.sessions_dir, settings.data_dir / "guidance"]
    live = LiveRelay()
    system = system or ProcessManager(
        ros_services(settings, Path(__file__).resolve().parents[2]),
        settings.data_dir / "run",
        settings.data_dir / "logs",
    )

    @asynccontextmanager
    async def lifespan(app):
        if load_model:
            threading.Thread(target=engine.load, name="qwen-load", daemon=True).start()
        yield

    app = FastAPI(title="Aria Guidance", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestGuard, settings=settings)
    app.state.store, app.state.engine = store, engine

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse({"detail": "Item not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "style.css", "favicon.svg"}:
            raise HTTPException(404)
        return FileResponse(web / name)

    @app.get("/api/health")
    def health():
        return {"ok": True, "model_ready": engine.state == "ready", "service": "aria-guidance"}

    @app.get("/api/ready")
    def ready():
        return JSONResponse(engine.status(), status_code=200 if engine.state == "ready" else 503)

    def latest_report():
        path = reports / "latest.json"
        return json.loads(path.read_text()) if path.exists() else None

    @app.get("/api/status")
    def status():
        return {
            "model": engine.status(),
            "speech": speech.status(),
            "uptime_seconds": round(time.time() - started_at),
            "observation": {"frames": 20, "window_seconds": 10, "sampling_hz": 2},
            "auth_required": bool(settings.api_token),
            "hardware": {
                "validated": False,
                "note": "Aria hardware integration must be checked on a ROS host.",
            },
            "report": latest_report(),
            "sessions": store.list_sessions(20),
        }

    @app.post("/api/sessions", status_code=201)
    def new_session(body: SessionCreate):
        return store.create_session(**body.model_dump())

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: UUID):
        return store.session_detail(str(session_id))

    @app.post("/api/sessions/{session_id}/feedback")
    def feedback(session_id: UUID, body: Feedback):
        return store.feedback(str(session_id), **body.model_dump())

    @app.post("/api/sessions/{session_id}/events", status_code=201)
    def event(session_id: UUID, body: ClientEvent):
        if body.request_id:
            plan = store.plan(str(body.request_id))
            if not plan or plan["session_id"] != str(session_id):
                raise HTTPException(422, "Event request_id does not belong to this session")
        store.event(str(session_id), body.model_dump(mode="json"))
        return {"saved": True}

    def save_images(plan_id, images):
        target = runs / plan_id
        target.mkdir(exist_ok=True)
        for i, image in enumerate(images):
            (target / f"{i:02d}.jpg").write_bytes(encode_jpeg(image))

    async def checked_images(body, live, received_at_ms):
        if engine.state != "ready":
            raise HTTPException(503, "Model is loading or unavailable; check /api/ready")
        if engine.status().get("busy"):
            raise HTTPException(429, "One inference is already running; retry after it finishes")
        if live and settings.max_frame_age_ms:
            age = received_at_ms - body.frames[-1].timestamp_ms
            if not -3000 <= age <= settings.max_frame_age_ms:
                raise HTTPException(
                    422,
                    f"Newest frame is {age / 1000:.1f} s old (allowed -3.0 to "
                    f"{settings.max_frame_age_ms / 1000:.1f} s); check the camera stream and "
                    "synchronize the client and server clocks",
                )
        try:
            return await asyncio.to_thread(
                lambda: [decode_image(f.image_base64) for f in body.frames]
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/v1/plan")
    async def plan_v1(body: PlanV1):
        """Stateless step: the caller (guidance_node) owns the task state."""
        started = time.perf_counter()
        received_at_ms = time.time() * 1000
        images = await checked_images(body, body.live, received_at_ms)
        history = [t.model_dump() for t in body.history]
        timestamps = [f.timestamp_ms for f in body.frames]
        try:
            result = await asyncio.to_thread(
                engine.generate, body.goal, history, images, timestamps
            )
        except ModelBusy as e:
            raise HTTPException(429, str(e)) from e
        except Exception as e:
            LOGGER.exception("Stateless planning failed")
            reason = f"{type(e).__name__}: {e}"[:300]
            raise HTTPException(503, f"Inference failed ({reason}); inspect server logs") from e
        return {
            "status": result.status,
            "instruction": result.instruction,
            "raw_output": result.raw_output,
            "proposal": result.proposal,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "timings": {
                **result.timings,
                "server_total_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            "received_at_ms": received_at_ms,
            "responded_at_ms": time.time() * 1000,
        }

    @app.post("/api/sessions/{session_id}/plan")
    async def plan(session_id: UUID, body: PlanRequest):
        started = time.perf_counter()
        received_at_ms = time.time() * 1000
        sid, rid = str(session_id), str(body.request_id)
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        session = store.get_session(sid)
        existing = store.plan(rid)
        if existing:
            if existing["session_id"] != sid or existing["fingerprint"] != fingerprint:
                raise Conflict("Request ID was already used with different input")
            if existing["state"] == "complete" and session["state"] != "cancelled":
                return {**existing["response"], "cached": True}
            raise Conflict(
                f"Request is {existing['state']}; use a new ID after resolving the state"
            )
        images = await checked_images(body, session["source"] in {"aria", "webcam"}, received_at_ms)
        decode_ms = round((time.perf_counter() - started) * 1000, 2)
        session = store.reserve(sid, rid, fingerprint)
        try:
            result = await asyncio.to_thread(
                engine.generate,
                session["goal"],
                session["history"],
                images,
                [f.timestamp_ms for f in body.frames],
            )
            if settings.save_frames:
                await asyncio.to_thread(save_images, rid, images)
            response = {
                "request_id": rid,
                "session_id": sid,
                "status": result.status,
                "instruction": result.instruction,
                "raw_output": result.raw_output,
                "proposal": result.proposal,
                "model": "Qwen/Qwen3.5-9B",
                "cached": False,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "timings": {
                    **result.timings,
                    "decode_ms": decode_ms,
                    "server_total_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                "observation": {
                    "frame_count": 20,
                    "first_timestamp_ms": body.frames[0].timestamp_ms,
                    "last_timestamp_ms": body.frames[-1].timestamp_ms,
                },
                "received_at_ms": received_at_ms,
                "responded_at_ms": time.time() * 1000,
                "preview": f"api/runs/{rid}/frames/19" if settings.save_frames else None,
            }
            accepted = store.finish(sid, rid, session["version"], response)
            if not accepted:
                raise HTTPException(
                    409, "Task was cancelled during inference; instruction discarded"
                )
            return response
        except HTTPException:
            raise
        except ModelBusy as e:
            store.fail(sid, rid, session["version"], str(e))
            raise HTTPException(429, str(e)) from e
        except Exception as e:
            LOGGER.exception("Planning failed for request %s", rid)
            # Keep the message: parse_output rejections (leaked reasoning, echoed goal) differ.
            reason = f"{type(e).__name__}: {e}"[:300]
            store.fail(sid, rid, session["version"], reason)
            raise HTTPException(503, f"Inference failed ({reason}); inspect server logs") from e

    @app.get("/api/runs/{request_id}/frames/{index}")
    def frame(request_id: UUID, index: int):
        path = runs / str(request_id) / f"{index:02d}.jpg"
        if not 0 <= index < 20 or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/transcribe")
    @app.post("/v1/asr")
    async def transcribe(file: Annotated[UploadFile, File()]):
        data = await file.read(6 * 1024 * 1024 + 1)
        if len(data) > 6 * 1024 * 1024:
            raise HTTPException(413, "WAV upload exceeds 6 MiB")
        try:
            return await asyncio.to_thread(speech.transcribe, data)
        except BlockingIOError as e:
            raise HTTPException(429, str(e)) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e

    @app.post("/v1/live/state")
    async def live_state_push(request: Request):
        """guidance_node's state, the same JSON it publishes on /guidance/state."""
        try:
            state = await request.json()
        except ValueError as e:
            raise HTTPException(422, "State must be a JSON object") from e
        if not isinstance(state, dict):
            raise HTTPException(422, "State must be a JSON object")
        await live.push_state(state)
        return {"version": live.version}

    @app.put("/v1/live/preview")
    async def live_preview_push(request: Request):
        jpeg = await request.body()
        if not jpeg.startswith(b"\xff\xd8"):
            raise HTTPException(422, "Preview must be a JPEG")
        await live.push_preview(jpeg)
        return {"saved": True}

    @app.get("/v1/live/commands")
    async def live_commands(wait: float = 10.0):
        return await live.take_commands(min(max(wait, 0.0), 25.0))

    @app.get("/api/live/state")
    async def live_state(after: int = -1, wait: float = 0.0):
        return await live.wait_state(after, min(max(wait, 0.0), 25.0))

    @app.get("/api/live/preview.jpg")
    def live_preview():
        if live.preview is None or not live.node_online():
            raise HTTPException(404, "No live preview; is guidance_node running?")
        return Response(
            live.preview, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/live/command", status_code=202)
    async def live_command(body: LiveCommand):
        if not live.node_online():
            # Refused, not queued: a "done" must not fire minutes later when the node returns.
            raise HTTPException(503, "guidance_node is not connected to this backend")
        await live.add_command(body.model_dump())
        return {"queued": True}

    @app.get("/api/system")
    def system_status():
        return system.status()

    @app.post("/api/system/{name}/{action}")
    def system_action(name: str, action: str):
        """Starts or stops one of the fixed ROS processes; nothing else can be run."""
        if action not in {"start", "stop"} or name not in system.services:
            raise HTTPException(404, "Unknown process or action")
        getattr(system, action)(name)
        return system.status()

    @app.get("/api/trials")
    def trials():
        return list_trials(trial_roots)

    @app.get("/api/trials/{session}/{trial}")
    def trial_detail(session: str, trial: str):
        return read_trial(find_trial(trial_roots, session, trial))

    @app.get("/api/trials/{session}/{trial}/video.mp4")
    async def trial_mp4(session: str, trial: str):
        path = await asyncio.to_thread(trial_video, find_trial(trial_roots, session, trial))
        if path is None:
            raise HTTPException(404, "This trial has no frames")
        # Open trials are re-encoded as frames arrive; closed ones are cached.
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/api/trials/{session}/{trial}/captions.vtt")
    def trial_vtt(session: str, trial: str):
        path = trial_captions(find_trial(trial_roots, session, trial))
        if path is None:
            raise HTTPException(404, "This trial has no frames")
        return FileResponse(path, media_type="text/vtt", headers={"Cache-Control": "no-cache"})

    @app.put("/api/trials/{session}/{trial}/review")
    def review(session: str, trial: str, body: Review):
        return write_review(find_trial(trial_roots, session, trial), body.verdict, body.notes)

    @app.get("/api/reports/latest")
    def report():
        result = latest_report()
        if result is None:
            raise HTTPException(404, "No evaluation has been published")
        return result

    @app.get("/api/download/{name}")
    def download(name: str):
        if name not in {
            "aria-guidance-source.zip",
            "README.md",
            "validation.json",
            "desk-reset.mp4",
            "color-sort.mp4",
        }:
            raise HTTPException(404)
        target = settings.data_dir / "artifacts" / name
        if not target.is_file():
            raise HTTPException(404, "Artifact has not been built yet")
        return FileResponse(target, filename=name)

    return app
