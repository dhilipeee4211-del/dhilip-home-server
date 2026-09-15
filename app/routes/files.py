"""
DhilipHome Server - File Management Routes.
All paths are relative to MEDIA_ROOT; a leading "/" is treated as UI notation,
never as the Linux filesystem root.
"""
import os
import urllib.parse
from flask import Blueprint, request, send_file
from app.services.file_service import FileService
from app.services.download_service import create, get_task, list_tasks, pause, resume, cancel
from app.utils.security import require_auth, success_response, error_response

files_bp = Blueprint("files", __name__)


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
        return error_response("LIST_FILES_ERROR", f"Unable to list files: {e}", 500)


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


@files_bp.route("/api/files/download", methods=["GET"])
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
    return send_file(str(target), as_attachment=True, download_name=target.name)


@files_bp.route("/api/files/upload", methods=["POST"])
@require_auth
def upload_file():
    if "file" not in request.files:
        return error_response("NO_FILE_UPLOADED", "No file found in multipart upload", 400)
    try:
        info = FileService.save_uploaded_file(request.form.get("path", ""), request.files["file"])
        return success_response(info, "File uploaded successfully", 201)
    except FileNotFoundError as e:
        return error_response("TARGET_DIR_NOT_FOUND", str(e), 404)
    except ValueError as e:
        return error_response("INVALID_UPLOAD", str(e), 400)
    except Exception as e:
        return error_response("UPLOAD_FAILED", f"File upload failed: {e}", 500)


@files_bp.route("/api/files/folder", methods=["POST"])
@require_auth
def create_folder():
    data = request.get_json(silent=True) or {}
    name = data.get("name") or request.form.get("name", "")
    parent = data.get("path") or request.form.get("path", "")
    if not name:
        return error_response("NAME_REQUIRED", "Folder name is required", 400)
    try:
        return success_response(FileService.create_folder(parent, name), "Folder created successfully", 201)
    except FileExistsError as e:
        return error_response("FOLDER_EXISTS", str(e), 409)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FOLDER_CREATE_ERROR", str(e), 400)
    except Exception as e:
        return error_response("FOLDER_CREATE_FAILED", str(e), 500)


@files_bp.route("/api/files", methods=["DELETE"])
@require_auth
def delete_file_or_folder():
    data = request.get_json(silent=True) or {}
    path_arg = request.args.get("path") or data.get("path", "")
    if not path_arg:
        return error_response("PATH_REQUIRED", "Parameter 'path' is required", 400)
    try:
        FileService.delete_item(path_arg)
        return success_response({"deleted_path": path_arg}, "Item deleted successfully")
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e), 404)
    except PermissionError as e:
        return error_response("PERMISSION_DENIED", str(e), 403)
    except Exception as e:
        return error_response("DELETE_FAILED", str(e), 500)


# One canonical server-side download API. Android must use only these routes.
@files_bp.route("/api/files/remote-download", methods=["POST"])
@require_auth
def start_remote_download():
    data = request.get_json(silent=True) or {}
    try:
        task = create(
            url=str(data.get("url", "")).strip(),
            destination=str(data.get("destination") or data.get("destination_folder") or data.get("path") or "/"),
            filename=str(data.get("filename", "")).strip(),
        )
        return success_response(task, "Server download started", 202)
    except ValueError as e:
        return error_response("INVALID_DOWNLOAD", str(e), 400)
    except FileNotFoundError as e:
        return error_response("DESTINATION_NOT_FOUND", str(e), 404)
    except Exception as e:
        return error_response("DOWNLOAD_START_FAILED", str(e), 500)


@files_bp.route("/api/files/remote-download", methods=["GET"])
@require_auth
def remote_downloads():
    return success_response({"tasks": list_tasks()})


@files_bp.route("/api/files/remote-download/<task_id>", methods=["GET"])
@require_auth
def remote_download_status(task_id):
    task = get_task(task_id)
    if not task:
        return error_response("TASK_NOT_FOUND", "Download task was not found", 404)
    return success_response(task)


def _control(action, task_id):
    try:
        task = {"pause": pause, "resume": resume, "cancel": cancel}[action](task_id)
        return success_response(task, f"Download {action} request accepted")
    except KeyError:
        return error_response("TASK_NOT_FOUND", "Download task was not found", 404)
    except Exception as e:
        return error_response("DOWNLOAD_CONTROL_FAILED", str(e), 400)


@files_bp.route("/api/files/remote-download/pause", methods=["POST"])
@require_auth
def pause_remote():
    data = request.get_json(silent=True) or {}
    return _control("pause", str(data.get("task_id") or data.get("id") or "").strip())


@files_bp.route("/api/files/remote-download/resume", methods=["POST"])
@require_auth
def resume_remote():
    data = request.get_json(silent=True) or {}
    return _control("resume", str(data.get("task_id") or data.get("id") or "").strip())


@files_bp.route("/api/files/remote-download/cancel", methods=["POST"])
@require_auth
def cancel_remote():
    data = request.get_json(silent=True) or {}
    return _control("cancel", str(data.get("task_id") or data.get("id") or "").strip())
