"""
AccessService
---------------
Everything about roles and permissions that touches the database:

    reading        roles(), permissions(), grouped_permissions(), grants(), role_defaults()
    the matrix     save_matrix()          - the admin permissions page (roles x permissions)
    custom perms   create_permission(), delete_permission()
    discovery      sync()                 - what `python console.py apps:discover` calls

How the pieces fit together
    * Apps declare roles/permissions in public/apps/<app>/permissions.py (validated and
      collected by core/apps/AppManager.py). Every key starts with the app's name.
    * sync() makes the roles/permissions tables match what was discovered: new ones are added,
      changed labels updated, and anything whose app is no longer registered (or no longer
      declares it) is removed. The grants matrix (role_permissions) is kept for everything
      that survives - an administrator's checkbox choices are not wiped by a re-discovery -
      and cleaned of references to anything that's gone.
    * A user's permissions are a JSON snapshot in users.permissions_raw, taken from their
      role when the account is created (see User.save()). sync() also scrubs those snapshots:
      permissions that no longer exist are dropped, and a user whose role no longer exists
      falls back to the default role.
    * Permissions an administrator adds by hand (Permission.source == "custom") survive
      discovery as long as the app they belong to is still registered.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from core.models.Permission import Permission
from core.models.Role import Role
from core.models.RolePermission import RolePermission
from core.models.User import User

# the part of a key after "<app>." - lowercase words joined by dots, e.g. "create" or "reports.export"
KEY_SUFFIX_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
MAX_KEY_LENGTH = 100


def as_list(value: Any) -> List[str]:
    """Form values arrive as None (absent), a str (one value) or a list (several) - normalise to a list."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


