"""
DhilipHome Server - File Management Routes
Provides secure directory traversal, metadata inspection, file upload, download, and deletion.
Strictly confined inside Config.MEDIA_ROOT.
"""

from flask import Blueprint, request, send_file, jsonify
from app.services.file_service import FileService
from app.utils.security import require_auth, success_response, error_response
from app.utils.config import Config

files_bp = Blueprint("files", __name__)


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


@files_bp.route("/api/files", methods=["DELETE"])
@require_auth
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
