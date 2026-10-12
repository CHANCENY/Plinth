# Plinth

**A small, batteries-included Python web framework - the base your apps stand on.**

Plinth gives you routing, an ORM with migrations, authentication, roles and permissions, an admin
dashboard and a media library, on top of Python's standard-library HTTP server and Jinja2. Your own
features live in self-contained **apps** that declare their routes, models, permissions and menu items
right next to the code that uses them.

- **Apps, not a monolith.** A feature is a folder (`public/apps/<name>/`) with controllers, models, views,
  permissions and menus. Register it in one line and everything is discovered.
- **Routes live on the method.** `@route("/notes/new", permission=["notes.write"])` - no central route table.
- **Django-style ORM.** Declare fields on a model, query with `filter(title__icontains="x")`, evolve the
  schema with generated migrations.
- **Auth included.** Registration, login, password reset, database-backed sessions and optional
  Google / GitHub / Microsoft sign-in.
- **Roles and permissions, Drupal-style.** Apps declare them; administrators tick a roles x permissions
  matrix; every account carries its own editable permission list.
- **Admin dashboard.** Users, permissions, media library and files - with a sidebar that apps extend.
- **Media library.** Validated uploads (image / document / audio / video), grid library, edit, replace, delete.
- **Few dependencies.** `python-dotenv`, `Jinja2`, and the driver for your database.