class AccessService:
    def __init__(self, db_connection):
        self.db = db_connection

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #
    def roles(self) -> List[Role]:
        return list(Role.objects(self.db).order_by("weight", "id"))

    def permissions(self) -> List[Permission]:
        return list(Permission.objects(self.db).order_by("app", "permission_key"))

    def grouped_permissions(self) -> List[Dict[str, Any]]:
        """[{"app": "admin", "permissions": [Permission, ...]}, ...] - the matrix rows, grouped per app."""
        groups: Dict[str, List[Permission]] = {}
        for permission in self.permissions():
            groups.setdefault(permission.app, []).append(permission)
        return [{"app": app, "permissions": items} for app, items in groups.items()]

    def grants(self) -> Dict[str, Set[str]]:
        """{role_key: {permission_key, ...}} - what's ticked in the matrix."""
        grants: Dict[str, Set[str]] = {}
        for row in list(RolePermission.objects(self.db).all()):
            grants.setdefault(row.role_key, set()).add(row.permission_key)
        return grants

    def role_defaults(self) -> Dict[str, List[str]]:
        """{role_key: [permission keys]} (["*"] for a superuser role) - what a new user of that role gets."""
        grants = self.grants()
        return {
            role.role_key: ["*"] if role.is_superuser else sorted(grants.get(role.role_key, set()))
            for role in self.roles()
        }

    # ------------------------------------------------------------------ #
    # the matrix
    # ------------------------------------------------------------------ #
    def save_matrix(self, posted: Dict[str, Iterable[str]], apply_to_users: bool = False) -> Dict[str, int]:
        """
        Replaces each (non-superuser) role's granted permissions with `posted[role_key]`.
        Unknown permission keys are ignored. With apply_to_users, the *difference* (what was added
        and what was revoked) is also applied to the stored permissions of existing users with that
        role - so a revoked permission really stops working, while any per-user edits an
        administrator made to other permissions are left alone.
        """
        valid = {permission.permission_key for permission in self.permissions()}
        current = self.grants()
        roles_changed = 0
        users_updated = 0

        for role in self.roles():
            if role.is_superuser:
                continue  # holds everything implicitly - nothing to store
            wanted = {key for key in posted.get(role.role_key, []) if key in valid}
            have = current.get(role.role_key, set())
            add, remove = wanted - have, have - wanted
            if not add and not remove:
                continue

            roles_changed += 1
            for key in sorted(add):
                RolePermission(self.db, role_key=role.role_key, permission_key=key).save()
            if remove:
                RolePermission.objects(self.db).filter(role_key=role.role_key, permission_key__in=sorted(remove)).delete()
            if apply_to_users:
                users_updated += self._apply_diff_to_users(role.role_key, add, remove)

        return {"roles_changed": roles_changed, "users_updated": users_updated}

    def _apply_diff_to_users(self, role_key: str, add: Set[str], remove: Set[str]) -> int:
        updated = 0
        for user in list(User.objects(self.db).filter(role=role_key)):
            current = set(User.parse_permissions(user.permissions_raw))
            if "*" in current:
                continue  # administrators hold everything already
            new = (current | add) - remove
            if new != current:
                User.objects(self.db).filter(uid=user.uid).update(permissions_raw=User.dump_permissions(new))
                updated += 1
        return updated

    # ------------------------------------------------------------------ #
    # custom (admin-created) permissions
    # ------------------------------------------------------------------ #
    def create_permission(self, app: str, name: str, label: str, description: Optional[str] = None) -> Permission:
        return Permission(
            self.db,
            permission_key=f"{app}.{name}",
            label=label,
            description=description or None,
            app=app,
            source="custom",
        ).save()

    def delete_permission(self, permission: Permission) -> None:
        """Removes a permission along with every grant of it and every user's stored copy of it."""
        key = permission.permission_key
        RolePermission.objects(self.db).filter(permission_key=key).delete()
        self._strip_from_users({key})
        permission.delete()

    def _strip_from_users(self, keys: Set[str]) -> None:
        for key in keys:
            for user in list(User.objects(self.db).filter(permissions_raw__contains=key)):
                current = User.parse_permissions(user.permissions_raw)
                kept = [p for p in current if p not in keys]
                if len(kept) != len(current):
                    User.objects(self.db).filter(uid=user.uid).update(permissions_raw=User.dump_permissions(kept))

    # ------------------------------------------------------------------ #
    # discovery sync - `python console.py apps:discover`
    # ------------------------------------------------------------------ #
    def sync(self, permissions: List[Dict[str, Any]], roles: List[Dict[str, Any]], registered_apps: Iterable[str]) -> Dict[str, int]:
        """
        Makes the permissions/roles tables (and the users that reference them) match what the
        registered apps declare. `permissions`/`roles` are the validated definitions from
        AppManager; `registered_apps` is app.py's REGISTERED_APPS.
        """
        defined_role_keys = {r["key"] for r in roles}
        missing = [key for key in (Role.ADMIN_KEY, Role.DEFAULT_KEY) if key not in defined_role_keys]
        if missing:
            # Refuse before touching anything: pruning without the built-in roles would demote
            # every administrator. They come from the "admin" app - keep it registered.
            raise ValueError(
                f"Role(s) {', '.join(missing)} aren't defined by any registered app - the built-in "
                f"'admin' app (public/apps/admin/permissions.py) must stay registered in app.py."
            )

        registered = set(registered_apps)
        summary = {
            "permissions_added": 0, "permissions_updated": 0, "permissions_removed": 0,
            "roles_added": 0, "roles_updated": 0, "roles_removed": 0,
            "grants_added": 0, "grants_removed": 0,
            "users_updated": 0, "users_role_reset": 0, "user_permissions_removed": 0,
        }

        # ---- permissions --------------------------------------------------------------
        existing = {p.permission_key: p for p in self.permissions()}
        defined_perm_keys = {d["key"] for d in permissions}
        new_perm_keys: Set[str] = set()

        for definition in permissions:
            row = existing.get(definition["key"])
            if row is None:
                Permission(
                    self.db, permission_key=definition["key"], label=definition["label"],
                    description=definition["description"], app=definition["app"], source="app",
                ).save()
                new_perm_keys.add(definition["key"])
                summary["permissions_added"] += 1
            elif self._update_fields(row, label=definition["label"], description=definition["description"],
                                     app=definition["app"], source="app"):
                summary["permissions_updated"] += 1

        kept_custom: Set[str] = set()
        for key, row in existing.items():
            if key in defined_perm_keys:
                continue
            if row.is_custom and row.app in registered:
                kept_custom.add(key)  # added by an administrator; its app is still registered
            else:
                row.delete()
                summary["permissions_removed"] += 1
        final_perm_keys = defined_perm_keys | kept_custom

        # ---- roles ----------------------------------------------------------------------
        existing_roles = {r.role_key: r for r in self.roles()}
        new_role_keys: Set[str] = set()

        for weight, definition in enumerate(roles):
            row = existing_roles.get(definition["key"])
            if row is None:
                Role(
                    self.db, role_key=definition["key"], label=definition["label"],
                    description=definition["description"], app=definition["app"],
                    is_superuser=definition["superuser"], weight=weight,
                ).save()
                new_role_keys.add(definition["key"])
                summary["roles_added"] += 1
            elif self._update_fields(row, label=definition["label"], description=definition["description"],
                                     app=definition["app"], weight=weight, is_superuser=definition["superuser"]):
                summary["roles_updated"] += 1

        for key, row in existing_roles.items():
            if key not in defined_role_keys:
                row.delete()
                summary["roles_removed"] += 1

        # ---- grants (the matrix) ---------------------------------------------------------
        superuser_keys = {d["key"] for d in roles if d["superuser"]}
        stale_ids: List[Any] = []
        present: Set[tuple] = set()
        for row in list(RolePermission.objects(self.db).all()):
            if (row.role_key not in defined_role_keys
                    or row.permission_key not in final_perm_keys
                    or row.role_key in superuser_keys):
                stale_ids.append(row.id)
            else:
                present.add((row.role_key, row.permission_key))
        if stale_ids:
            RolePermission.objects(self.db).filter(id__in=stale_ids).delete()
            summary["grants_removed"] = len(stale_ids)

        # A role's declared default grants apply when the role or the permission is brand new -
        # never again after that, so an administrator's later checkbox changes stick.
        for definition in roles:
            if definition["superuser"]:
                continue
            for permission_key in definition["permissions"]:
                is_new_pair = definition["key"] in new_role_keys or permission_key in new_perm_keys
                if permission_key in final_perm_keys and is_new_pair and (definition["key"], permission_key) not in present:
                    RolePermission(self.db, role_key=definition["key"], permission_key=permission_key).save()
                    present.add((definition["key"], permission_key))
                    summary["grants_added"] += 1

        # ---- users -----------------------------------------------------------------------
        for user in list(User.objects(self.db).all()):
            raw = user.permissions_raw
            original = User.parse_permissions(raw)
            role_reset = user.role not in defined_role_keys
            candidates = [p for p in original if not (role_reset and p == "*")]
            kept = [p for p in candidates if p == "*" or p in final_perm_keys]

            changes: Dict[str, Any] = {}
            if role_reset:
                changes["role"] = Role.DEFAULT_KEY
                summary["users_role_reset"] += 1
            if set(kept) != set(original) or (raw and raw != User.dump_permissions(kept)):
                changes["permissions_raw"] = User.dump_permissions(kept)
                summary["user_permissions_removed"] += len(set(original) - set(kept))
            if changes:
                User.objects(self.db).filter(uid=user.uid).update(**changes)
                summary["users_updated"] += 1

        return summary

    @staticmethod
    def _update_fields(row, **values) -> bool:
        """Sets whichever of `values` differ from the row (comparing booleans loosely, since a
        BOOLEAN column comes back as 0/1) and saves once. Returns True if anything changed."""
        changed = False
        for name, value in values.items():
            current = row.attributes.get(name)
            if isinstance(value, bool):
                current = bool(current)
            if current != value:
                row.attributes[name] = value
                changed = True
        if changed:
            row.save()
        return changed
