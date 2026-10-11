"""
MediaController
-----------------
The media library, modelled on Drupal's: browse uploaded media as a grid, filter by type, search by
name, upload new files, edit an item's name / alt text / published flag (and replace its file), and
delete. Every route requires the "admin.media.manage" permission (see the @route decorators below).

    GET        /admin/media                  the library (?type=image|document|audio|video, ?q=, ?page=)
    GET/POST   /admin/media/new              upload one or several files (multipart form)
    GET/POST   /admin/media/<mid>/edit       edit an item; optionally replace its file
    POST       /admin/media/<mid>/delete     delete the item and its stored file

A media item wraps a File record (core/models/Media.py); the uploading, validation and storing is
all in core/services/MediaService.py, and the files are served by UploadController at /uploads/....
"""

from pathlib import Path
from urllib.parse import urlencode

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.models.File import File
from core.models.Media import Media
from core.services.MediaService import MediaService, MediaUploadError

PAGE_SIZE = 24


class MediaController(ControllerBase):

    # ------------------------------------------------------------------ #
    # library
    # ------------------------------------------------------------------ #
    @route("/admin/media", methods=["GET"], permission=["admin.media.manage"])
    def index(self, request):
        db = self.db()
        service = MediaService(db)

        bundle = request.get("type") if request.get("type") in Media.BUNDLES else ""
        search = str(request.get("q") or "").strip()
        page = max(request.query.get_int("page", 1) or 1, 1)

        queryset = Media.objects(db).order_by("-created_at", "-mid")
        if bundle:
            queryset = queryset.filter(bundle=bundle)
        if search:
            queryset = queryset.filter(name__icontains=search)

        total = queryset.count()
        pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        page = min(page, pages)
        items = list(queryset[(page - 1) * PAGE_SIZE: page * PAGE_SIZE])

        base_query = {k: v for k, v in (("type", bundle), ("q", search)) if v}

        def page_url(number):
            if number <= 1 and not base_query:
                return "/admin/media"
            return "/admin/media?" + urlencode({**base_query, "page": number})

        return self.view(
            "admin:media/index",
            {
                "rows": self._rows(items, service),
                "bundle": bundle,
                "search": search,
                "counts": {b: Media.objects(db).filter(bundle=b).count() for b in Media.BUNDLES},
                "all_count": Media.objects(db).count(),
                "total": total,
                "page": page,
                "pages": pages,
                "prev_url": page_url(page - 1) if page > 1 else None,
                "next_url": page_url(page + 1) if page < pages else None,
                "type_urls": {b: "/admin/media?" + urlencode({**({"q": search} if search else {}), "type": b}) for b in Media.BUNDLES},
                "all_url": "/admin/media" + ("?" + urlencode({"q": search}) if search else ""),
                "uploaded": request.query.get_int("uploaded", 0),
                "deleted": bool(request.get("deleted")),
                "active_nav": "media",
            },
            request=request,
        )

    # ------------------------------------------------------------------ #
    # upload
    # ------------------------------------------------------------------ #
    @route("/admin/media/new", methods=["GET", "POST"], permission=["admin.media.manage"])
    def create(self, request):
        service = MediaService(self.db())

        if request.is_method("POST"):
            uploads = request.files.get_uploaded_files("files")
            published = bool(request.get("status"))

            if not uploads:
                return self._upload_form(request, service, problems=["Choose at least one file to upload."], published=published)

            actor = self.require_auth(request)
            created, problems = [], []
            for upload in uploads:
                try:
                    created.append(service.create(upload, uid=actor.uid, published=published))
                except MediaUploadError as exc:
                    problems.append(str(exc))

            if created and not problems:
                if len(created) == 1:  # like Drupal: straight to the edit form to add alt text
                    return self.redirect(f"/admin/media/{created[0].mid}/edit?created=1")
                return self.redirect(f"/admin/media?uploaded={len(created)}")

            # Some or all were rejected: stay on the form and say exactly why (and what did go through).
            return self._upload_form(request, service, problems=problems, uploaded=len(created), published=published)

        return self._upload_form(request, service, published=True)

    # ------------------------------------------------------------------ #
    # edit
    # ------------------------------------------------------------------ #
    @route("/admin/media/<mid>/edit", methods=["GET", "POST"], permission=["admin.media.manage"])
    def edit(self, request):
        media = self._find(request)
        if media is None:
            return self.not_found("Media item not found")
        service = MediaService(self.db())

        if request.is_method("POST"):
            validator = self.validate(request, {"name": "required|min:1|max:255", "alt": "max:512"})
            errors = validator.get_errors() if validator.fails() else {}
            data = validator.validated()

            replacement = request.files.get_uploaded_file("file")
            if not errors and replacement is not None:
                try:
                    service.replace_file(media, replacement, uid=self.require_auth(request).uid)
                except MediaUploadError as exc:
                    errors["file"] = [str(exc)]

            if errors:
                return self._edit_form(request, media, service, errors=errors, old=request.all())

            media.name = data["name"]
            if media.bundle == "image":
                media.alt = data.get("alt") or None
            media.status = bool(request.get("status"))
            media.save()
            return self.redirect(f"/admin/media/{media.mid}/edit?saved=1")

        return self._edit_form(request, media, service)

    # ------------------------------------------------------------------ #
    # delete
    # ------------------------------------------------------------------ #
    @route("/admin/media/<mid>/delete", methods=["POST"], permission=["admin.media.manage"])
    def delete(self, request):
        media = self._find(request)
        if media is None:
            return self.not_found("Media item not found")

        MediaService(self.db()).delete(media)
        return self.redirect("/admin/media?deleted=1")

    # ------------------------------------------------------------------ #
    def _find(self, request):
        return Media.find(self.db(), request.get_route_parameter("mid"))

    def _rows(self, items, service):
        """Library rows: each media item paired with its file, URL and display bits."""
        fids = [item.fid for item in items]
        files = {f.fid: f for f in File.objects(self.db()).filter(fid__in=fids)} if fids else {}
        rows = []
        for item in items:
            file_record = files.get(item.fid)
            rows.append(self._row(item, file_record, service))
        return rows

    @staticmethod
    def _row(item, file_record, service):
        return {
            "media": item,
            "file": file_record,
            "url": service.url(file_record),
            "size": service.human_size(file_record.filesize) if file_record else "-",
            "extension": Path(file_record.filename).suffix.lstrip(".").upper() if file_record else "?",
        }

    def _upload_form(self, request, service, problems=None, uploaded=0, published=True):
        return self.view(
            "admin:media/create",
            {
                "problems": problems or [],
                "uploaded": uploaded,
                "published": published,
                "accept": service.accept_attribute(),
                "allowed": service.allowed,
                "max_mb": service.max_megabytes,
                "active_nav": "media",
            },
            request=request,
        )

    def _edit_form(self, request, media, service, errors=None, old=None):
        file_record = media.file()
        context = self._row(media, file_record, service)
        context.update(
            errors=errors or {},
            old=old or {},
            accept=",".join(f".{ext}" for ext in service.allowed if service.bundle_for(f"x.{ext}") == media.bundle),
            published=bool(request.get("status")) if request.is_method("POST") else media.is_published,
            created=bool(request.get("created")),
            saved=bool(request.get("saved")),
            active_nav="media",
        )
        return self.view("admin:media/edit", context, request=request)
