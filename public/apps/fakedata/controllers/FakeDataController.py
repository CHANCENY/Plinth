"""
FakeDataController
--------------------
The dashboard front end for the fake-data engine (core/fakedata/service.py) - the same thing as
`python console.py fake:generate`, with checkboxes. Pick the models and how many rows, preview sample rows, generate,
and undo a run later.

    GET/POST  /admin/fake-data                       the page; POST with action=preview or action=generate
    POST      /admin/fake-data/runs/<run_id>/undo    delete what a run created

Switched OFF by default. It writes junk into your database, so it only works while FAKE_DATA_ENABLED=true in .env -
leave that off anywhere that holds real data. Administrators only, even when enabled.
"""

from core.controllers.ControllerBase import ControllerBase
from core.fakedata.generators import FakeOptions
from core.fakedata.service import FakeDataError, FakeDataService, database_id
from core.http.routing.route import route
from core.services.EnvService import EnvService

MAX_ROWS = 1000  # per model, per request - bigger loads belong on the command line
PREVIEW_ROWS = 5
LOCALES = [
    ("en_US", "English (US)"), ("en_GB", "English (UK)"), ("fr_FR", "French"), ("de_DE", "German"), ("es_ES", "Spanish"),
    ("it_IT", "Italian"), ("pt_BR", "Portuguese (Brazil)"), ("nl_NL", "Dutch"), ("ru_RU", "Russian"), ("ja_JP", "Japanese"),
    ("zh_CN", "Chinese"), ("ar_SA", "Arabic"),
]


def enabled() -> bool:
    return EnvService().get_bool("FAKE_DATA_ENABLED", False)


class FakeDataController(ControllerBase):

    @route("/admin/fake-data", methods=["GET", "POST"], permission=["administrator"])
    def index(self, request):
        if not request.is_method("POST"):
            return self._page(request)
        if not enabled():
            return self.forbidden("Fake data is switched off - set FAKE_DATA_ENABLED=true in .env (development only).")

        errors, targets, options = self._read_form(request)
        if errors:
            return self._page(request, errors=errors, old=request.all())

        service = FakeDataService(self.db(), options)
        try:
            service.check_ready()
            if request.get("action") == "preview":
                previews = [(model, count, service.preview(model, min(count, PREVIEW_ROWS))) for model, count in targets]
                return self._page(request, old=request.all(), previews=previews)
            result = service.generate(targets, note="dashboard")
            return self._page(request, result=result, password=options.password if "users" in result.created else None)
        except FakeDataError as exc:
            return self._page(request, errors={"__all__": str(exc)}, old=request.all())

    @route("/admin/fake-data/runs/<run_id>/undo", methods=["POST"], permission=["administrator"])
    def undo(self, request):
        if not enabled():
            return self.forbidden("Fake data is switched off - set FAKE_DATA_ENABLED=true in .env (development only).")
        try:
            result = FakeDataService(self.db()).undo(request.get_route_parameter("run_id"))
        except FakeDataError as exc:
            return self._page(request, errors={"__all__": str(exc)})
        return self._page(request, undone=result)

    # ------------------------------------------------------------------ #
    def _read_form(self, request):
        errors, targets = {}, []
        service = FakeDataService(self.db())
        for info in service.catalog():
            if not request.get(f"use_{info.table}"):
                continue
            raw = str(request.get(f"count_{info.table}") or "").strip()
            try:
                count = int(raw) if raw else info.default_count
                if not 1 <= count <= MAX_ROWS:
                    raise ValueError
            except ValueError:
                errors[f"count_{info.table}"] = f"Enter a number from 1 to {MAX_ROWS}."
                continue
            targets.append((info.model, count))
        if not targets and not errors:
            errors["__all__"] = "Tick at least one model."

        locale = str(request.get("locale") or "en_US")
        if locale not in dict(LOCALES):
            errors["locale"] = "Choose one of the listed languages."

        seed = None
        if str(request.get("seed") or "").strip():
            try:
                seed = int(str(request.get("seed")).strip())
            except ValueError:
                errors["seed"] = "The seed must be a whole number (or empty)."

        try:
            percent = float(request.get("null_percent") or 10)
            if not 0 <= percent <= 100:
                raise ValueError
        except ValueError:
            errors["null_percent"] = "Enter a percentage from 0 to 100."
            percent = 10

        password = str(request.get("password") or "password")
        if len(password) > 100:
            errors["password"] = "Keep it under 100 characters."

        options = FakeOptions(locale=locale, seed=seed, password=password, null_ratio=percent / 100,
                              create_related=bool(request.get("create_related")))
        return errors, targets, options

    def _page(self, request, errors=None, old=None, previews=None, result=None, password=None, undone=None):
        service = FakeDataService(self.db())
        is_on = enabled()
        return self.view(
            "fakedata:index",
            {
                "enabled": is_on,
                "faker_installed": service.faker_available(),
                "catalog": service.catalog() if is_on else [],
                "runs": service.runs() if is_on else [],
                "database": database_id(),
                "locales": LOCALES,
                "max_rows": MAX_ROWS,
                "errors": errors or {},
                "old": old or {},
                "previews": previews,
                "result": result,
                "password": password,
                "undone": undone,
                "first_visit": old is None,
            },
            request=request,
        )
