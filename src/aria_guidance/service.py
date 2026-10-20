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

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .audio import SpeechRecognizer
from .config import Settings
from .engine import ModelBusy, QwenEngine
from .frames import decode_image, encode_jpeg
from .schemas import ClientEvent, Feedback, PlanRequest, SessionCreate
from .store import Conflict, Store

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


def create_app(settings=None, engine=None, load_model=True):
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
        if engine.state != "ready":
            raise HTTPException(503, "Model is loading or unavailable; check /api/ready")
        if engine.status().get("busy"):
            raise HTTPException(429, "One inference is already running; retry after it finishes")
        if session["source"] in {"aria", "webcam"}:
            age = received_at_ms - body.frames[-1].timestamp_ms
            if not -3000 <= age <= 5000:
                raise HTTPException(
                    422, "Live frames must be recent; synchronize the client and server clocks"
                )
        try:
            images = await asyncio.to_thread(
                lambda: [decode_image(f.image_base64) for f in body.frames]
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
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
            store.fail(sid, rid, session["version"], type(e).__name__)
            raise HTTPException(
                503, f"Inference failed ({type(e).__name__}); inspect server logs"
            ) from e

    @app.get("/api/runs/{request_id}/frames/{index}")
    def frame(request_id: UUID, index: int):
        path = runs / str(request_id) / f"{index:02d}.jpg"
        if not 0 <= index < 20 or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/transcribe")
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
