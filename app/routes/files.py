"""Secure file management and server-side cloud downloads."""
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Blueprint, request, send_file
from werkzeug.utils import secure_filename

from app.services.file_service import FileService
from app.utils.config import Config
from app.utils.security import require_auth, success_response, error_response

files_bp = Blueprint("files", __name__)
_remote_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dhilip-remote-download")
_remote_lock = threading.RLock()
_remote_tasks = {}


def _is_admin():
    return str(getattr(request, "current_user", {}).get("role", "")).lower() == "admin"


def require_admin():
    if not _is_admin():
        return error_response("ADMIN_REQUIRED", "Administrator permission is required for this action", 403)
    return None


def _task_update(task_id, **changes):
    with _remote_lock:
        task = _remote_tasks.get(task_id)
        if task:
            task.update(changes)


def _remote_download_worker(task_id, url, destination, filename):
    target_dir = FileService.resolve_safe_path(destination)
    if target_dir is None or not target_dir.is_dir():
        raise FileNotFoundError("Destination folder does not exist inside MEDIA_ROOT")

    target = target_dir / secure_filename(filename)
    if not target.name:
        raise ValueError("Invalid filename")
    if target.exists():
        raise FileExistsError(f"File '{target.name}' already exists")

    tmp = target.with_name(target.name + ".part")
    started = time.monotonic()
    downloaded = 0
    last_bytes = 0
    last_time = started
    req = urllib.request.Request(url, headers={"User-Agent": "DhilipHome-Server/0.2.1"})

    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(tmp, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            _task_update(task_id, status="downloading", total_bytes=total, speed_bps=0)

            while True:
                with _remote_lock:
                    task = _remote_tasks.get(task_id, {})
                    if task.get("cancel_requested"):
                        raise InterruptedError("Download cancelled by user")

                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)

                now = time.monotonic()
                interval = now - last_time
                if interval >= 0.5:
                    speed = int((downloaded - last_bytes) / interval)
                    elapsed = max(now - started, 0.001)
                    avg_speed = int(downloaded / elapsed)
                    eta = int((total - downloaded) / speed) if speed > 0 and total > downloaded else None
                    percent = int(downloaded * 100 / total) if total else 0
                    _task_update(
                        task_id,
                        downloaded_bytes=downloaded,
                        progress_percent=min(99, percent),
                        speed_bps=max(speed, 0),
                        average_speed_bps=max(avg_speed, 0),
                        eta_seconds=eta,
                    )
                    last_bytes, last_time = downloaded, now

            out.flush()
            os.fsync(out.fileno())

        os.replace(tmp, target)
        size = target.stat().st_size
        try:
            from app.services.media_service import MediaService
            MediaService.scan_and_index()
        except Exception:
            pass

        _task_update(
            task_id,
            status="completed",
            progress_percent=100,
            downloaded_bytes=size,
            total_bytes=total or size,
            speed_bps=0,
            eta_seconds=0,
            path=target.relative_to(Config.MEDIA_ROOT).as_posix(),
        )
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def _run_remote_download(task_id, url, destination, filename):
    try:
        _remote_download_worker(task_id, url, destination, filename)
    except InterruptedError as exc:
        _task_update(task_id, status="cancelled", error=str(exc), speed_bps=0)
    except Exception as exc:
        _task_update(task_id, status="failed", error=str(exc), speed_bps=0)


@files_bp.route("/api/files/remote-download", methods=["POST"])
@require_auth
def start_remote_download():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    destination = str(data.get("destination", "") or "/").strip() or "/"
    filename = str(data.get("filename", "") or "").strip()

    if not url or not url.lower().startswith(("http://", "https://")):
        return error_response("INVALID_URL", "Only HTTP/HTTPS URLs are supported", 400)
    if not filename:
        filename = Path(urllib.parse.urlparse(url).path).name or "download.bin"
    filename = secure_filename(filename)
    if not filename:
        return error_response("INVALID_FILENAME", "Invalid destination filename", 400)

    target_dir = FileService.resolve_safe_path(destination)
    if target_dir is None or not target_dir.is_dir():
        return error_response("DESTINATION_NOT_FOUND", "Destination folder does not exist", 404)

    task_id = "srvdl_" + uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "status": "queued",
        "progress_percent": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0,
        "average_speed_bps": 0,
        "eta_seconds": None,
        "filename": filename,
        "destination": destination,
        "url": url,
        "created_at": time.time(),
        "cancel_requested": False,
    }
    with _remote_lock:
        _remote_tasks[task_id] = task
    _remote_executor.submit(_run_remote_download, task_id, url, destination, filename)
    return success_response(task, "Server download started", 202)


@files_bp.route("/api/files/remote-download", methods=["GET"])
@require_auth
def remote_download_status():
    task_id = request.args.get("task_id", "").strip()
    if not task_id:
        return error_response("TASK_ID_REQUIRED", "task_id is required", 400)
    with _remote_lock:
        task = dict(_remote_tasks.get(task_id, {}))
    if not task:
        return error_response("TASK_NOT_FOUND", "Download task was not found", 404)
    return success_response(task)


@files_bp.route("/api/files/remote-download/cancel", methods=["POST"])
@require_auth
def cancel_remote_download():
    data = request.get_json(silent=True) or {}
    task_id = str(data.get("task_id", "")).strip()
    if not task_id:
        return error_response("TASK_ID_REQUIRED", "task_id is required", 400)
    with _remote_lock:
        task = _remote_tasks.get(task_id)
        if not task:
            return error_response("TASK_NOT_FOUND", "Download task was not found", 404)
        if task.get("status") in {"completed", "failed", "cancelled"}:
            return success_response(dict(task), "Download already finished")
        task["cancel_requested"] = True
        task["status"] = "cancelling"
    return success_response(dict(task), "Cancellation requested")


