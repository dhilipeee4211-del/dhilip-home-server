"""
DhilipHome server-side persistent download manager.

All remote download bytes are written on the Debian server. Android clients only
create/control jobs and read state. Jobs survive app cache clears and server
restarts (active jobs resume from .part when possible).
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.database.database import get_db_connection
from app.utils.config import Config, format_bytes, format_duration
from app.utils.security import is_safe_path

_active: dict[str, threading.Thread] = {}
_lock = threading.RLock()
_stop_events: dict[str, threading.Event] = {}


def _clean_rel(value: str | None) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("/")


def _safe_destination(destination: str) -> Optional[Path]:
    rel = _clean_rel(destination)
    target = (Config.MEDIA_ROOT / rel).resolve()
    if not is_safe_path(Config.MEDIA_ROOT, target) or not target.is_dir():
        return None
    return target


def _safe_filename(name: str) -> str:
    name = Path(str(name or "")).name
    # Keep useful unicode while removing path separators/control chars.
    name = re.sub(r'[\x00-\x1f\x7f/\\]', "_", name).strip()
    return name or f"download_{int(time.time())}.bin"


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    total = int(d.get("total_bytes") or 0)
    downloaded = int(d.get("downloaded_bytes") or 0)
    d["progress_percent"] = round((downloaded * 100.0 / total), 1) if total else 0.0
    d["downloaded_human"] = format_bytes(downloaded)
    d["total_human"] = format_bytes(total) if total else "Unknown size"
    speed = float(d.get("speed_bps") or 0)
    d["speed_text"] = f"{format_bytes(int(speed))}/s" if speed > 0 else ""
    if speed > 0 and total > downloaded:
        d["eta_seconds"] = int((total - downloaded) / speed)
        d["eta_text"] = format_duration(d["eta_seconds"])
    else:
        d["eta_seconds"] = None
        d["eta_text"] = ""
    return d


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE task_id = ?", (task_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_tasks() -> list[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM download_jobs ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _emit(task_id: str):
    try:
        from app import socketio
        task = get_task(task_id)
        if task:
            socketio.emit("download_progress", task)
    except Exception:
        pass


def _update(task_id: str, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    with get_db_connection() as conn:
        conn.execute(f"UPDATE download_jobs SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE task_id = ?", values)
    _emit(task_id)


def _infer_filename(url: str) -> str:
    try:
        path = urllib.request.urlparse(url).path
        name = Path(path).name
        return _safe_filename(name) if name else f"download_{int(time.time())}.bin"
    except Exception:
        return f"download_{int(time.time())}.bin"


def create(url: str, destination: str = "/", filename: str = "") -> Dict[str, Any]:
    url = str(url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Only HTTP/HTTPS URLs are supported")
    dest = _safe_destination(destination or "/")
    if dest is None:
        raise FileNotFoundError("Destination folder does not exist inside MEDIA_ROOT")
    filename = _safe_filename(filename or _infer_filename(url))
    task_id = "srvdl_" + uuid.uuid4().hex[:12]
    rel_dest = _clean_rel(destination)
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO download_jobs
            (id, url, filename, destination, status, downloaded_bytes, total_bytes, speed_bps, error)
            VALUES (?, ?, ?, ?, 'queued', 0, 0, 0, NULL)
        """, (task_id, url, filename, rel_dest))
    _start(task_id)
    return get_task(task_id) or {}


def _start(task_id: str):
    with _lock:
        t = _active.get(task_id)
        if t and t.is_alive():
            return
        stop = threading.Event()
        _stop_events[task_id] = stop
        t = threading.Thread(target=_worker, args=(task_id, stop), daemon=True, name=f"download-{task_id}")
        _active[task_id] = t
        t.start()


def pause(task_id: str) -> Dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise KeyError("Download task not found")
    if task["status"] in ("completed", "cancelled"):
        return task
    _update(task_id, status="paused", error=None)
    stop = _stop_events.get(task_id)
    if stop:
        stop.set()
    return get_task(task_id) or task


def resume(task_id: str) -> Dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise KeyError("Download task not found")
    if task["status"] == "completed":
        return task
    _update(task_id, status="queued", error=None)
    _start(task_id)
    return get_task(task_id) or task


