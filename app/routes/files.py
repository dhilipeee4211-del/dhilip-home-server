"""
DhilipHome Server - File Management Routes
Provides secure directory traversal, metadata inspection, file upload, download, and deletion.
Strictly confined inside Config.MEDIA_ROOT.
"""

from flask import Blueprint, request, send_file, jsonify
import os
import threading
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from werkzeug.utils import secure_filename
from app.services.file_service import FileService
from app.services.media_service import MediaService
from app.utils.security import require_auth, require_admin, success_response, error_response
from app.utils.config import Config

files_bp = Blueprint("files", __name__)

_remote_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dhilip-remote-download")
_remote_lock = threading.Lock()
_remote_tasks = {}

def _remote_download_worker(task_id, url, destination, filename):
    target_dir = FileService.resolve_safe_path(destination)
    if target_dir is None or not target_dir.is_dir():
        raise FileNotFoundError("Destination folder does not exist inside MEDIA_ROOT")
    target = target_dir / secure_filename(filename)
    if not target.name:
        raise ValueError("Invalid filename")

    tmp = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "DhilipHome-Server/0.1"})
    started = time.monotonic()
    last_sample_time = started
    last_sample_bytes = 0
    with urllib.request.urlopen(req, timeout=30) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with _remote_lock:
            _remote_tasks[task_id].update(status="downloading", total_bytes=total)
        while True:
            with _remote_lock:
                current = _remote_tasks.get(task_id, {})
                if current.get("cancel_requested"):
                    raise InterruptedError("Cancelled by user")
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            with _remote_lock:
                task = _remote_tasks.get(task_id)
                if task:
                    task["downloaded_bytes"] = downloaded
                    task["progress_percent"] = int(downloaded * 100 / total) if total else min(99, task.get("progress_percent", 0) + 1)
                    now = time.monotonic()
                    elapsed = max(now - last_sample_time, 0.001)
                    if now - last_sample_time >= 0.5:
                        bps = max(0, int((downloaded - last_sample_bytes) / elapsed))
                        task["speed_bps"] = bps
                        task["speed"] = f"{bps / 1024 / 1024:.2f} MB/s" if bps >= 1024 * 1024 else f"{bps / 1024:.1f} KB/s" if bps >= 1024 else f"{bps} B/s"
                        last_sample_time = now
                        last_sample_bytes = downloaded
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, target)
    rel_path = target.relative_to(Config.MEDIA_ROOT).as_posix()
    try:
        MediaService.index_file(rel_path)
    except Exception:
        pass
    with _remote_lock:
        _remote_tasks[task_id].update(status="completed", progress_percent=100,
                                      downloaded_bytes=target.stat().st_size, speed_bps=0, speed="Complete", path=rel_path)

def _run_remote_download(task_id, url, destination, filename):
    try:
        _remote_download_worker(task_id, url, destination, filename)
    except Exception as exc:
        try:
            target_dir = FileService.resolve_safe_path(destination)
            if target_dir:
                partial = target_dir / (secure_filename(filename) + ".part")
                if partial.exists(): partial.unlink()
        except Exception:
            pass
        with _remote_lock:
            if task_id in _remote_tasks:
                _remote_tasks[task_id].update(status="cancelled" if isinstance(exc, InterruptedError) else "failed", error="Cancelled by user" if isinstance(exc, InterruptedError) else str(exc))



@files_bp.route("/api/files/remote-download", methods=["POST"])
@require_auth
def start_remote_download():
    """Download a URL from the Internet directly onto the home server."""
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    destination = str(data.get("destination", "") or "").strip() or "/"
    filename = str(data.get("filename", "") or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")):
        return error_response("INVALID_URL", "Only HTTP/HTTPS URLs are supported", 400)
    if not filename:
        filename = os.path.basename(urllib.parse.urlparse(url).path) or "download.bin"
    filename = secure_filename(filename)
    if not filename:
        return error_response("INVALID_FILENAME", "Invalid destination filename", 400)
    if FileService.resolve_safe_path(destination) is None or not FileService.resolve_safe_path(destination).is_dir():
        return error_response("DESTINATION_NOT_FOUND", "Destination folder does not exist", 404)
    import uuid
    task_id = "srvdl_" + uuid.uuid4().hex[:10]
    with _remote_lock:
        _remote_tasks[task_id] = {"task_id": task_id, "status": "queued", "progress_percent": 0,
                                  "downloaded_bytes": 0, "total_bytes": 0, "filename": filename,
                                  "destination": destination, "url": url, "speed_bps": 0, "speed": "Starting…", "started_at": time.time()}
    _remote_executor.submit(_run_remote_download, task_id, url, destination, filename)
    return success_response(_remote_tasks[task_id], "Server download started", 202)

@files_bp.route("/api/files/remote-download/<task_id>/cancel", methods=["POST"])
@require_auth
def cancel_remote_download(task_id):
    with _remote_lock:
        task = _remote_tasks.get(task_id)
        if not task:
            return error_response("TASK_NOT_FOUND", "Download task was not found", 404)
        if task.get("status") in ("completed", "failed", "cancelled"):
            return success_response(dict(task), "Download already finished")
        task["cancel_requested"] = True
        task["status"] = "cancelling"
    return success_response({"task_id": task_id, "status": "cancelling"}, "Cancellation requested")

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

@files_bp.route("/api/files", methods=["GET"])
@files_bp.route("/api/files/list", methods=["GET"])
@require_auth
def list_files():
    """
    List directory contents inside MEDIA_ROOT (Section 10).
    Query parameter: path (relative path from MEDIA_ROOT).
    """
    path_arg = request.args.get("path", "")
    try:
        listing = FileService.list_directory(path_arg)
        return success_response(listing)
    except FileNotFoundError as e:
        return error_response("NOT_FOUND", str(e), 404)
    except NotADirectoryError as e:
        return error_response("NOT_A_DIRECTORY", str(e), 400)
    except Exception as e:
        return error_response("LIST_FILES_ERROR", f"Unable to list files: {str(e)}", 500)


@files_bp.route("/api/files/info", methods=["GET"])
@require_auth
def get_file_info():
    """
    Get detailed metadata for a file or folder.
    Query parameter: path.
    """
    path_arg = request.args.get("path", "")
    if not path_arg:
        return error_response("PARAM_REQUIRED", "Query parameter 'path' is required", 400)

    try:
        info = FileService.get_item_info(path_arg)
        return success_response(info)
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e), 404)
    except Exception as e:
        return error_response("FILE_INFO_ERROR", str(e), 500)