@files_bp.route("/api/files", methods=["GET"])
@files_bp.route("/api/files/list", methods=["GET"])
@require_auth
def list_files():
    path_arg = request.args.get("path", "")
    try:
        return success_response(FileService.list_directory(path_arg))
    except FileNotFoundError as e:
        return error_response("NOT_FOUND", str(e), 404)
    except NotADirectoryError as e:
        return error_response("NOT_A_DIRECTORY", str(e), 400)
    except Exception as e:
        return error_response("LIST_FILES_ERROR", str(e), 500)


@files_bp.route("/api/files/info", methods=["GET"])
@require_auth
def get_file_info():
    path_arg = request.args.get("path", "")
    if not path_arg:
        return error_response("PARAM_REQUIRED", "Query parameter 'path' is required", 400)
    try:
        return success_response(FileService.get_item_info(path_arg))
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e), 404)
    except Exception as e:
        return error_response("FILE_INFO_ERROR", str(e), 500)


@files_bp.route("/api/files/download", methods=["GET", "HEAD"])
def download_file():
    from app.utils.security import get_request_token, verify_token
    valid, _, err = verify_token(get_request_token())
    if not valid:
        return error_response("AUTH_REQUIRED", f"Authentication failed: {err}", 401)
    path_arg = request.args.get("path", "")
    if not path_arg:
        return error_response("PARAM_REQUIRED", "Query parameter 'path' is required", 400)
    target = FileService.resolve_safe_path(path_arg)
    if target is None or not target.is_file():
        return error_response("FILE_NOT_FOUND", "File was not found or invalid path", 404)
    return send_file(str(target), as_attachment=True, download_name=target.name, conditional=True, etag=True)


@files_bp.route("/api/files/upload", methods=["POST"])
@require_auth
def upload_file():
    if "file" not in request.files:
        return error_response("NO_FILE_UPLOADED", "No file found in multipart upload", 400)
    uploaded = request.files["file"]
    parent_path = request.form.get("path", "")
    try:
        info = FileService.save_uploaded_file(parent_path, uploaded)
        try:
            from app.services.media_service import MediaService
            MediaService.scan_and_index()
        except Exception:
            pass
        return success_response(info, "File uploaded successfully", 201)
    except FileNotFoundError as e:
        return error_response("TARGET_DIR_NOT_FOUND", str(e), 404)
    except ValueError as e:
        return error_response("INVALID_UPLOAD", str(e), 400)
    except Exception as e:
        return error_response("UPLOAD_FAILED", str(e), 500)


@files_bp.route("/api/files/folder", methods=["POST"])
@require_auth
def create_folder():
    data = request.get_json(silent=True) or {}
    folder_name = data.get("name") or request.form.get("name", "")
    parent_path = data.get("path") or request.form.get("path", "")
    if not folder_name:
        return error_response("NAME_REQUIRED", "Folder name is required", 400)
    try:
        return success_response(FileService.create_folder(parent_path, folder_name), "Folder created successfully", 201)
    except FileExistsError as e:
        return error_response("FOLDER_EXISTS", str(e), 409)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FOLDER_CREATE_ERROR", str(e), 400)
    except Exception as e:
        return error_response("FOLDER_CREATE_FAILED", str(e), 500)


@files_bp.route("/api/files/rename", methods=["POST"])
@require_auth
def rename_file_or_folder():
    denied = require_admin()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    path_arg = str(data.get("path", "")).strip()
    new_name = str(data.get("new_name", "")).strip()
    if not path_arg or not new_name:
        return error_response("PARAM_REQUIRED", "path and new_name are required", 400)
    try:
        target = FileService.resolve_safe_path(path_arg)
        if target is None or not target.exists() or target == Config.MEDIA_ROOT:
            return error_response("FILE_NOT_FOUND", "Target item not found", 404)
        safe_name = secure_filename(new_name)
        if not safe_name:
            return error_response("INVALID_NAME", "Invalid new name", 400)
        destination = target.parent / safe_name
        if not FileService.resolve_safe_path(destination.relative_to(Config.MEDIA_ROOT).as_posix()):
            return error_response("INVALID_PATH", "Invalid destination path", 400)
        if destination.exists():
            return error_response("NAME_EXISTS", "An item with that name already exists", 409)
        target.rename(destination)
        try:
            from app.services.media_service import MediaService
            MediaService.scan_and_index()
        except Exception:
            pass
        return success_response(FileService.get_item_info(destination.relative_to(Config.MEDIA_ROOT).as_posix()), "Item renamed successfully")
    except Exception as e:
        return error_response("RENAME_FAILED", str(e), 500)


@files_bp.route("/api/files", methods=["DELETE"])
@require_auth
def delete_file_or_folder():
    denied = require_admin()
    if denied:
        return denied
    path_arg = request.args.get("path")
    if not path_arg:
        data = request.get_json(silent=True) or {}
        path_arg = data.get("path", "")
    if not path_arg:
        return error_response("PATH_REQUIRED", "Parameter 'path' is required", 400)
    try:
        FileService.delete_item(path_arg)
        try:
            from app.services.media_service import MediaService
            MediaService.scan_and_index()
        except Exception:
            pass
        return success_response({"deleted_path": path_arg}, "Item deleted successfully")
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e), 404)
    except PermissionError as e:
        return error_response("PERMISSION_DENIED", str(e), 403)
    except Exception as e:
        return error_response("DELETE_FAILED", str(e), 500)
