"""
FileController
-----------------
Admin management for the file_managed table: list/edit/delete. There's no
create() here - a File record is meant to come from an actual upload
(StorageService.store()), not a bare form, so there's nothing sensible to
hand-create. Every route here requires the "files.manage" permission (see
Routing.py) - PermissionMiddleware enforces that before any of these methods
ever runs.
"""

from core.controllers.ControllerBase import ControllerBase
from core.models.File import File

EDITABLE_STATUSES = ("temporary", "permanent")


class FileController(ControllerBase):

    def index(self, request):
        files = list(File.objects(self.db()).filter(deleted_at__isnull=True).order_by("-created_at"))
        return self.view("files/index", {"files": files, "active_nav": "files"}, request=request)

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
                    "files/edit",
                    {"file": file_record, "errors": validator.get_errors(), "old": request.all(), "active_nav": "files"},
                    request=request,
                )

            data = validator.validated()
            file_record.filename = data["filename"]
            file_record.status = data["status"]
            file_record.save()
            return self.redirect("/admin/files")

        return self.view("files/edit", {"file": file_record, "errors": {}, "old": {}, "active_nav": "files"}, request=request)

    def delete(self, request):
        file_record = self._find_or_404(request)
        if file_record is None:
            return self.not_found("File not found")

        file_record.delete()  # soft delete - see File.delete(); doesn't remove the underlying stored file
        return self.redirect("/admin/files")

    # ------------------------------------------------------------------ #
    def _find_or_404(self, request):
        return File.find(self.db(), request.get_route_parameter("fid"))
