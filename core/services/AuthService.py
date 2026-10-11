"""
AuthService
------------
Wires the User model and SessionService together into a login/logout/
"who's currently logged in" API. This is the piece Routing/Server are
missing: AuthenticationMiddleware and PermissionMiddleware both take a
current_user argument, but nothing in the framework currently resolves one
from the request - Routing's current_user defaults to None and nothing ever
calls set_current_user(). AuthService.user() is what should feed that.

Project layout assumed - same as the other services:
    project/
        core/
            services/
                AuthService.py   <- this file

Usage
-----
    from core.database.DatabaseConnection import DatabaseConnection
    from core.services.SessionService import SessionService
    from core.services.AuthService import AuthService

    db = DatabaseConnection()
    session = SessionService(db).start(request)
    auth = AuthService(db, session)

    # logging in
    if auth.attempt(email, password):
        ...
    else:
        ...  # bad credentials

    # anywhere later in the same request
    if auth.check():
        current_user = auth.user()

    # logging out
    auth.logout()

    session.save(response)   # always save the session so login/logout state persists

Wiring into the request lifecycle (in Server._handle_request, before
routing.dispatch()):

    session = SessionService(db).start(request)
    auth = AuthService(db, session)
    routing.set_current_user(auth.user())
    ...
    response = routing.dispatch()
    session.save(response)
"""

from typing import Optional

from core.models.User import User


class AuthService:
    SESSION_KEY = "_auth_uid"

    def __init__(self, db_connection, session):
        """
        db_connection: a DatabaseConnection instance (mysql/postgres/sqlite).
        session:       a SessionService instance that has already had .start(request) called on it.
        """
        self.db = db_connection
        self.session = session
        self._user: Optional[User] = None
        self._resolved = False

    # ------------------------------------------------------------------ #
    # authenticating
    # ------------------------------------------------------------------ #
    def attempt(self, email: str, password: str) -> bool:
        """Checks credentials via User.authenticate() and logs in on success."""
        user = User.authenticate(self.db, email, password)
        if user is None:
            return False
        self.login(user)
        return True

    def login(self, user: User) -> "AuthService":
        """Logs in an already-loaded User instance directly (e.g. right after registration)."""
        self.session.set(self.SESSION_KEY, user.uid)
        self.session.regenerate()  # new session id on privilege change - avoids session fixation
        self._user = user
        self._resolved = True
        return self

    def logout(self) -> "AuthService":
        self.session.delete(self.SESSION_KEY)
        self.session.regenerate()
        self._user = None
        self._resolved = True
        return self

    # ------------------------------------------------------------------ #
    # current user
    # ------------------------------------------------------------------ #
    def user(self) -> Optional[User]:
        """Returns the currently logged-in User, or None. Resolved once per request and memoized."""
        if self._resolved:
            return self._user

        uid = self.session.get(self.SESSION_KEY)
        self._user = User.find(self.db, uid) if uid is not None else None
        self._resolved = True
        return self._user

    def check(self) -> bool:
        return self.user() is not None

    def guest(self) -> bool:
        return not self.check()

    def id(self):
        user = self.user()
        return user.uid if user else None
