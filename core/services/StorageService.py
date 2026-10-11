"""
StorageService
----------------
Turns an UploadedFile (from Request.files, a FileBag) into a stored file on
disk plus a File model record - the piece connecting request.files to the
file_managed table. Nothing in the framework currently does this: FileBag
just hands back the raw UploadedFile, and the File model has no idea how
files actually get onto disk.

Project layout assumed - same as the other services:
    project/
        .env
        core/
            services/
                StorageService.py   <- this file
        public/
            uploads/                 <- default disk, web-accessible (public webroot)
        storage/
            app/                     <- default disk for files that shouldn't be web-accessible

Expected .env keys (all optional)
-----------------------------------
STORAGE_PUBLIC_PATH     default: <project_root>/public/uploads
STORAGE_PRIVATE_PATH    default: <project_root>/storage/app

Usage
-----
    from core.database.DatabaseConnection import DatabaseConnection
    from core.services.StorageService import StorageService

    db = DatabaseConnection()
    storage = StorageService(db)

    uploaded = request.files.get_uploaded_file("avatar")
    if uploaded and uploaded.is_valid():
        file_record = storage.store(uploaded, uid=current_user.uid, disk="public")
        avatar_url = storage.url(file_record)

    # later, e.g. when a user is deleted or replaces their avatar
    storage.delete(file_record)   # removes the row (soft-delete, like File.delete()) and the file on disk
"""

import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from core.models.File import File
from core.services.EnvService import EnvService

DISKS = ("public", "private")
UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageService:
    def __init__(self, db_connection, env: Optional[EnvService] = None):
        self.db = db_connection
        env = env or EnvService()

        public_path = env.get("STORAGE_PUBLIC_PATH")
        private_path = env.get("STORAGE_PRIVATE_PATH")

        self.paths = {
            "public": self._resolve_path(public_path, "public", "uploads"),
            "private": self._resolve_path(private_path, "storage", "app"),
        }

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _project_root(self) -> str:
        # this file lives at project/core/services/StorageService.py
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _resolve_path(self, configured: Optional[str], *default_parts: str) -> Path:
        if configured:
            path = Path(configured)
            return path if path.is_absolute() else Path(self._project_root()) / path
        return Path(self._project_root()).joinpath(*default_parts)

    # ------------------------------------------------------------------ #
    # storing
    # ------------------------------------------------------------------ #
    def store(self, uploaded_file, uid=None, disk: str = "public", subdirectory: str = "") -> File:
        """
        Moves an UploadedFile onto the given disk and creates its File record.
        disk: "public" (web-accessible, under public/uploads) or "private" (under storage/app).
        subdirectory: optional path segment under the disk root, e.g. "avatars".
        """
        if disk not in DISKS:
            raise ValueError(f"StorageService: unknown disk '{disk}'. Expected one of {DISKS}.")

        content = uploaded_file.get_content()
        checksum = hashlib.sha256(content).hexdigest()

        stored_name = self._generate_filename(uploaded_file.get_client_original_name())
        destination_dir = self.paths[disk] / subdirectory if subdirectory else self.paths[disk]
        destination = destination_dir / stored_name

        uploaded_file.move(destination)

        uri = (Path(disk) / subdirectory / stored_name if subdirectory else Path(disk) / stored_name).as_posix()

        record = File(
            self.db,
            uid=uid,
            filename=uploaded_file.get_client_original_name(),
            uri=uri,
            filemime=uploaded_file.get_client_mime_type(),
            filesize=uploaded_file.get_size() or len(content),
            checksum=checksum,
            status="permanent",
        )
        record.save()
        return record

    @staticmethod
    def _generate_filename(original_name: str) -> str:
        suffix = Path(original_name).suffix
        safe_stem = UNSAFE_FILENAME_RE.sub("-", Path(original_name).stem).strip("-") or "file"
        unique = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        return f"{safe_stem}-{unique}{suffix}"

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #
    def absolute_path(self, file_record: File) -> Path:
        """Resolves a File record's uri back to an absolute path on disk."""
        disk, _, relative = file_record.uri.partition("/")
        if disk not in DISKS:
            # uri wasn't written by this service (disk prefix missing/unrecognized) - treat as public
            return self.paths["public"] / file_record.uri
        return self.paths[disk] / relative

    def url(self, file_record: File, base_url: str = "") -> str:
        """Builds a public URL for a file stored on the 'public' disk. base_url e.g. 'https://myapp.com'."""
        disk, _, relative = file_record.uri.partition("/")
        if disk != "public":
            raise ValueError("StorageService.url(): file is not on the public disk")
        return f"{base_url.rstrip('/')}/uploads/{relative}"

    def read(self, file_record: File) -> bytes:
        return self.absolute_path(file_record).read_bytes()

    # ------------------------------------------------------------------ #
    # deleting
    # ------------------------------------------------------------------ #
    def delete(self, file_record: File, hard: bool = False) -> None:
        """
        Removes the file from disk and the File record. hard=True also hard-deletes the
        database row (File.hard_delete()); otherwise the row is soft-deleted (File.delete()),
        matching File's own default, and the row's history is kept.
        """
        path = self.absolute_path(file_record)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

        if hard:
            file_record.hard_delete()
        else:
            file_record.delete()