def cancel(task_id: str) -> Dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise KeyError("Download task not found")
    stop = _stop_events.get(task_id)
    if stop:
        stop.set()
    dest = _safe_destination(task["destination"])
    if dest:
        part = dest / (_safe_filename(task["filename"]) + ".part")
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
    _update(task_id, status="cancelled", error=None, speed_bps=0)
    return get_task(task_id) or task


def _worker(task_id: str, stop: threading.Event):
    try:
        task = get_task(task_id)
        if not task:
            return
        dest = _safe_destination(task["destination"])
        if dest is None:
            raise FileNotFoundError("Destination folder does not exist inside MEDIA_ROOT")
        filename = _safe_filename(task["filename"])
        target = dest / filename
        if not is_safe_path(Config.MEDIA_ROOT, target):
            raise ValueError("Destination path outside MEDIA_ROOT")
        part = target.with_name(target.name + ".part")

        downloaded = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "DhilipHome-Server/1.0"}
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        req = urllib.request.Request(task["url"], headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            # Some servers reject Range. Restart cleanly only for 416 or a 200-less range response.
            if downloaded > 0 and exc.code in (400, 416):
                downloaded = 0
                try:
                    part.unlink()
                except OSError:
                    pass
                req = urllib.request.Request(task["url"], headers={"User-Agent": "DhilipHome-Server/1.0"})
                response = urllib.request.urlopen(req, timeout=30)
            else:
                raise

        with response:
            status = getattr(response, "status", response.getcode())
            content_length = int(response.headers.get("Content-Length") or 0)
            content_range = response.headers.get("Content-Range", "")
            total = 0
            if content_range:
                m = re.search(r"/(\d+)$", content_range)
                if m:
                    total = int(m.group(1))
            if not total:
                total = downloaded + content_length if status == 206 else content_length

            if status == 200 and downloaded > 0:
                # Server ignored Range; restart from zero to avoid corrupting the file.
                downloaded = 0
                part.unlink(missing_ok=True)
            mode = "ab" if downloaded > 0 and status == 206 else "wb"
            if mode == "wb":
                downloaded = 0

            _update(task_id, status="downloading", downloaded_bytes=downloaded,
                    total_bytes=total, speed_bps=0, error=None)

            last_emit = time.monotonic()
            last_bytes = downloaded
            last_time = last_emit

            with open(part, mode) as out:
                while True:
                    if stop.is_set():
                        # Pause/cancel command has already updated DB state.
                        return
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_emit >= 0.75:
                        speed = (downloaded - last_bytes) / max(now - last_time, 0.001)
                        _update(task_id, downloaded_bytes=downloaded, total_bytes=total,
                                speed_bps=max(0, int(speed)))
                        last_emit, last_bytes, last_time = now, downloaded, now

                out.flush()
                os.fsync(out.fileno())

        # A stop can arrive just after EOF; honor a server pause/cancel state.
        latest = get_task(task_id)
        if not latest or latest["status"] in ("paused", "cancelled"):
            return

        if total and downloaded < total:
            raise IOError(f"Download ended early ({downloaded} of {total} bytes)")

        os.replace(part, target)
        rel = target.relative_to(Config.MEDIA_ROOT).as_posix()
        _update(task_id, status="completed", downloaded_bytes=target.stat().st_size,
                total_bytes=max(total, target.stat().st_size), speed_bps=0, path=rel, error=None)
        try:
            from app.services.media_service import MediaService
            MediaService.scan_and_index()
        except Exception:
            pass
    except Exception as exc:
        # Keep .part for resumable retry; don't delete it on ordinary failure.
        latest = get_task(task_id)
        if latest and latest["status"] not in ("paused", "cancelled", "completed"):
            _update(task_id, status="failed", error=str(exc), speed_bps=0)
    finally:
        with _lock:
            _active.pop(task_id, None)
            _stop_events.pop(task_id, None)


def resume_persisted_jobs():
    """Called at server startup: make interrupted downloads resumable, not lost."""
    with get_db_connection() as conn:
        conn.execute("""
            UPDATE download_jobs
            SET status='paused', speed_bps=0, updated_at=CURRENT_TIMESTAMP
            WHERE status IN ('downloading','queued')
        """)
    for task in list_tasks():
        if task["status"] == "queued":
            _start(task["id"])