> **Status:** Plinth is a young framework built for development and learning. Read
> [Status and known limitations](#status-and-known-limitations) before putting anything on the internet.

---

## Contents

- [Quick start](#quick-start)
- [How a request flows](#how-a-request-flows)
- [Project layout](#project-layout)
- [Apps](#apps)
- [Routing and controllers](#routing-and-controllers)
- [Views](#views)
- [Models, queries and migrations](#models-queries-and-migrations)
- [Validation](#validation)
- [Authentication](#authentication)
- [Roles, permissions and menus](#roles-permissions-and-menus)
- [Media library and file storage](#media-library-and-file-storage)
- [Configuration](#configuration)
- [Command line](#command-line)
- [Tutorial: build an app](#tutorial-build-an-app)
- [Status and known limitations](#status-and-known-limitations)

---

## Quick start

**Requirements:** Python 3.10 or newer and a **MySQL or MariaDB** database. (Migrations currently run only on
MySQL / MariaDB - see [Status and known limitations](#status-and-known-limitations).)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env                    # then edit the DB_* settings
```

Create an empty database, then point `.env` at it:

```sql
CREATE DATABASE plinth CHARACTER SET utf8mb4;
```

```ini
DB_DRIVER=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=plinth
```

Then:

```bash
python console.py apps:discover        # find routes, models, permissions and menus
python console.py migrate              # create the tables
python console.py apps:discover        # again - now it can load roles & permissions into the database
python console.py serve                # http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000/register>. **The first account you register becomes the administrator** and is
active immediately. Accounts registered afterwards start as `pending` until an administrator activates
them (Admin -> Users). After signing in you land on the dashboard at `/admin`.

Built-in pages:

| URL | What |
|---|---|
| `/register`, `/login`, `/logout` | Account pages |
| `/forgot-password`, `/reset-password/<token>` | Password reset by email |
| `/auth/<provider>/redirect` and `/callback` | Google / GitHub / Microsoft sign-in |
| `/admin` | Dashboard |
| `/admin/users` | User management (permission `admin.users.manage`) |
| `/admin/permissions` | Roles x permissions matrix (**administrators only**) |
| `/admin/media` | Media library (permission `admin.media.manage`) |
| `/admin/files` | Stored-file records (permission `admin.files.manage`) |
| `/uploads/<path>` | Public uploaded files |
| `/blog` | Example app |

---

## How a request flows

```
 browser
    |
    v
 core/http/http/Server.py      http.server handler: builds a Request, opens the DB connection,
    |                          loads the session, resolves the signed-in user
    v
 core/http/routing/Routing.py  matches path + method against the discovered routes
    |                          (storage/cache/apps.json - written by `apps:discover`)
    v
 PermissionMiddleware          401 if signed out, 403 if inactive or missing the permission
    |
    v
 Controller method             a fresh controller instance per request; returns a Response
    |                          (view / JSON / redirect)
    v
 browser
```

Routes are matched in discovery order - apps in registration order, controllers by filename, methods
alphabetically - and **the first match wins**. Keep a dynamic route like `/blog/<slug>` from shadowing a
static one such as `/blog/new` (alphabetical method order happens to put `create` before `show`).

---

## Project layout

```
app.py                      REGISTERED_APPS - which apps are active
console.py                  command line: serve, apps:discover, migration, migrate, ...
env.example                 copy to .env
requirements.txt
migrations/                 numbered migration files (generated by `migration`)
storage/cache/apps.json     discovery cache - generated, read by routing and migrations

core/                       the framework
    apps/AppManager.py          discovers apps; validates permissions.py / menus.py
    controllers/ControllerBase.py   base class for every controller
    database/                   connection, query builders, schema builders, fields, migrator
    http/                       server, request/response classes, routing, middleware
    models/                     BaseModel, QuerySet, and the built-in models
                                (User, Role, Permission, RolePermission, File, Media,
                                 Session, PasswordReset, OAuthIdentity)
    services/                   Auth, Session, Access, Menu, Media, Storage, Template,
                                Validation, Mail, OAuth, PasswordReset, Env

public/
    main.py                     the dev server entry point
    apps/
        admin/                  built-in app: auth pages, dashboard, users, permissions, media
        blog/                   a small example app
```

---

## Apps

An app is a folder under `public/apps/` and is switched on in `app.py`:

```python
REGISTERED_APPS = [
    "admin",    # built in - keep it registered (see below)
    "blog",
    "notes",
]
```

What Plinth looks for inside `public/apps/<name>/`:

```
controllers/     one controller class per file; the filename must equal the class name
models/          one model class per file; the filename must equal the class name
services/        plain helper classes - not scanned, import them yourself
views/           Jinja2 templates, rendered as "<name>:path/to/view"
permissions.py   optional - PERMISSIONS and ROLES the app declares
menus.py         optional - items for the admin sidebar
```

### Discovery

Scanning happens once, not on every request. `python console.py apps:discover`:

1. imports every registered app's controllers and collects their `@route` declarations,
2. collects the models, `permissions.py` and `menus.py`,
3. validates the definitions (a broken one aborts with a list of every problem and changes nothing),
4. writes everything to `storage/cache/apps.json`,
5. syncs roles and permissions into the database.

**Run it again whenever you** add or remove an app, add or change a route, add a model, or edit a
`permissions.py` or `menus.py`.

> **The `admin` app must stay registered.** It owns login, registration and the dashboard, and declares the
> two roles the framework relies on (`admin.administrator` and `admin.member`). Discovery refuses to run
> without it.

---

## Routing and controllers

A route is declared on the controller method:

```python
from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route


class NoteController(ControllerBase):

    @route("/notes/<id>", methods=["GET"], permission=["authenticated"])
    def show(self, request):
        note_id = request.get_route_parameter("id")
        ...
```

`methods` defaults to `["GET"]` and `permission` to `["*"]` (public). Stack several `@route` decorators on one
method to answer more than one path.

### Permission values

A route's `permission` is a list; holding **any one** entry is enough.

| Value | Who gets through |
|---|---|
| `"*"` | Anyone, including signed-out visitors |
| `"authenticated"` | Any signed-in, active user |
| `"administrator"` | Administrators only - cannot be granted to a role |
| `"app.permission"` | Users holding that permission (administrators hold every permission) |

Signed-out requests to a protected route get **401**; signed-in users who aren't allowed (or whose account
isn't active) get **403**.

### Route parameters

- `<name>` matches one path segment: `/blog/<slug>`.
- `<name:path>` matches the rest of the URL, slashes included, and must be last: `/uploads/<path:path>`.

### What a controller can use

`ControllerBase` provides:

| Helper | Purpose |
|---|---|
| `self.db()` | The database connection |
| `self.view("app:view", context, request=request)` | Render a template to a response |
| `self.redirect(url)` / `self.back(request)` | Redirects |
| `self.json(data, status=200)` | JSON response |
| `self.not_found()` / `self.forbidden()` / `self.error()` / `self.no_content()` | Error and empty responses |
| `self.validate(request, rules)` | Run [validation](#validation) |
| `self.require_auth(request)` | The signed-in `User`, or `None` |
| `self.session(request)` / `self.auth(request)` / `self.env()` | Services |

The `request` object offers `request.get(key)`, `request.all()` (form fields and query),
`request.query`, `request.files`, `request.headers`, `request.json()`, `request.is_method("POST")`,
`request.get_path_info()` and `request.get_route_parameter(name)`.

---

## Views

Views are Jinja2 templates with autoescaping on. An app's views are named `"<app>:<path>"` and live in the
app's own `views/` directory:

| Call | File |
|---|---|
| `self.view("notes:index", ...)` | `public/apps/notes/views/index.html` |
| `self.view("admin:users/edit", ...)` | `public/apps/admin/views/users/edit.html` |

Pass `request=request` and every template also receives `current_user` (the signed-in `User` or `None`) and
`dashboard_menu()` (the sidebar items that user may see). Without `request`, a page renders as signed out.

Inside a template: `{% extends %}`, `{% include %}` and `{% block %}` resolve relative to the app's `views/`
directory. Missing values degrade to an empty string rather than raising.

Optional **themes** live in `public/views/<theme>/` and are used for view names *without* an app prefix;
choose the default with `THEME_DEFAULT`.

---

## Models, queries and migrations

### Defining a model

```python
from core.database.fields import AutoField, CharField, TextField, TimestampField
from core.models.BaseModel import BaseModel


class Note(BaseModel):
    table_name = "notes"

    id = AutoField()
    title = CharField(max_length=255)
    body = TextField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)
```

Field types: `AutoField`, `IntegerField`, `BigIntegerField`, `CharField`, `TextField`, `BooleanField`,
`EnumField`, `DateTimeField`, `TimestampField`, `ForeignKey`, `UniqueTogether`. Set `timestamps = False` on a
model without `created_at` / `updated_at` columns.

> **Avoid reserved words as column names** (`key`, `order`, `group`, ...). MySQL rejects them.

### Querying

The connection is passed explicitly - there is no global:

```python
db = self.db()

Note(db, title="Hello", body="First note").save()          # insert
note = Note.find(db, 1)                                     # by primary key
note.title = "Updated"; note.save()                         # update
note.delete()

Note.objects(db).filter(title__icontains="hello").order_by("-created_at")[:10]
Note.objects(db).filter(id__in=[1, 2, 3]).count()
Note.objects(db).exclude(body=None).first()
```

A `QuerySet` is lazy; nothing runs until you iterate it or call `first()`, `count()`, `exists()`,
`update()`, `delete()`, `create()` or `get_or_create()`. Bulk `update()` / `delete()` refuse to run without a
filter unless you pass `allow_all=True`.

| Lookup | Meaning |
|---|---|
| `field=value` | equals (`None` means `IS NULL`) |
| `__ne`, `__gt`, `__gte`, `__lt`, `__lte` | comparisons |
| `__in=[...]` | in a list |
| `__contains`, `__icontains`, `__startswith`, `__endswith` | `LIKE` matches |
| `__isnull=True/False` | `IS NULL` / `IS NOT NULL` |

There are no joins, `Q` objects or annotations - conditions are ANDed. For anything else use
`db.connect()` directly.

### Migrations

Migrations are generated by diffing your model fields against the last snapshot in `migrations/`:

```bash
python console.py apps:discover     # first - app models are read from the discovery cache
python console.py migration         # write migration file(s) for whatever changed
# review the new file in migrations/
python console.py migrate           # apply pending migrations
```

`migrate:status` shows what has run; `migrate:fresh` drops every registered table and re-runs everything
(**destroys data**).

---

## Validation

```python
validator = self.validate(request, {
    "email": "required|email",
    "password": "required|min:8|confirmed",
    "age": "integer|min:13",
})
if validator.fails():
    errors = validator.get_errors()      # {"email": ["..."], ...}
data = validator.validated()             # only the fields named in the rules
```

Rules are joined with `|`. Any rule except `required` is skipped when the field is empty.

`required` - `email` - `numeric` - `integer` - `min:N` - `max:N` - `in:a,b,c` - `alpha` - `alpha_num` - `url` -
`confirmed` (must equal `<field>_confirmation`)

---

## Authentication

- **Registration and login** with salted PBKDF2 password hashes.
- **Account status:** `active`, `pending`, `suspended`, `blocked`, `deleted`. Only `active` accounts can sign
  in. Deleting a user is a soft delete.
- **Sessions** are stored in the `sessions` table and the session id is regenerated on login. Lifetime comes
  from `SESSION_LIFETIME` (default two hours).
- **Password reset** sends a single-use link by email; tokens are stored hashed and expire after
  `PASSWORD_RESET_LIFETIME` (default one hour). Use `MAIL_DRIVER=log` in development to write emails to a file
  instead of sending them.
- **Social sign-in** with Google, GitHub and Microsoft. A provider appears on the login page only when both
  its `*_CLIENT_ID` and `*_CLIENT_SECRET` are set. Accounts created this way are active immediately.

Using the current user in code:

```python
user = self.require_auth(request)        # User or None
user.has_permission("notes.write")       # True for administrators too
user.is_admin()                          # holds every permission
user.has_role("admin.member")
```

---

## Roles, permissions and menus

### Declaring them in an app

An app lists what it adds in `public/apps/<name>/permissions.py`:

```python
PERMISSIONS = [
    {"key": "notes.write", "label": "Write notes", "description": "optional"},
]

ROLES = [
    {
        "key": "notes.author",
        "label": "Note author",
        "permissions": ["notes.write"],   # optional - granted by default
        # "superuser": True               # optional - holds every permission
    },
]
```

**Every key must start with the app's own name and a dot** (`notes.write`), followed by lowercase letters,
digits, `_` or `.`, at most 100 characters. That keeps two apps from ever colliding. Typos in field names and
roles that grant permissions nobody declares are rejected at discovery.

### What `apps:discover` does with them

It syncs the declarations into three tables - `roles`, `permissions` and `role_permissions` (the matrix):

- New roles and permissions are added; changed labels are updated.
- A role's declared default grants are applied **only when the role or the permission is first created**, so
  later changes an administrator makes in the matrix are never overwritten.
- **Anything belonging to an app that is no longer registered is removed** - its roles, permissions and
  matrix entries.
- Users are cleaned up too: permissions that no longer exist are stripped from their stored list, and anyone
  whose role disappeared is moved to the default role (`admin.member`).

### The Permissions page (administrators only)

`/admin/permissions` shows a Drupal-style matrix: one row per permission, grouped by app, one checkbox column
per role. The Administrator column is ticked and locked - administrators hold everything.

Administrators can also add **custom permissions** here. Permissions declared in code are read-only in the UI
(change them in `permissions.py`); custom ones are editable and deletable, and survive discovery for as long
as the app they belong to stays registered.

### How users get their permissions

Each user has one `role` and a list of permissions stored as JSON in `users.permissions_raw`
(`["notes.write"]`, or `["*"]` for an administrator).

- **At creation** the list is filled in automatically from the role's column of the matrix - whether the
  account came from registration, social sign-in or the admin **Add user** form.
- **It is a snapshot.** Changing a role's permissions later doesn't silently rewrite existing users - the
  matrix page has an **Apply to existing users with these roles** checkbox (on by default) that applies just
  the difference, so per-user edits survive.
- **Administrators can edit it per user** on the user's edit page, and choose the role. Everyone else - even
  someone holding `admin.users.manage` - can't see or change roles and permissions, and anything they submit
  for them is ignored on the server.
- To move a user to another role in code, use `user.set_role("notes.author")`: it also takes that role's
  permissions, so demoting an administrator really removes their access.

### Sidebar menus

Apps add items to the dashboard sidebar in `menus.py`:

```python
MENUS = [
    {
        "key": "notes.new",             # "<app>.<name>", same rule as permissions
        "label": "New note",
        "path": "/notes/new",
        "group": "Content",             # sidebar section (default "Menu")
        "permission": ["notes.write"],  # who sees it (default ["authenticated"])
        "weight": 220,                  # lower = higher up (default 0)
        "exact": False,                 # True = highlight only on this exact path
    },
]
```

`permission` takes the same values as a route and is checked with the **same rule**, so a link appears
exactly when the page behind it would be allowed. Items are ordered by weight, grouped by `group`, and groups
with nothing visible disappear.

---

## Media library and file storage

`/admin/media` is modelled on Drupal's Media: a **media item** (type, name, alt text, published flag)
wraps a stored **file**.

- **Library:** thumbnail grid, type tabs with counts, search by name, pagination.
- **Upload:** drag and drop, several files at once. A single file goes straight to its edit form to add alt
  text.
- **Edit:** preview (image, audio or video player), name, alt text, published flag, and *replace file* (must
  be the same type).
- **Delete:** removes the item and the stored file.

| Type | Extensions |
|---|---|
| image | png, jpg, jpeg, gif, webp |
| document | pdf, txt, csv, doc, docx, xls, xlsx, ppt, pptx, odt, ods, odp |
| audio | mp3, wav, ogg, m4a |
| video | mp4, webm, mov |

**Safety.** Uploads are checked against the extension allowlist and - for formats with a recognisable
signature - the actual content, so a renamed `.html` can't pass as `.png`. The MIME type comes from the
extension, never from the browser. Client filenames are reduced to a safe basename, and stored names are
generated. Files are saved on the public disk under `media/<YYYY-MM>/` and served at `/uploads/...`, where
path traversal is blocked, `X-Content-Type-Options: nosniff` is set, HTML / SVG / XML are always sent as
downloads, and ETag / `304` / `Range` requests are supported.

Limits: `MEDIA_MAX_UPLOAD_MB` per file (default 10) and a 50 MB cap on any request body. Oversized requests
get a proper `413`.

For production, serve `/uploads/` from nginx or a CDN - the built-in route reads each file into memory.

---

## Configuration

Settings live in `.env` (start from `env.example`).

| Setting | Default | Purpose |
|---|---|---|
| `DB_DRIVER` | `mysql` | `mysql`, `postgres`, `sqlite` or `mongodb` - migrations support MySQL / MariaDB only (see limitations) |
| `DB_HOST` `DB_PORT` `DB_USER` `DB_PASSWORD` `DB_NAME` | | Database connection. For SQLite `DB_NAME` is the file path |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | Dev server address |
| `APP_URL` | | Base URL used for absolute links in emails |
| `MAIL_DRIVER` | `smtp` | `smtp`, or `log` to write emails to `MAIL_LOG_PATH` |
| `MAIL_FROM_ADDRESS` `MAIL_FROM_NAME` | | Sender |
| `MAIL_HOST` `MAIL_PORT` `MAIL_USERNAME` `MAIL_PASSWORD` `MAIL_ENCRYPTION` | | SMTP (`tls`, `ssl`, `none`) |
| `SESSION_LIFETIME` | `7200` | Seconds |
| `SESSION_COOKIE_NAME` | `session_id` | |
| `PASSWORD_RESET_LIFETIME` | `3600` | Seconds |
| `GOOGLE_*` `GITHUB_*` `MICROSOFT_*` | | `CLIENT_ID`, `CLIENT_SECRET`, `REDIRECT_URI` (plus `MICROSOFT_TENANT`) |
| `MEDIA_MAX_UPLOAD_MB` | `10` | Largest single upload |
| `MEDIA_ALLOWED_EXTENSIONS` | all | Comma list that narrows the built-in types |
| `STORAGE_PUBLIC_PATH` | `public/uploads` | Web-accessible disk, served at `/uploads/` |
| `STORAGE_PRIVATE_PATH` | `storage/app` | Not web-accessible |
| `THEME_DEFAULT` | `default` | Theme for view names without an app prefix |

---

## Command line

Run from the project root (next to `.env`).

| Command | What it does |
|---|---|
| `python console.py serve [--host H] [--port P]` | Start the dev server |
| `python console.py apps:discover` | Scan apps, refresh the cache, sync roles and permissions to the database |
| `python console.py migration` | Generate migration files from model changes |
| `python console.py migrate` | Apply pending migrations |
| `python console.py migrate:status` | Show applied and pending migrations |
| `python console.py migrate:fresh` | Drop all registered tables and re-run every migration |

If `apps:discover` runs before the tables exist, it saves the cache, says the roles and permissions were not
synced, and tells you to `migrate` and run it again.

---

## Tutorial: build an app

A small notes app with a permission, a role, and sidebar entries. Create these files.

**`public/apps/notes/models/Note.py`**

```python
from core.database.fields import AutoField, CharField, TextField, TimestampField
from core.models.BaseModel import BaseModel


class Note(BaseModel):
    table_name = "notes"

    id = AutoField()
    title = CharField(max_length=255)
    body = TextField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)
```

**`public/apps/notes/controllers/NoteController.py`**

```python
from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route

from public.apps.notes.models.Note import Note


class NoteController(ControllerBase):

    @route("/notes", methods=["GET"], permission=["authenticated"])
    def index(self, request):
        notes = list(Note.objects(self.db()).order_by("-created_at"))
        return self.view("notes:index", {"notes": notes}, request=request)

    @route("/notes/new", methods=["GET", "POST"], permission=["notes.write"])
    def create(self, request):
        if request.is_method("POST"):
            validator = self.validate(request, {"title": "required|min:2", "body": "max:5000"})
            if validator.fails():
                return self.view(
                    "notes:form", {"errors": validator.get_errors(), "old": request.all()}, request=request
                )
            data = validator.validated()
            Note(self.db(), title=data["title"], body=data.get("body")).save()
            return self.redirect("/notes")

        return self.view("notes:form", {"errors": {}, "old": {}}, request=request)
```

**`public/apps/notes/views/index.html`**

```html
<h1>Notes</h1>
<ul>
  {% for note in notes %}
  <li><strong>{{ note.title }}</strong> - {{ note.body }}</li>
  {% else %}
  <li>No notes yet.</li>
  {% endfor %}
</ul>
{% if current_user and current_user.has_permission("notes.write") %}<a href="/notes/new">New note</a>{% endif %}
```

**`public/apps/notes/views/form.html`**

```html
<h1>New note</h1>
<form method="post" action="/notes/new">
  <input name="title" value="{{ old.title|default('') }}" placeholder="Title">
  {% if errors.title %}<p>{{ errors.title[0] }}</p>{% endif %}
  <textarea name="body">{{ old.body|default('') }}</textarea>
  <button type="submit">Save</button>
</form>
```

**`public/apps/notes/permissions.py`**

```python
PERMISSIONS = [
    {"key": "notes.write", "label": "Write notes"},
]

ROLES = [
    {"key": "notes.author", "label": "Note author", "permissions": ["notes.write"]},
]
```

**`public/apps/notes/menus.py`**

```python
MENUS = [
    {"key": "notes.list", "label": "Notes", "path": "/notes", "group": "Content", "permission": ["authenticated"], "weight": 210},
    {"key": "notes.new", "label": "New note", "path": "/notes/new", "group": "Content", "permission": ["notes.write"], "weight": 220},
]
```

Register it in **`app.py`**: add `"notes"` to `REGISTERED_APPS`. Then:

```bash
python console.py apps:discover     # routes, model, permission, role and menus are found
python console.py migration         # writes migrations/0013_create_notes_table.py
python console.py migrate
python console.py apps:discover     # loads the new role and permission into the database
python console.py serve
```

What you get, with no further code:

- `/notes` is open to any signed-in user; `/notes/new` needs `notes.write`.
- **Note author** appears as a column on the Permissions page and as a role on the Add user form. A user
  created with that role starts with `["notes.write"]` stored on their account.
- The sidebar shows **Notes** to every signed-in user and **New note** only to people who can write.
- Titles are HTML-escaped by default, so `<b>` in a title shows as text.

Remove `"notes"` from `REGISTERED_APPS` and run `apps:discover` again: its role, permission and menu items
disappear, and any user holding that role moves to the default role.

---

## Status and known limitations

Plinth is built for development and learning. Things to know before relying on it:

- **Dev server.** It runs on Python's single-threaded `http.server`. For anything real, run behind a
  production server or reverse proxy, and serve `/uploads/` from nginx or a CDN.
- **No CSRF protection** on forms, and the session cookie sets `HttpOnly` but not `SameSite` or `Secure`.
- **Migrations run on MySQL / MariaDB only.** Generated migrations use MySQL syntax (`AUTO_INCREMENT`,
  inline `ENUM`, `ON UPDATE CURRENT_TIMESTAMP`). `python console.py migrate` fails on SQLite with a syntax error
  near `AUTO_INCREMENT`, and PostgreSQL doesn't accept that syntax either. Once the tables exist (create them by hand) the application runs on SQLite; PostgreSQL support is untested. MongoDB has a connection and schema builder but no ORM or session support.
- **Sessions are saved only by the sign-in / sign-out / OAuth flows.** Data you put in a session elsewhere
  is not persisted unless your controller saves it (`session.save(response)`).
- **No generic 500 page.** An unhandled exception in a controller drops the connection (oversized or
  malformed request bodies *do* get proper 413 / 400 responses).
- **Social sign-in links accounts by email** without checking that the provider has verified that address.
- **`/` is public** and renders the dashboard; the dashboard itself is `/admin`.
- **The media "Published" flag** is stored and shown, but nothing consumes it yet, and files on the public
  disk are reachable by URL regardless. SVG uploads and private-disk media are not supported.
- A non-administrator holding `admin.users.manage` can still edit an administrator's email or password.
