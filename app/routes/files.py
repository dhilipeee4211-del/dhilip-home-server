"""Authenticated file and server-side download API."""
import os, urllib.parse
from flask import Blueprint, request, send_file
from app.services.file_service import FileService
from app.services.download_service import DownloadService
from app.utils.security import require_auth, success_response, error_response
from app.utils.config import Config

files_bp = Blueprint("files", __name__)

@files_bp.route("/api/files", methods=["GET"])
@files_bp.route("/api/files/list", methods=["GET"])
@require_auth
def list_files():
    try:
        return success_response(FileService.list_directory(request.args.get("path", "")))
    except FileNotFoundError as e: return error_response("NOT_FOUND", str(e), 404)
    except NotADirectoryError as e: return error_response("NOT_A_DIRECTORY", str(e), 400)
    except Exception as e: return error_response("LIST_FILES_ERROR", str(e), 500)

@files_bp.route("/api/files/info", methods=["GET"])
@require_auth
def get_file_info():
    path_arg=request.args.get("path", "")
    if not path_arg: return error_response("PARAM_REQUIRED", "Query parameter 'path' is required", 400)
    try: return success_response(FileService.get_item_info(path_arg))
    except FileNotFoundError as e: return error_response("FILE_NOT_FOUND", str(e), 404)
    except Exception as e: return error_response("FILE_INFO_ERROR", str(e), 500)

@files_bp.route("/api/files/download", methods=["GET"])
def download_file():
    from app.utils.security import get_request_token, verify_token
    valid,_,err=verify_token(get_request_token())
    if not valid: return error_response("AUTH_REQUIRED", f"Authentication failed: {err}", 401)
    path_arg=request.args.get("path", "")
    target=FileService.resolve_safe_path(path_arg) if path_arg else None
    if target is None or not target.is_file(): return error_response("FILE_NOT_FOUND", "File was not found or invalid path", 404)
    return send_file(str(target), as_attachment=True, download_name=target.name)

@files_bp.route("/api/files/upload", methods=["POST"])
@require_auth
def upload_file():
    if "file" not in request.files: return error_response("NO_FILE_UPLOADED", "No file found in multipart upload", 400)
    try: return success_response(FileService.save_uploaded_file(request.form.get("path", ""), request.files["file"]), "File uploaded successfully", 201)
    except Exception as e: return error_response("UPLOAD_FAILED", str(e), 500)

@files_bp.route("/api/files/folder", methods=["POST"])
@require_auth
def create_folder():
    data=request.get_json(silent=True) or {}
    name=data.get("name") or request.form.get("name", "")
    path=data.get("path") or request.form.get("path", "")
    if not name: return error_response("NAME_REQUIRED", "Folder name is required", 400)
    try: return success_response(FileService.create_folder(path,name), "Folder created successfully", 201)
    except FileExistsError as e: return error_response("FOLDER_EXISTS", str(e), 409)
    except Exception as e: return error_response("FOLDER_CREATE_FAILED", str(e), 400)

@files_bp.route("/api/files", methods=["DELETE"])
@require_auth
def delete_file_or_folder():
    data=request.get_json(silent=True) or {}
    path=request.args.get("path") or data.get("path", "")
    if not path: return error_response("PATH_REQUIRED", "Parameter 'path' is required", 400)
    try:
        FileService.delete_item(path); return success_response({"deleted_path":path}, "Item deleted successfully")
    except Exception as e: return error_response("DELETE_FAILED", str(e), 500)

# ---------- Server-side cloud downloads ----------
@files_bp.route("/api/files/remote-download", methods=["POST"])
@require_auth
def start_remote_download():
    data=request.get_json(silent=True) or {}
    url=str(data.get("url","")).strip()
    destination=str(data.get("destination") or data.get("destination_folder") or "/").strip()
    filename=str(data.get("filename","")).strip()
    if not url.lower().startswith(("http://","https://")): return error_response("INVALID_URL","Only HTTP/HTTPS URLs are supported",400)
    target_dir=FileService.resolve_safe_path(destination)
    if target_dir is None or not target_dir.is_dir(): return error_response("DESTINATION_NOT_FOUND","Destination folder does not exist inside MEDIA_ROOT",404)
    if not filename: filename=os.path.basename(urllib.parse.urlparse(url).path) or "download.bin"
    from werkzeug.utils import secure_filename
    filename=secure_filename(filename)
    if not filename: return error_response("INVALID_FILENAME","Invalid destination filename",400)
    job=DownloadService.create(url, destination, filename)
    return success_response(job,"Server download started",202)

@files_bp.route("/api/files/remote-download", methods=["GET"])
@require_auth
def remote_download_status():
    task_id=request.args.get("task_id","").strip()
    if task_id:
        job=DownloadService._get(task_id)
        if not job: return error_response("TASK_NOT_FOUND","Download task was not found",404)
        return success_response(job)
    return success_response({"tasks":DownloadService.list()})

@files_bp.route("/api/files/remote-download/pause", methods=["POST"])
@require_auth
def pause_remote_download():
    data=request.get_json(silent=True) or {}; tid=str(data.get("task_id") or data.get("id") or "").strip()
    job=DownloadService.control(tid,"pause") if tid else None
    return success_response(job,"Server download paused") if job else error_response("TASK_NOT_FOUND","Download task was not found",404)

@files_bp.route("/api/files/remote-download/resume", methods=["POST"])
@require_auth
def resume_remote_download():
    data=request.get_json(silent=True) or {}; tid=str(data.get("task_id") or data.get("id") or "").strip()
    job=DownloadService.control(tid,"resume") if tid else None
    return success_response(job,"Server download resumed") if job else error_response("TASK_NOT_FOUND","Download task was not found",404)

@files_bp.route("/api/files/remote-download/cancel", methods=["POST"])
@require_auth
def cancel_remote_download():
    data=request.get_json(silent=True) or {}; tid=str(data.get("task_id") or data.get("id") or "").strip()
    job=DownloadService.control(tid,"cancel") if tid else None
    return success_response(job,"Server download cancelled") if job else error_response("TASK_NOT_FOUND","Download task was not found",404)
