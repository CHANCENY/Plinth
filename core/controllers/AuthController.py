"""
AuthController
----------------
Handles the standard auth flows: login, registration, forgot/reset password,
and "social login" via Google/GitHub/Microsoft OAuth2. Extends ControllerBase,
so db()/views()/env()/session()/auth()/validate()/view()/redirect()/json()
are all already available - see core/controllers/ControllerBase.py.

Routes (added to core/http/routing/Routing.py):
    GET/POST  /login
    GET/POST  /register
    GET       /logout
    GET/POST  /forgot-password
    GET/POST  /reset-password/<token>
    GET       /auth/<provider>/redirect
    GET       /auth/<provider>/callback

Each GET/POST pair is one route mapped to one method here, which branches on
request.is_method("POST") - the same pattern PageController already uses for
"/pages" -> PageController::index (show vs. process are one method, not two).

Setup before any of this works:
    User.create_table(db)
    PasswordResetService.create_table(db)
    OAuthIdentity.create_table(db)

OAuth account linking: on a provider callback, an existing oauth_identities
row for that (provider, provider_user_id) logs the linked user straight in.
Otherwise a User is looked up by the email the provider returned, or created
if there isn't one yet - OAuth-created accounts get a random, never-shown
password (since users.password is NOT NULL in the given schema) and are
marked active + verified immediately, since the provider already verified
the email address.
"""

import secrets

from core.controllers.ControllerBase import ControllerBase
from core.models.OAuthIdentity import OAuthIdentity
from core.models.User import User
from core.services.AuthService import AuthService
from core.services.MailService import MailService
from core.services.OAuthService import OAuthService
from core.services.PasswordResetService import PasswordResetService


