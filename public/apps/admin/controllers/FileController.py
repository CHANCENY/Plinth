"""
FileController
-----------------
Admin management for the file_managed table: list/edit/delete - the low-level view of stored
files, like Drupal's Files screen. There's no create() here: a File record comes from an actual
upload, and uploading is done in the media library (/admin/media/new -> MediaController), which
creates the File and the media item that wraps it together. A file that a media item still uses
can't be deleted from here - delete the media item instead. Every route here requires the "admin.files.manage" permission (see
the @route decorators below) - PermissionMiddleware enforces that before any of these methods
ever runs.
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.models.File import File
from core.models.Media import Media

EDITABLE_STATUSES = ("temporary", "permanent")


class FileController(ControllerBase):

    @route("/admin/files", methods=["GET", "POST"], permission=["admin.files.manage"])
    def index(self, request):
        files = list(File.objects(self.db()).filter(deleted_at__isnull=True).order_by("-created_at"))
        return self.view("admin:files/index", {"files": files, "active_nav": "files"}, request=request)

    @route("/admin/files/<fid>/edit", methods=["GET", "POST"], permission=["admin.files.manage"])
    def edit(self, request):
        file_record = self._find_or_404(request)
        if file_record is None:
            return self.not_found("File not found")

        if request.is_method("POST"):
            validator = self.validate(
                request,
                {
                    "filename": "required|min:1",
                    "status": f"required|in:{','.join(EDITABLE_STATUSES)}",
                },
            )
            if validator.fails():
                return self.view(
                    "admin:files/edit",
                    {"file": file_record, "errors": validator.get_errors(), "old": request.all(), "active_nav": "files"},
                    request=request,
                )

            data = validator.validated()
            file_record.filename = data["filename"]
            file_record.status = data["status"]
            file_record.save()
            return self.redirect("/admin/files")

        return self.view("admin:files/edit", {"file": file_record, "errors": {}, "old": {}, "active_nav": "files"}, request=request)

    @route("/admin/files/<fid>/delete", methods=["POST"], permission=["admin.files.manage"])
    def delete(self, request):
        file_record = self._find_or_404(request)
        if file_record is None:
            return self.not_found("File not found")

        in_use = Media.using_file(self.db(), file_record.fid)
        if in_use is not None:
            return self.forbidden(
                f"This file is used by the media item '{in_use.name}' - delete that item in the media library instead."
            )

        file_record.delete()  # soft delete - see File.delete(); doesn't remove the underlying stored file
        return self.redirect("/admin/files")

    # ------------------------------------------------------------------ #
    def _find_or_404(self, request):
        return File.find(self.db(), request.get_route_parameter("fid"))