@files_bp.route("/api/files/download", methods=["GET"])
def download_file():
    """
    Download a file from MEDIA_ROOT.
    Requires authentication via Bearer token or 'token' query param.
    """
    # Verify auth manually to allow direct download links with ?token=...
    from app.utils.security import get_request_token, verify_token
    token = get_request_token()
    is_valid, _, err = verify_token(token)
    if not is_valid:
        return error_response("AUTH_REQUIRED", f"Authentication failed: {err}", 401)

    path_arg = request.args.get("path", "")
    if not path_arg:
        return error_response("PARAM_REQUIRED", "Query parameter 'path' is required", 400)

    target = FileService.resolve_safe_path(path_arg)
    if target is None or not target.is_file():
        return error_response("FILE_NOT_FOUND", "File was not found or invalid path", 404)

    return send_file(
        str(target),
        as_attachment=True,
        download_name=target.name,
    )


@files_bp.route("/api/files/upload", methods=["POST"])
@require_auth
def upload_file():
    """
    Upload a file to a destination directory inside MEDIA_ROOT.
    Form data:
    - file: file payload
    - path: parent relative directory (optional, default root)
    """
    if "file" not in request.files:
        return error_response("NO_FILE_UPLOADED", "No file found in multipart upload", 400)

    uploaded = request.files["file"]
    parent_path = request.form.get("path", "")

    try:
        info = FileService.save_uploaded_file(parent_path, uploaded)
        return success_response(info, "File uploaded successfully", 201)
    except FileNotFoundError as e:
        return error_response("TARGET_DIR_NOT_FOUND", str(e), 404)
    except ValueError as e:
        return error_response("INVALID_UPLOAD", str(e), 400)
    except Exception as e:
        return error_response("UPLOAD_FAILED", f"File upload failed: {str(e)}", 500)


@files_bp.route("/api/files/folder", methods=["POST"])
@require_auth
def create_folder():
    """
    Create a new directory inside MEDIA_ROOT.
    JSON body:
    - name: folder name (required)
    - path: parent relative path (optional, default root)
    """
    data = request.get_json(silent=True) or {}
    folder_name = data.get("name") or request.form.get("name", "")
    parent_path = data.get("path") or request.form.get("path", "")

    if not folder_name:
        return error_response("NAME_REQUIRED", "Folder name is required", 400)

    try:
        info = FileService.create_folder(parent_path, folder_name)
        return success_response(info, "Folder created successfully", 201)
    except FileExistsError as e:
        return error_response("FOLDER_EXISTS", str(e), 409)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FOLDER_CREATE_ERROR", str(e), 400)
    except Exception as e:
        return error_response("FOLDER_CREATE_FAILED", str(e), 500)


@files_bp.route("/api/files/rename", methods=["POST"])
@require_admin
def rename_file_or_folder():
    data = request.get_json(silent=True) or {}
    path_arg = str(data.get("path", "")).strip()
    new_name = str(data.get("name", "")).strip()
    if not path_arg or not new_name:
        return error_response("PARAM_REQUIRED", "Both path and name are required", 400)
    safe_name = secure_filename(new_name)
    if not safe_name:
        return error_response("INVALID_NAME", "Invalid destination name", 400)
    target = FileService.resolve_safe_path(path_arg)
    if target is None or not target.exists() or target == Config.MEDIA_ROOT:
        return error_response("FILE_NOT_FOUND", "Item not found or invalid path", 404)
    destination = target.parent / safe_name
    if not FileService.resolve_safe_path(destination.relative_to(Config.MEDIA_ROOT).as_posix()):
        return error_response("INVALID_PATH", "Destination is outside media root", 400)
    if destination.exists():
        return error_response("NAME_EXISTS", "An item with that name already exists", 409)
    target.rename(destination)
    return success_response(FileService.get_item_info(destination.relative_to(Config.MEDIA_ROOT).as_posix()), "Item renamed successfully")

@files_bp.route("/api/files", methods=["DELETE"])
@require_admin
def delete_file_or_folder():
    """
    Delete a file or directory from MEDIA_ROOT.
    Query param or JSON:
    - path: relative path to delete
    """
    path_arg = request.args.get("path")
    if not path_arg:
        data = request.get_json(silent=True) or {}
        path_arg = data.get("path", "")

    if not path_arg:
        return error_response("PATH_REQUIRED", "Parameter 'path' is required for deletion", 400)

    try:
        FileService.delete_item(path_arg)
        return success_response({"deleted_path": path_arg}, "Item deleted successfully")
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e), 404)
    except PermissionError as e:
        return error_response("PERMISSION_DENIED", str(e), 403)
    except Exception as e:
        return error_response("DELETE_FAILED", str(e), 500)