class AuthController(ControllerBase):
    OAUTH_STATE_KEY = "_oauth_state"

    def __init__(self):
        super().__init__()
        self._oauth = None

    def oauth(self) -> OAuthService:
        if self._oauth is None:
            self._oauth = OAuthService(env=self.env())
        return self._oauth

    # ------------------------------------------------------------------ #
    # login
    # ------------------------------------------------------------------ #
    def login(self, request):
        auth = self.auth(request)
        if auth.check():
            return self._finish_auth(auth, "/admin")  # already signed in - don't show a login form

        if request.is_method("POST"):
            validator = self.validate(request, {"email": "required|email", "password": "required"})
            if validator.fails():
                return self._render_auth_form("auth/login", request, errors=validator.get_errors(), old=request.all())

            data = validator.validated()
            if auth.attempt(data["email"], data["password"]):
                return self._finish_auth(auth, "/admin")

            return self._render_auth_form(
                "auth/login",
                request,
                errors={"email": ["Those credentials don't match an active account."]},
                old={"email": data["email"]},
            )

        return self._render_auth_form("auth/login", request)

    # ------------------------------------------------------------------ #
    # registration
    # ------------------------------------------------------------------ #
    def register(self, request):
        auth = self.auth(request)
        if auth.check():
            return self._finish_auth(auth, "/admin")  # already signed in - don't show a registration form

        if request.is_method("POST"):
            validator = self.validate(
                request,
                {
                    "name": "required|min:2",
                    "email": "required|email",
                    "password": "required|min:8|confirmed",
                },
            )
            if validator.fails():
                return self._render_auth_form("auth/register", request, errors=validator.get_errors(), old=request.all())

            data = validator.validated()

            if User.find_by_email(self.db(), data["email"]) is not None:
                return self._render_auth_form(
                    "auth/register",
                    request,
                    errors={"email": ["An account with that email already exists."]},
                    old={"name": data["name"], "email": data["email"]},
                )

            user = User(
                self.db(),
                name=data["name"],
                email=data["email"],
                password=data["password"],
                first_name=data["name"],
            )
            # The very first account ever registered becomes an admin, and active immediately -
            # otherwise there'd be no way to grant users.manage/files.manage to anyone, ever
            # (every /admin route needs a permission only an admin could hand out, and a
            # "pending" account can't even log in to hand it out). Every later signup keeps the
            # normal pending/member default and needs an admin to activate/promote them.
            if User.objects(self.db()).count() == 0:
                user.make_admin()
                user.set_status("active")
            user.save()

            return self._finish_auth(auth.login(user), "/admin")

        return self._render_auth_form("auth/register", request)

    # ------------------------------------------------------------------ #
    # logout
    # ------------------------------------------------------------------ #
    def logout(self, request):
        auth = self.auth(request)
        auth.logout()
        return self._finish_auth(auth, "/login")

    # ------------------------------------------------------------------ #
    # forgot password
    # ------------------------------------------------------------------ #
    def forgot_password(self, request):
        if request.is_method("POST"):
            validator = self.validate(request, {"email": "required|email"})
            if validator.fails():
                return self._render_auth_form(
                    "auth/forgot-password", request, errors=validator.get_errors(), old=request.all()
                )

            email = validator.validated()["email"]
            user = User.find_by_email(self.db(), email)
            if user is not None:
                token = PasswordResetService(self.db(), env=self.env()).create(email)
                self._send_reset_email(user, token)

            # Same response whether or not the account exists - never confirm/deny a registered email.
            return self.view("auth/forgot-password", {"sent": True}, request=request)

        return self._render_auth_form("auth/forgot-password", request)

    def _send_reset_email(self, user, token: str) -> None:
        base_url = self.env().get("APP_URL", "").rstrip("/")
        reset_url = f"{base_url}/reset-password/{token}?email={user.email}"
        MailService(env=self.env()).send(
            to=user.email,
            subject="Reset your password",
            body=f"Reset your password using this link: {reset_url}\nThis link expires in 1 hour.",
            html=f'<p>Reset your password using <a href="{reset_url}">this link</a>.</p>'
            f"<p>This link expires in 1 hour.</p>",
        )

    # ------------------------------------------------------------------ #
    # reset password
    # ------------------------------------------------------------------ #
    def reset_password(self, request):
        token = request.get_route_parameter("token")
        email = request.get("email", "")

        if request.is_method("POST"):
            validator = self.validate(request, {"email": "required|email", "password": "required|min:8|confirmed"})
            if validator.fails():
                return self._render_auth_form(
                    "auth/reset-password",
                    request,
                    errors=validator.get_errors(),
                    old={"email": email},
                    extra={"token": token},
                )

            data = validator.validated()
            resets = PasswordResetService(self.db(), env=self.env())

            user = User.find_by_email(self.db(), data["email"])
            if user is None or not resets.verify(data["email"], token):
                return self._render_auth_form(
                    "auth/reset-password",
                    request,
                    errors={"token": ["This reset link is invalid or has expired."]},
                    old={"email": data["email"]},
                    extra={"token": token},
                )

            user.set_password(data["password"]).save()
            resets.consume(data["email"])

            return self.redirect("/login")

        return self.view("auth/reset-password", {"token": token, "email": email}, request=request)

    # ------------------------------------------------------------------ #
    # oauth - step 1: send the user to the provider
    # ------------------------------------------------------------------ #
    def oauth_redirect(self, request):
        provider = request.get_route_parameter("provider")
        if not self.oauth().is_configured(provider):
            return self.error(f"'{provider}' sign-in is not configured.", status=501)

        session = self.session(request)
        state = secrets.token_urlsafe(24)
        session.set(self.OAUTH_STATE_KEY, state)

        response = self.redirect(self.oauth().authorize_url(provider, state))
        session.save(response)
        return response

    # ------------------------------------------------------------------ #
    # oauth - step 2: provider redirects back here with a code
    # ------------------------------------------------------------------ #
    def oauth_callback(self, request):
        provider = request.get_route_parameter("provider")
        session = self.session(request)

        expected_state = session.get(self.OAUTH_STATE_KEY)
        session.delete(self.OAUTH_STATE_KEY)

        code = request.get("code")
        state = request.get("state")

        if not code or not state or not expected_state or state != expected_state:
            response = self.error("Sign-in could not be verified - please try again.", status=400)
            session.save(response)
            return response

        try:
            profile = self.oauth().handle_callback(provider, code)
        except Exception as exc:
            response = self.error(f"Sign-in with {provider} failed: {exc}", status=502)
            session.save(response)
            return response

        if not profile.get("provider_user_id"):
            response = self.error(f"{provider} did not return a usable account id.", status=502)
            session.save(response)
            return response

        user = self._find_or_create_oauth_user(provider, profile)

        auth = AuthService(self.db(), session)
        auth.login(user)
        return self._finish_auth(auth, "/admin")

    def _find_or_create_oauth_user(self, provider: str, profile: dict) -> User:
        identity = OAuthIdentity.find_by_provider(self.db(), provider, profile["provider_user_id"])
        if identity is not None:
            return User.find(self.db(), identity.uid)

        email = profile.get("email")
        user = User.find_by_email(self.db(), email) if email else None

        if user is None:
            user = User(
                self.db(),
                name=profile.get("name") or email or f"{provider}_{profile['provider_user_id']}",
                email=email or f"{provider}-{profile['provider_user_id']}@users.noreply.local",
                password=secrets.token_urlsafe(32),  # unusable random password - this account signs in via OAuth only
                first_name=profile.get("name"),
            )
            user.set_status("active").mark_verified().save()

        OAuthIdentity(self.db(), uid=user.uid, provider=provider, provider_user_id=profile["provider_user_id"]).save()
        return user

    # ------------------------------------------------------------------ #
    # shared helpers
    # ------------------------------------------------------------------ #
    def _finish_auth(self, auth: AuthService, redirect_to: str):
        """Builds the redirect and makes sure the (possibly just-changed) session actually gets saved."""
        response = self.redirect(redirect_to)
        auth.session.save(response)
        return response

    def _render_auth_form(self, view: str, request, errors=None, old=None, extra=None):
        context = {"errors": errors or {}, "old": old or {}}
        context.update(extra or {})
        return self.view(view, context, request=request)
