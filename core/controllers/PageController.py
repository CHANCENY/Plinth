from core.controllers import ControllerBase
from core.http.request import Request
from core.models import File, User


class PageController(ControllerBase):

    def index(self, request: Request):
        """The admin dashboard - a summary overview, not the users list (see UserController
        for that). Available to any logged-in user; the per-section links below respect
        whether the viewer actually has users.manage/files.manage (see partials/nav.html for
        the same pattern)."""
        db = self.db()
        stats = {
            "users_total": User.objects(db).exclude(status="deleted").count(),
            "users_active": User.objects(db).filter(status="active").count(),
            "users_pending": User.objects(db).filter(status="pending").count(),
            "files_total": File.objects(db).filter(deleted_at__isnull=True).count(),
        }
        return self.view("dashboard/index", {"stats": stats, "active_nav": "dashboard"}, request=request)
