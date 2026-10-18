# Plinth

**A small, batteries-included Python web framework - the base your apps stand on.**

Plinth gives you routing, an ORM with migrations, authentication, roles and permissions, an admin
dashboard and a media library, on top of Python's standard-library HTTP server and Jinja2. Your own
features live in self-contained **apps** that declare their routes, models, permissions and menu items
right next to the code that uses them.

- **Apps, not a monolith.** A feature is a folder (`public/apps/<name>/`) with controllers, models, views,
  permissions and menus. Register it in one line and everything is discovered.
- **Routes live on the method.** `@route("/notes/new", permission=["notes.write"])` - no central route table.
- **Django-style ORM.** Declare fields on a model, query with `filter(title__icontains="x")` - including
  `Q`/`F` expressions, aggregates, relationships and joins - and evolve the schema with generated migrations.
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
- [Relationships](#relationships)
- [Validation](#validation)
- [Authentication](#authentication)
- [Roles, permissions and menus](#roles-permissions-and-menus)
- [Media library and file storage](#media-library-and-file-storage)
- [Public API access (CORS)](#public-api-access-cors)
- [API authentication (the apiauth app)](#api-authentication-the-apiauth-app)
- [Fake data (development)](#fake-data-development)
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

Several routes can share a path with different methods (`GET /items` and `POST /items` as two controller methods): the first
route whose path *and* method match handles the request, and a path that exists but not for that method gets `405` with an
`Allow` header listing every method it does accept.

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
authenticators.py  optional - classes that identify API callers from their credentials (see API authentication)
factories.py     optional - teaches the fake-data tools how to build realistic rows for your models
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

An app can reuse another app's templates by prefixing the name with the app and a colon - the dashboard layout is
`{% extends "admin:admin" %}`, which is how the `apiauth` pages look like part of the admin. Unprefixed names always
resolve in the app's own `views/` first.

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

Field types:

| Field | Column | Python value |
|---|---|---|
| `AutoField`, `IntegerField`, `BigIntegerField`, `SmallIntegerField` | `INTEGER`, `BIGINT`, `SMALLINT` (`unsigned=True` on all but `AutoField`) | `int` |
| `FloatField` | `DOUBLE` | `float` |
| `DecimalField(max_digits, decimal_places)` | `DECIMAL(p,s)` - use it for money | `decimal.Decimal` |
| `CharField(max_length)`, `TextField(size=)` | `VARCHAR`, `TEXT` / `MEDIUMTEXT` / `LONGTEXT` | `str` |
| `BooleanField`, `EnumField(choices)` | `BOOLEAN`, `ENUM(...)` | `bool`, `str` |
| `DateField`, `TimeField` | `DATE`, `TIME` | `datetime.date`, `datetime.time` |
| `DateTimeField`, `TimestampField` | `TIMESTAMP` | passed through as the driver returns it |
| `JSONField` | `JSON` | any JSON value - `dict`, `list`, ... |
| `UUIDField(auto=False)` | `CHAR(36)`; `auto=True` generates a `uuid4` for new rows | `uuid.UUID` |
| `BinaryField(max_length=None, size="blob")` | `BLOB` / `MEDIUMBLOB` / `LONGBLOB`, or `VARBINARY(n)` | `bytes` |
| `ForeignKey`, `OneToOneField`, `ManyToManyField` | see [Relationships](#relationships) | the related model |
| `UniqueTogether` | a table-level `UNIQUE` constraint | - |

Fields convert in both directions, so the Python value is the same on every driver: save a `Decimal`, a
`dict`, a `UUID`, a `date` or `bytes` and you get the same type back, and the same objects work in filters
(`filter(price__gt=Decimal("9.99"))`). Pass JSON fields the Python object, not an already-encoded string.
MySQL doesn't allow a `DEFAULT` on `JSON` and blob columns, so those fields refuse one - set defaults in code.
Set `timestamps = False` on a model without `created_at` / `updated_at` columns.

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

A model instance can stand in for a relation's key: `filter(owner=user)`, `filter(owner__in=[a, b])`. `pk` means the
primary key.

#### OR, NOT and column arithmetic

```python
from core.models import Q, F

Note.objects(db).filter(Q(title__icontains="plan") | Q(body__icontains="plan"))
Note.objects(db).filter(Q(owner=ada) & ~Q(status="archived"))      # & | ~ and nesting
Note.objects(db).exclude(Q(status="draft") | Q(status="archived"))

Product.objects(db).filter(price__gt=F("cost") * 2)                 # compare a column with another
Product.objects(db).filter(pk=1).update(stock=F("stock") - 1)       # one atomic UPDATE
```

#### Aggregates and grouping

```python
from core.models import Count, Sum, Avg, Min, Max

Order.objects(db).aggregate(total=Sum("amount"), orders=Count("id"))     # {"total": Decimal(...), "orders": 12}

(Media.objects(db)
      .values("bundle").annotate(n=Count("mid"))                         # GROUP BY bundle
      .filter(n__gt=1).order_by("-n"))                                   # HAVING / ORDER BY the aggregate
```

`annotate()` follows `values()` (it groups by those fields). Sums of `DecimalField`s come back as `Decimal`.

#### Choosing what to load

```python
Note.objects(db).values("id", "title")                    # dicts instead of model instances
Note.objects(db).values_list("title", flat=True)          # plain values
Note.objects(db).values_list("owner_id", flat=True).distinct()

Doc.objects(db).defer("body", "thumbnail")                # skip big columns ...
Doc.objects(db).only("title")                             # ... or name the ones you want
doc.thumbnail                                             # a deferred column is fetched on first read
```

A queryset loads every column of a row unless you say otherwise - use `defer()` for large text or blob columns.
`Model.objects(db).none()` is a queryset that never matches (and never queries).

### Relationships

A `ForeignKey` wraps a column you declare normally, adds the `FOREIGN KEY` constraint, and lets you follow the
relationship in Python and in queries:

```python
from core.database.fields import AutoField, CharField, IntegerField, ForeignKey, OneToOneField, ManyToManyField, UniqueTogether

class Book(BaseModel):
    table_name = "books"
    id = AutoField()
    title = CharField(max_length=100)
    author_id = IntegerField(null=True)
    author = ForeignKey(column="author_id", references=Author,
                        on_delete="SET NULL", related_name="books")
```

```python
book.author                          # the Author (fetched on first access, then cached)
book.author = ada                    # sets author_id; Book(db, author=ada) works too
ada.books                            # reverse side: a normal QuerySet of her books
ada.books.create(title="New")        # fills in author_id for you

Book.objects(db).filter(author__name="Ada")             # follow a relation in a filter, any depth
Book.objects(db).order_by("author__name")
Book.objects(db).select_related("author")                # JOIN instead of one query per row
```

Without `select_related()`, reading `book.author` for each book in a loop runs one query per book - use it
(or `select_related("author__publisher")` for deeper chains) whenever you list rows and show their relations.

`ForeignKey` options: `references` (the model class, or just a table name), `references_column` (defaults to the
related model's primary key), `on_delete` / `on_update` (`CASCADE`, `SET NULL`, `RESTRICT`, `NO ACTION`,
`SET DEFAULT`) and `related_name` (the reverse accessor, default `<model>_set`; `"+"` for none). Left at their
defaults the generated constraint is unchanged, so adding the relation to an existing model creates no migration.

**One-to-one.** `OneToOneField` is a `ForeignKey` whose column is declared `unique=True`; the reverse accessor
returns the single related object (or `None`) instead of a queryset.

**Many-to-many** goes through a join table that you declare as an ordinary model - so your migrations create
it and it can carry extra columns:

```python
class BookTag(BaseModel):
    table_name = "book_tags"
    id = AutoField(); book_id = IntegerField(); tag_id = IntegerField()
    unique_pair = UniqueTogether(columns=["book_id", "tag_id"])

class Book(BaseModel):
    ...
    tags = ManyToManyField(Tag, through=BookTag, source_column="book_id",
                           target_column="tag_id", related_name="books")
```

```python
book.tags.add(python, web)          # idempotent; add(tag, note="x") fills extra join-table columns
book.tags.remove(web); book.tags.set([python, orm]); book.tags.clear()
book.tags.filter(name__startswith="p").count()           # book.tags is a QuerySet of Tag
python.books                                             # the reverse side has the same API
```

Things to know:

- Reverse accessors (`ada.books`) exist once the model holding the `ForeignKey` has been imported.
- Lookups follow a relation **forwards** (`ForeignKey` / `OneToOneField`), not backwards or through many-to-many:
  to find books with a tag, use `Book.objects(db).filter(pk__in=[b.id for b in tag.books])`.
- `update()` and `delete()` can't be combined with a relation lookup - filter by key instead (`pk__in=[...]`).
- There is no `prefetch_related()`; for a reverse relation over many rows, query the related model once with
  `filter(author_id__in=[...])`.
- The migrator never creates a many-to-many field's table - declare the join model.
- `exclude(field=x)` is plain SQL negation, so rows where `field` is `NULL` are not returned; `exclude(Q(...))`
  negates the whole group.

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

## Public API access (CORS)

Out of the box, a browser on another site can't call your pages from JavaScript - browsers enforce CORS and
Plinth sends none. Switch on **public API access** and the whole site becomes callable from any origin you
allow, with no CORS code in any app. Apps then only need to authenticate their own requests.

```ini
# .env
PUBLIC_API_ENABLED=true
```

That is all it takes for "any site may call this one". It is **off by default**; with it off the server behaves
exactly as before.

| Setting | Default | Meaning |
|---|---|---|
| `PUBLIC_API_ENABLED` | `false` | Master switch |
| `PUBLIC_API_ALLOWED_ORIGINS` | `*` | `*` for any site, or a comma list such as `https://app.example.com,https://admin.example.com` |
| `PUBLIC_API_ALLOWED_METHODS` | `GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS` | Methods browsers may use |
| `PUBLIC_API_ALLOWED_HEADERS` | `*` | `*` allows whatever the browser asks for (including `Authorization`); or list them: `Authorization,Content-Type` |
| `PUBLIC_API_EXPOSE_HEADERS` | | Response headers scripts may read, e.g. `X-Total-Count` |
| `PUBLIC_API_MAX_AGE` | `86400` | Seconds a browser may cache a preflight answer |

**How it works.** For "non-simple" requests (a JSON body, `PUT`/`DELETE`, an `Authorization` header) the browser first
sends an `OPTIONS` *preflight*. `Server.py` answers it directly with `204` and the `Access-Control-Allow-*`
headers - no database, session or routing involved, so it works for every URL. Every other response to a
cross-origin request gets `Access-Control-Allow-Origin` too, **errors included**, so a front end can read its own
`401` or `404`. When you list specific origins, an unlisted origin's preflight gets `403` and its responses get
no CORS headers; a listed origin is echoed back with `Vary: Origin`. A controller can set its own
`Access-Control-Allow-Origin` to override the policy for one route.

**Authentication is the app's job.** The framework's own login uses a session cookie, and **cookies are never sent
cross-origin** (Plinth never sends `Access-Control-Allow-Credentials`), so your admin session isn't exposed to other
sites. A public API authenticates with a token instead:

```python
import hmac

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route


class NoteApiController(ControllerBase):

    @route("/api/notes", methods=["GET", "POST"])        # public as far as the framework is concerned
    def notes(self, request):
        expected = f"Bearer {self.env().get('NOTES_API_TOKEN', '')}"
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, expected):      # the app decides who gets in
            return self.json({"error": "unauthorized"}, status=401)
        return self.json({"notes": []})
```

Be aware that with `PUBLIC_API_ENABLED=true` and `*`, any website's scripts can read every response that doesn't
itself require authentication. Keep the switch off unless you mean that, or list the origins you trust.

---

## API authentication (the `apiauth` app)

Plinth ships an optional app, `apiauth`, that gives a site every common way of authenticating API clients -
**API keys, OAuth-style bearer tokens, JWTs, HTTP Basic and signed (HMAC) requests** - plus the routes and
dashboard pages to create and manage the credentials. Register it and you're done; you don't write any of it
yourself. Pair it with [Public API access (CORS)](#public-api-access-cors) to let other sites call your API.

It is **not registered by default**. To switch it on:

```python
# app.py
REGISTERED_APPS = ["admin", "blog", "apiauth"]
```

```bash
python console.py apps:discover     # finds its routes, models, permissions and menus
python console.py migration         # writes the migration for its two tables (api_keys, api_tokens)
python console.py migrate
python console.py apps:discover     # loads its permissions and role into the database
```

**What you get for free.** An API caller who authenticates becomes the request's `current_user` - exactly as if
that person had signed in with a browser - so **every route you've already written, with its `permission=[...]`
list, roles and menus, works for API clients unchanged**. A key can be limited to some of its owner's permissions
(its *scopes* - permission keys), and can never do more than its owner can.

### The ways to authenticate

Choose which are accepted in `.env` with `API_AUTH_SCHEMES` (default `apikey,bearer`):

| Scheme | Client sends | Good for |
|---|---|---|
| `apikey` | `X-API-Key: <key>` (or `Authorization: ApiKey <key>`) | Scripts, integrations, anything simple |
| `bearer` | `Authorization: Bearer <token>` - an opaque token from the token endpoint | Apps where people sign in; revocable, expires |
| `jwt` | `Authorization: Bearer <jwt>` - a signed JWT from the token endpoint | When clients or other services want to read the claims |
| `basic` | `Authorization: Basic base64(<key_id>:<secret>)` | Tools that only speak HTTP Basic (`curl -u`) |
| `hmac` | `Authorization: Signature keyId=..., timestamp=..., nonce=..., signature=...` | Server-to-server: the secret is never sent, requests can't be altered or replayed |

`GET /api/auth/schemes` (public) tells a client which of these are on and how to use them, and
`GET /api/auth/me` returns who you are, how you authenticated and what this credential may do - handy for testing.

### Getting credentials

**API keys** belong to a user who holds the `apiauth.keys.manage` permission (administrators do; the bundled
role *API developer* has it). Create them on the dashboard under **API keys**, or over the API:

```bash
# sign in to get a token, then create a key with it
curl -d grant_type=password -d username=ada@example.com -d password=... https://example.com/api/oauth/token
curl -H "Authorization: Bearer plt_..." -H "Content-Type: application/json" \
     -d '{"name": "nightly export", "scopes": ["notes.read"], "expires_in_days": 90}' https://example.com/api/keys
# -> {"key": {...}, "secret": "pk_3f9c1a7b2d4e.Zx...", "signing_secret": "..."}   <- shown once, never again

curl -H "X-API-Key: pk_3f9c1a7b2d4e.Zx..." https://example.com/api/notes          # use it
curl -u pk_3f9c1a7b2d4e:Zx... https://example.com/api/notes                         # or as HTTP Basic
```

**Tokens** come from the token endpoint, which follows OAuth 2.0 (RFC 6749), so existing OAuth libraries work:

| Grant | You send | You get |
|---|---|---|
| `password` | `username` (the account email), `password` | access token + refresh token, for a person's account |
| `client_credentials` | `client_id` (the key id) + `client_secret` (the secret), or HTTP Basic | access token only, for a program using a key |
| `refresh_token` | `refresh_token` | a new pair; the old refresh token is spent |

```bash
curl -d grant_type=password -d username=ada@example.com -d password=... -d scope="notes.read" https://example.com/api/oauth/token
# -> {"access_token": "plt_...", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "plr_...", "scope": "notes.read"}

curl -u pk_3f9c1a7b2d4e:Zx... -d grant_type=client_credentials https://example.com/api/oauth/token
curl -d grant_type=refresh_token -d refresh_token=plr_... https://example.com/api/oauth/token
curl -d token=plt_... https://example.com/api/oauth/revoke                              # sign out / revoke
```

Requests may be form-encoded or JSON. Set `API_AUTH_TOKEN_FORMAT=jwt` to issue JWTs instead of opaque tokens
(needs `jwt` in `API_AUTH_SCHEMES` and an `API_AUTH_SECRET`).

**Signed requests** prove possession of a secret without sending it. The key's creation response includes a
`signing_secret`; the client signs each request:

```python
import hashlib, hmac, secrets, time, urllib.request

def signed_request(key_id, signing_secret, method, path_and_query, body=b""):
    timestamp, nonce = str(int(time.time())), secrets.token_hex(12)
    canonical = "\n".join([method, path_and_query, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    signature = hmac.new(signing_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    header = f'Signature keyId="{key_id}", timestamp="{timestamp}", nonce="{nonce}", signature="{signature}"'
    request = urllib.request.Request("https://example.com" + path_and_query, data=body or None,
                                     method=method, headers={"Authorization": header})
    return urllib.request.urlopen(request)
```

The server rejects a wrong signature, a timestamp more than `API_AUTH_HMAC_TOLERANCE` seconds off, and a nonce
it has already seen - so a captured request can't be altered or replayed. (`hmac` needs `API_AUTH_SECRET`.)

### Using it from your own code

Nothing special - protect routes the way you always do, and API callers are covered:

```python
@route("/api/notes", methods=["GET"], permission=["notes.read"])      # a browser session OR any API credential
def notes(self, request):
    user = self.require_auth(request)                                  # the person (or key owner) making the call
    auth = getattr(request, "_auth", None)                             # how: auth.scheme, auth.credential, auth.scopes
    return self.json({"notes": [...]})
```

A key with the scope `notes.read` can call that route; a key without it gets `403`; a bad or expired credential gets
`401` with a `WWW-Authenticate` header before your code runs. Any authenticated caller can be asked who they are
with `GET /api/auth/me`.

### Everything the app provides

| Route | Who | Purpose |
|---|---|---|
| `POST /api/oauth/token` | public | The token endpoint (grants above) |
| `POST /api/oauth/revoke` | public | Revoke an access or refresh token (RFC 7009) |
| `GET /api/auth/schemes` | public | Which schemes are on, and how to use them |
| `GET /api/auth/me` | any authenticated caller | Who am I, how, with what scopes |
| `POST /api/auth/logout` | token callers | Revoke the token this request used |
| `GET` / `POST /api/keys` | `apiauth.keys.manage` | List / create your keys |
| `DELETE /api/keys/<key_id>` | owner (or `apiauth.keys.admin`) | Revoke a key and the tokens it issued |
| `POST /api/keys/<key_id>/rotate` | owner (or `apiauth.keys.admin`) | New secret for the same key; the old one dies immediately |
| `GET /api/tokens`, `DELETE /api/tokens/<id>` | any authenticated caller | See and end your own signed-in sessions |
| `/admin/api-keys` | `apiauth.keys.manage` | Dashboard: your keys, create / rotate / revoke |
| `/admin/api-credentials` | `apiauth.keys.admin` | Dashboard: everyone's keys and active tokens |

### Security model

- **Secrets are never stored.** Keys and tokens are random 256-bit values; only SHA-256 hashes are kept, and the
  plaintext is shown exactly once. Signing secrets are derived (HMAC of `API_AUTH_SECRET` and the key), not stored.
- **Scopes only narrow.** A credential's permissions are the intersection of its owner's and its scopes, and you can
  only grant scopes you hold yourself - a scoped key can't create a more powerful one.
- **Refresh tokens rotate.** Using one spends it. If a spent refresh token is ever presented again (stolen, or a
  replaying client), the whole login - every token descended from it - is revoked.
- **Revocation is immediate:** revoking or rotating a key kills the tokens it issued; suspending an account stops
  all of its credentials; JWTs are checked against the database by default (`API_AUTH_JWT_CHECK_REVOCATION=false`
  makes them stateless, valid until they expire).
- **Strict JWTs.** Only HS256 is accepted (never `none` or another algorithm); the signature is verified before the
  payload is trusted; `exp` is required and `iss` is checked.
- **Guessing is throttled.** Failed attempts are counted per client address + identity and answered with `429` and
  `Retry-After` past `API_AUTH_MAX_FAILURES`. The password check takes about the same time whether or not the email exists.
- **Not exposed to CSRF.** These credentials travel in headers, not cookies, so another site can't ride on them.

Limits: the throttle and the nonce cache are **in memory, per process** - fine for the single-process server this
framework ships with, but behind several processes also rate-limit at the proxy. The client address is the TCP peer,
so behind a reverse proxy every request appears to come from the proxy. Send credentials only over HTTPS in
production. `jwt` and `hmac` need `API_AUTH_SECRET`; the app reports a missing or short one instead of running
without it (`/api/auth/schemes` lists `configuration_problems`).

### Writing your own authenticator

The app is built on a framework hook you can use too. An app lists authenticator classes in its `authenticators.py`:

```python
AUTHENTICATORS = ["public.apps.myapp.services.MyAuth.MyAuth"]
```

```python
from core.services.RequestAuthService import AuthResult, AuthenticationFailed

class MyAuth:
    def __init__(self, db_connection): self.db = db_connection

    def authenticate(self, request):
        header = request.headers.get("X-My-Token")
        if not header:
            return None                                   # not my credentials - try the next authenticator / the session
        user = ...                                        # look it up
        if user is None:
            raise AuthenticationFailed("invalid_token", "unknown token")      # -> 401
        return AuthResult(user=user, scheme="mytoken", scopes=None)           # scopes: permission keys, or None
```

On every request `Server` asks each registered authenticator in turn; the first to return a result decides, and that user
becomes `current_user` for the request.

---

## Fake data (development)

Plinth can fill your database with realistic fake rows - users, posts, products, whatever your models describe -
from the command line or from a page in the dashboard. It reads your models' fields (types, limits, choices,
relationships) and your column names, so **it works on any model with no setup**; an app can teach it more.
It uses [Faker](https://faker.readthedocs.io/), which is an optional dependency:

```bash
pip install Faker
```

### From the command line

```bash
python console.py fake:models                       # what can be faked, and how many rows each table has
python console.py fake:generate User=20 Post=100    # create rows (Model or table name, optional =count)
python console.py fake:generate Post --count 50 --seed 7 --locale fr_FR
python console.py fake:generate Post --dry-run      # show sample rows, save nothing
python console.py fake:runs                         # what earlier runs created
python console.py fake:undo [run_id]                # delete exactly what a run created (default: the latest)
```

`fake:generate` shows the database it is about to write to and asks for confirmation (`--yes` skips it; without a
terminal it refuses unless you pass `--yes`, so nothing is ever written unattended by accident).

| Option | Default | Meaning |
|---|---|---|
| `--count N` | the model's default (10) | Rows for models named without `=count` |
| `--locale xx_YY` | `en_US` | Language of the data - `fr_FR`, `de_DE`, `ja_JP` ... (any Faker locale) |
| `--seed N` | random | The same seed gives the same data every time |
| `--password P` | `password` | The password every fake user gets, so you can sign in as any of them |
| `--null-ratio R` | `0.1` | How often a nullable column is left empty (0 to 1) |
| `--no-related` | off | Don't create rows in related tables that are empty |
| `--dry-run` | off | Preview only |
| `--force` | off | `fake:undo`: allow it even if the run was made against a different database |

### What it generates

Each column gets a value that fits its field - a `DecimalField(6,2)` a price that fits six digits, a
`CharField(max_length=5)` text of at most five characters, an `EnumField` one of its choices - and where the
**name** suggests something, something fitting: `email` an email address, `title` a headline, `slug` a slug, `price`
a price, `age` an age, `created_at` a date in the past, `phone`, `city`, `url`, `company` ... The auto-increment id,
`updated_at`, `deleted_at` and auto UUIDs are left to the framework.

- **Relations.** Foreign keys point at rows that exist. If the related table is empty, a few related rows are created
  first (or pass `--no-related` to get an error that tells you what to generate first). Many-to-many relations get
  up to three links. Models are generated parents-first, so `Post=10 User=3` works in either order.
- **Uniqueness.** Unique columns and `UniqueTogether` constraints never collide - with each other or with rows already
  in the table; a clashing row is simply regenerated. If a constraint is too tight for random data, you get a clear
  message instead of an endless loop.
- **Fake users really work.** They get a hashed password (the one from `--password`), a status mostly `active`, and
  the default role - with its permissions collected automatically, exactly as for any new account.
- **Undo.** Every run is recorded (in `storage/fakedata/runs.json`), including a run that failed half way, so
  `fake:undo` removes exactly what was created - join-table rows and related rows included - and leaves everything else
  alone. It refuses to delete by id from a different database than the one the run was made against.
- **Real data is protected.** Models marked `fakeable = False` are never generated: sessions, password resets, tokens,
  API keys, roles, permissions, stored files and media. Mark your own the same way:
  `class Invoice(BaseModel): fakeable = False`.

### Teaching it about your models

When guessing isn't enough, an app adds a `factories.py` that overrides single columns:

```python
# public/apps/blog/factories.py
from public.apps.blog.models.Post import Post

FACTORIES = {
    Post: {
        "__count__": 20,                                                   # default rows when none is requested
        "title": lambda fake: fake.sentence(nb_words=6).rstrip("."),       # a callable receives a Faker
        "slug": lambda fake, ctx: slugify(ctx.row["title"]),               # ... or (Faker, ctx); ctx.row = columns so far
        "status": "draft",                                                 # or just a constant
        "__after__": lambda instance, fake, ctx: ...,                      # runs after each row is saved
    },
}
```

A callable may return `None` for NULL, or `SKIP` (from `core.fakedata.generators`) to leave the column alone.
`ctx.index` is the row's position in the batch and `ctx.options` holds the run's settings. `python console.py
apps:discover` checks these files, so a misspelt column name fails there rather than half way through a run.
The bundled blog app has a working example.

### In the dashboard

The optional `fakedata` app gives the same thing a page with checkboxes - pick models and counts, choose a language,
seed and password, **Preview** sample rows, **Generate**, and **Undo** earlier runs:

```python
# app.py
REGISTERED_APPS = ["admin", "blog", "fakedata"]
```

```ini
# .env - it writes junk into your database, so it is off unless you turn it on
FAKE_DATA_ENABLED=true
```

Then run `python console.py apps:discover` and open **Fake data** in the sidebar's *Developer* group. It is
administrators only even when enabled, limited to 1000 rows per model per request (use the command line for bigger
loads), and shows which database it will write to before you generate. **Leave `FAKE_DATA_ENABLED` off anywhere that
holds real data.**

### From your own code

```python
from core.fakedata.service import FakeDataService
from core.fakedata.generators import FakeOptions

service = FakeDataService(db, FakeOptions(seed=42, locale="de_DE"))
result = service.generate([(User, 20), (Post, 100)])    # a RunResult: run_id, created={"users": 20, ...}
service.preview(Post, 3)                                # sample rows, nothing saved
service.undo(result.run_id)                             # delete what that run created
```

Handy in tests and seed scripts. Notes: rows are inserted one at a time through the models (so model logic like
password hashing runs), which is fast enough for thousands of rows but not millions; related rows are chosen from the
first 500 existing rows of the related table; and there is no transaction, so a run that fails part way leaves what
it created - recorded, and removable with `fake:undo`.

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
| `PUBLIC_API_ENABLED` | `false` | Allow other sites to call this one from the browser - see [Public API access (CORS)](#public-api-access-cors) |
| `PUBLIC_API_ALLOWED_ORIGINS` `_METHODS` `_HEADERS` `_EXPOSE_HEADERS` `_MAX_AGE` | `*` / all / `*` / none / `86400` | CORS policy details |
| `API_AUTH_SCHEMES` `_SECRET` `_GRANTS` `_TOKEN_FORMAT` `_ACCESS_TTL` `_REFRESH_TTL` `_ISSUER` `_JWT_CHECK_REVOCATION` `_HMAC_TOLERANCE` `_BASIC_PASSWORDS` `_MAX_FAILURES` `_FAILURE_WINDOW` | `apikey,bearer` / none / all / `opaque` / `3600` / `2592000` / ... | The optional `apiauth` app - see [API authentication](#api-authentication-the-apiauth-app) |
| `FAKE_DATA_ENABLED` | `false` | Allow the optional `fakedata` dashboard app to generate data - see [Fake data](#fake-data-development) |
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
