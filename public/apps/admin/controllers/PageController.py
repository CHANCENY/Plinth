from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.http.request import Request
from core.models import File, User


class PageController(ControllerBase):

    @route("/", methods=["GET", "POST"])
    @route("/admin", methods=["GET", "POST"], permission=["authenticated"])
    def index(self, request: Request):
        """The admin dashboard - a summary overview, not the users list (see UserController
        for that). Available to any logged-in user; the per-section links below respect
        whether the viewer actually has admin.users.manage/admin.files.manage (see partials/nav.html for
        the same pattern)."""
        db = self.db()
        stats = {
            "users_total": User.objects(db).exclude(status="deleted").count(),
            "users_active": User.objects(db).filter(status="active").count(),
            "users_pending": User.objects(db).filter(status="pending").count(),
            "files_total": File.objects(db).filter(deleted_at__isnull=True).count(),
        }
        return self.view("admin:dashboard/index", {"stats": stats, "active_nav": "dashboard"}, request=request)
