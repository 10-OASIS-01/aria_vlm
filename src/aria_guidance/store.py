"""Durable sessions with explicit planning transitions."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "guidance.sqlite3"
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, session_id TEXT, body TEXT NOT NULL);
            """)
            # A process restart cannot resume generation. Make this visible and retryable.
            for row in db.execute("SELECT id, body FROM sessions").fetchall():
                session = json.loads(row[1])
                if session["state"] == "planning":
                    session["state"] = "ready"
                    session["version"] += 1
                    self._put(db, "sessions", session)
            for row in db.execute("SELECT id, body FROM plans").fetchall():
                plan = json.loads(row[1])
                if plan["state"] == "pending":
                    plan.update(state="failed", error="Service restarted during inference")
                    db.execute("UPDATE plans SET body=? WHERE id=?", (json.dumps(plan), row[0]))

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def _read(db, table, item_id):
        # Table names come only from this module, never from HTTP input.
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return json.loads(row[0])

    @staticmethod
    def _put(db, table, body):
        db.execute(f"UPDATE {table} SET body=? WHERE id=?", (json.dumps(body), body["id"]))

    def create_session(self, goal, source, task_id=""):
        session = {
            "id": str(uuid4()),
            "goal": goal,
            "source": source,
            "task_id": task_id,
            "created_at": time.time(),
            "updated_at": time.time(),
            "state": "ready",
            "version": 0,
            "history": [],
        }
        with self.transaction() as db:
            db.execute("INSERT INTO sessions VALUES (?,?)", (session["id"], json.dumps(session)))
        return session

    def get_session(self, session_id):
        with self.transaction() as db:
            return self._read(db, "sessions", session_id)

    def list_sessions(self, limit=30):
        with self.transaction() as db:
            rows = db.execute("SELECT body FROM sessions ORDER BY rowid DESC LIMIT ?", (limit,))
            return [json.loads(r[0]) for r in rows]

    def plan(self, plan_id):
        with self.transaction() as db:
            try:
                return self._read(db, "plans", plan_id)
            except KeyError:
                return None

    def reserve(self, session_id, plan_id, fingerprint):
        with self.transaction() as db:
            session = self._read(db, "sessions", session_id)
            if session["state"] != "ready":
                raise Conflict(
                    f"Session is {session['state']}; acknowledge the previous step first"
                )
            if db.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone():
                raise Conflict("Request ID already exists")
            plan = {
                "id": plan_id,
                "session_id": session_id,
                "state": "pending",
                "fingerprint": fingerprint,
                "created_at": time.time(),
            }
            db.execute("INSERT INTO plans VALUES (?,?,?)", (plan_id, session_id, json.dumps(plan)))
            session["state"] = "planning"
            session["updated_at"] = time.time()
            self._put(db, "sessions", session)
            return session

    def finish(self, session_id, plan_id, version, response):
        with self.transaction() as db:
            session = self._read(db, "sessions", session_id)
            plan = self._read(db, "plans", plan_id)
            accepted = session["version"] == version and session["state"] == "planning"
            plan.update(state="complete" if accepted else "discarded", response=response)
            self._put(db, "plans", plan)
            if accepted:
                session["state"] = (
                    "completed" if response["status"] == "completed" else "awaiting_feedback"
                )
                session["history"].append({"role": "assistant", "text": response["instruction"]})
                session["history"] = session["history"][-24:]
                session["updated_at"] = time.time()
                self._put(db, "sessions", session)
            return accepted

    def fail(self, session_id, plan_id, version, error):
        with self.transaction() as db:
            session = self._read(db, "sessions", session_id)
            plan = self._read(db, "plans", plan_id)
            plan.update(state="failed", error=error)
            self._put(db, "plans", plan)
            if session["version"] == version and session["state"] == "planning":
                session["state"] = "ready"
                self._put(db, "sessions", session)

    def feedback(self, session_id, event, text=""):
        with self.transaction() as db:
            session = self._read(db, "sessions", session_id)
            if event == "cancel":
                session["state"] = "cancelled"
                session["version"] += 1  # Invalidates any generation currently in flight.
            else:
                if session["state"] not in {"awaiting_feedback", "ready"}:
                    raise Conflict(f"Cannot give feedback while session is {session['state']}")
                session["history"].append(
                    {"role": "user", "text": text or f"{event}; please observe again."}
                )
                session["history"] = session["history"][-24:]
                session["state"] = "ready"
            session["updated_at"] = time.time()
            self._put(db, "sessions", session)
            return session

    def event(self, session_id, body):
        with self.transaction() as db:
            self._read(db, "sessions", session_id)
            db.execute(
                "INSERT INTO events (session_id, body) VALUES (?,?)", (session_id, json.dumps(body))
            )

    def session_detail(self, session_id):
        with self.transaction() as db:
            session = self._read(db, "sessions", session_id)
            session["plans"] = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM plans WHERE session_id=? ORDER BY rowid", (session_id,)
                )
            ]
            for plan in session["plans"]:
                plan.pop("fingerprint", None)
            session["events"] = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM events WHERE session_id=? ORDER BY id", (session_id,)
                )
            ]
            return session
