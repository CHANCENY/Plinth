"""
FakeDataService
-----------------
Fills your database with realistic fake data, driven by the models you already have - no per-model code needed.
Used by the command line (python console.py fake:...) and by the optional `fakedata` dashboard app; call it from your
own code or tests the same way:

    from core.fakedata.service import FakeDataService, FakeOptions

    service = FakeDataService(db, FakeOptions(seed=42, locale="fr_FR"))
    result = service.generate([(User, 20), (Post, 100)])   # fake users, then posts that belong to them
    service.preview(Post, 3)                               # sample rows, nothing is saved
    service.undo(result.run_id)                            # delete exactly what that run created

How it decides what to generate
    * Every column gets a value that fits its field type and limits (max_length, DECIMAL digits, ENUM choices ...)
      and, where the name suggests it, something fitting: email -> an email address, price -> a price, title -> a
      headline. See core/fakedata/generators.py. Auto-managed columns (the id, updated_at, deleted_at, auto UUIDs)
      are left alone, and created_at is back-dated.
    * Foreign keys point at rows that exist. If the related table is empty the service creates a few related rows
      first (turn that off with create_related=False). Many-to-many relations get a few links.
    * Unique columns and UniqueTogether constraints never collide - with each other or with rows already in the
      table; a clashing row is simply regenerated.
    * Models are generated parents-first, so User=5 Post=20 works whatever order you list them in.

Teaching it about your own models (optional): an app's factories.py
    A model that needs more than guesswork overrides single columns, and can set its default row count:

        # public/apps/blog/factories.py
        from public.apps.blog.models.Post import Post

        FACTORIES = {
            Post: {
                "__count__": 20,                                          # default rows when none is requested
                "title": lambda fake: fake.sentence(nb_words=6),          # a callable gets a Faker ...
                "slug": lambda fake, ctx: slugify(ctx.row["title"]),      # ... or (Faker, context) - ctx.row = columns so far
                "body": "Lorem ipsum",                                    # or just a constant
                "__after__": lambda instance, fake, ctx: ...,             # runs after each row is saved
            },
        }

    `python console.py apps:discover` validates these (a misspelt column fails there, not mid-run).

Faker is an optional dependency: `pip install Faker`. Everything else works without it; this raises
FakeDataUnavailable with that instruction when it's needed.

Safety
    Every run is recorded in storage/fakedata/runs.json so it can be undone, and undo refuses to delete from a different
    database than the run was made against. Models with fakeable = False (sessions, tokens, roles ...) are never touched.
"""

import datetime
import importlib
import inspect
import os
import secrets
import time
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.database.fields import AutoField, UniqueTogether
from core.fakedata.generators import SKIP, FakeContext, FakeOptions, column_value
from core.fakedata.runs import RunLog

MAX_ATTEMPTS = 40          # tries to produce a row that doesn't clash with a unique constraint
POOL_SIZE = 500            # existing related rows considered when picking a foreign key
MAX_RELATED_DEPTH = 2      # how many levels of "make the related rows first" are allowed
SPECIAL_KEYS = ("__count__", "__after__")


class FakeDataError(Exception):
    """Something the person running this can fix (a missing table, an unknown model, an unsatisfiable unique column ...)."""


class FakeDataUnavailable(FakeDataError):
    """Faker isn't installed."""


@dataclass
class ModelInfo:
    model: Any
    name: str
    table: str
    columns: int
    default_count: int
    rows: Optional[int]       # None when the table can't be read (not migrated yet)
    customised: bool          # has a factory override


@dataclass
class RunResult:
    run_id: Optional[str]
    created: Dict[str, int] = dataclass_field(default_factory=dict)  # table -> rows inserted, in creation order
    seconds: float = 0.0
    note: str = ""

    @property
    def total(self) -> int:
        return sum(self.created.values())


@dataclass
class UndoResult:
    run_id: str
    deleted: Dict[str, int] = dataclass_field(default_factory=dict)
    problems: List[str] = dataclass_field(default_factory=list)


def display(value: Any) -> Any:
    """A value as text fit for a preview table (bytes, Decimals, dates and JSON all become readable strings)."""
    import json

    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return value.isoformat()
    return str(value) if not isinstance(value, (int, float, str, bool)) else value


class FakeDataService:
    def __init__(self, db_connection, options: Optional[FakeOptions] = None, log: Optional[RunLog] = None):
        self.db = db_connection
        self.options = options or FakeOptions()
        self.log = log or RunLog()
        self._overrides_cache: Optional[Dict[type, Dict[str, Any]]] = None

    # ------------------------------------------------------------------ #
    # availability
    # ------------------------------------------------------------------ #
    @staticmethod
    def faker_available() -> bool:
        try:
            import faker  # noqa: F401
        except ImportError:
            return False
        return True

    def check_ready(self) -> None:
        """Raises FakeDataUnavailable / FakeDataError now if Faker is missing or the locale is unknown - so callers can
        complain before asking for confirmation rather than after."""
        self._new_faker()

    def _new_faker(self):
        try:
            from faker import Faker
            from faker.config import AVAILABLE_LOCALES
        except ImportError as exc:
            raise FakeDataUnavailable("Faker isn't installed - run: pip install Faker") from exc
        if self.options.locale not in AVAILABLE_LOCALES:
            raise FakeDataError(
                f"unknown locale '{self.options.locale}' - try en_US, en_GB, fr_FR, de_DE, es_ES, it_IT, pt_BR, nl_NL, ja_JP ..."
            )
        fake = Faker(self.options.locale)
        if self.options.seed is not None:
            fake.seed_instance(self.options.seed)
        return fake

    # ------------------------------------------------------------------ #
    # which models, and how they're customised
    # ------------------------------------------------------------------ #
    def models(self) -> List[type]:
        """Every model that can be faked: the framework's and the registered apps', minus the ones marked fakeable = False."""
        from core.database.Migrator import MODELS, Migrator

        found: List[type] = []
        for model in list(MODELS) + list(Migrator._app_models()):
            if model not in found and getattr(model, "fakeable", True) and model._fields and model.table_name:
                found.append(model)
        return found

    def is_fakeable(self, model: type) -> bool:
        return model in self.models()

    def resolve(self, name: str) -> type:
        """A model from its class name or table name, case-insensitively ("Post", "blog_posts", "user")."""
        wanted = name.strip().lower()
        candidates = self.models()
        for model in candidates:
            if wanted in (model.__name__.lower(), model.table_name.lower()):
                return model
        known = ", ".join(sorted(m.__name__ for m in candidates))
        raise FakeDataError(f"unknown model '{name}' - the ones that can be faked are: {known}")

    def overrides(self, model: type) -> Dict[str, Any]:
        """The factory overrides for a model: the framework's, then each registered app's factories.py."""
        if self._overrides_cache is None:
            from core.apps.AppManager import AppManager
            from core.fakedata.factories import core_factories

            merged: Dict[type, Dict[str, Any]] = {m: dict(o) for m, o in core_factories().items()}
            for entry in (AppManager().load_cache() or {}).get("factories", []):
                module = importlib.import_module(entry["module"])
                for factory_model, overrides in getattr(module, "FACTORIES", {}).items():
                    merged.setdefault(factory_model, {}).update(overrides)
            self._overrides_cache = merged
        return self._overrides_cache.get(model, {})

    def default_count(self, model: type) -> int:
        count = self.overrides(model).get("__count__", 10)
        return count if isinstance(count, int) and count > 0 else 10

    def catalog(self) -> List[ModelInfo]:
        infos = []
        for model in self.models():
            try:
                rows: Optional[int] = model.objects(self.db).count()
            except Exception:  # the table isn't there (not migrated yet)
                rows = None
            infos.append(ModelInfo(model, model.__name__, model.table_name, len(model._column_names),
                                   self.default_count(model), rows, bool(self.overrides(model))))
        return infos

    # ------------------------------------------------------------------ #
    # preview / generate / undo
    # ------------------------------------------------------------------ #
    def preview(self, model: type, count: int = 5) -> List[Dict[str, Any]]:
        """Sample rows as they would be generated - nothing is saved, no related rows are created."""
        session = _Session(self, self._new_faker(), dry_run=True)
        return [{k: display(v) for k, v in session.build_unique_row(model, i, depth=0).items()} for i in range(count)]

    def generate(self, targets: Sequence[Tuple[type, int]], note: str = "") -> RunResult:
        """Creates the requested rows - [(Model, count), ...] - parents first, and records the run so it can be undone."""
        if not targets:
            raise FakeDataError("nothing to generate - name at least one model")
        for model, count in targets:
            if count < 1:
                raise FakeDataError(f"the count for {model.__name__} must be at least 1")

        started = time.time()
        session = _Session(self, self._new_faker(), dry_run=False)
        counts = {model: count for model, count in targets}
        try:
            for model in self._dependency_order(list(counts)):
                session.create_many(model, counts[model], depth=0)
        finally:
            # even if something failed half way, what WAS created is recorded - so it can still be undone
            run_id = session.save_run(note)

        return RunResult(run_id, {m.table_name: len(ids) for m, ids in session.created.items()}, time.time() - started, note)

    def runs(self) -> List[Dict[str, Any]]:
        return self.log.all()

    def undo(self, run_id: Optional[str] = None, force: bool = False) -> UndoResult:
        """Deletes the rows a run created (the newest run if none is named). Refuses if the run was made against a
        different database than the current one, unless force=True. A run that couldn't be fully undone stays in
        the log so it can be retried."""
        runs = self.log.all()
        run = self.log.get(run_id) if run_id else (runs[0] if runs else None)
        if run is None:
            raise FakeDataError(f"no such run '{run_id}'" if run_id else "there are no recorded runs to undo")
        if run.get("database") != database_id() and not force:
            raise FakeDataError(
                f"run {run['id']} was made against {run.get('database')}, but the current database is {database_id()} - "
                f"refusing to delete by id from a different database (pass force to override)"
            )

        result = UndoResult(run["id"])
        models = {m.table_name: m for m in self.models()}

        for link in reversed(run.get("links", [])):  # many-to-many join rows first
            try:
                for chunk in _chunks(link["keys"], 400):
                    self.db.connect().table(link["through"]).where_in(link["source_column"], chunk).delete()
            except Exception as exc:
                result.problems.append(f"{link['through']}: {exc}")

        for item in reversed(run["items"]):  # children before parents
            model = models.get(item["table"])
            if model is None:
                result.problems.append(f"{item['table']}: that model isn't available any more")
                continue
            deleted, problem = self._delete_rows(model, item["pk"], item["ids"])
            result.deleted[item["table"]] = result.deleted.get(item["table"], 0) + deleted
            if problem:
                result.problems.append(f"{item['table']}: {problem}")

        if not result.problems:
            self.log.remove(run["id"])
        return result

    def _delete_rows(self, model: type, pk: str, ids: List[Any]) -> Tuple[int, Optional[str]]:
        deleted = 0
        try:
            for chunk in _chunks(ids, 400):
                deleted += model.objects(self.db).filter(**{f"{pk}__in": chunk}).delete()
            return deleted, None
        except Exception:
            pass
        # a foreign key between rows of this same table (a tree) can reject a bulk delete - go newest first, one by one
        deleted, problem = 0, None
        for key in reversed(ids):
            try:
                deleted += model.objects(self.db).filter(**{pk: key}).delete()
            except Exception as exc:
                problem = f"couldn't delete {pk}={key}: {exc}"
        return deleted, problem

    @staticmethod
    def _dependency_order(models: List[type]) -> List[type]:
        order: List[type] = []
        visiting: set = set()

        def visit(model: type) -> None:
            if model in order or model in visiting:
                return
            visiting.add(model)
            for field in model._fields.values():
                if field.is_relation and not field.is_virtual:
                    target = field.target_model_if_ready()
                    if target in models and target is not model:
                        visit(target)
            visiting.discard(model)
            order.append(model)

        for model in models:
            visit(model)
        return order


def database_id() -> str:
    """Which database this process points at - recorded with every run so undo can't hit the wrong one."""
    return f"{os.getenv('DB_DRIVER', 'mysql')}:{os.getenv('DB_HOST', '')}:{os.getenv('DB_NAME', '')}"


def _chunks(items: List[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------------- #
# one generate()/preview() call
# ---------------------------------------------------------------------- #
class _Session:
    def __init__(self, service: FakeDataService, fake: Any, dry_run: bool):
        self.service = service
        self.db = service.db
        self.options = service.options
        self.fake = fake
        self.dry_run = dry_run
        self.created: Dict[type, List[Any]] = {}            # model -> primary keys created, in order
        self.links: List[Dict[str, Any]] = []               # many-to-many links created
        self.pools: Dict[Tuple[type, str], List[Any]] = {}  # (model, column) -> keys a foreign key may point at
        self.seen: Dict[Tuple[str, Any], set] = {}          # unique values handed out in this run

    # ---- creating rows ------------------------------------------------ #
    def create_many(self, model: type, count: int, depth: int) -> None:
        for index in range(count):
            self.create_one(model, index, depth)

    def create_one(self, model: type, index: int, depth: int):
        row = self.build_unique_row(model, index, depth)
        instance = model(self.db, **row)
        instance.save()

        pk = instance.attributes.get(model.primary_key)
        self.created.setdefault(model, []).append(_jsonable(pk))
        for (target, column), pool in self.pools.items():
            if target is model:
                pool.append(instance.attributes.get(column))

        self._attach_many_to_many(instance, depth)
        after = self.service.overrides(model).get("__after__")
        if callable(after):
            after(instance, self.fake, FakeContext(self.fake, index, self.options, model, self.db, dict(row)))
        return instance

    def build_unique_row(self, model: type, index: int, depth: int) -> Dict[str, Any]:
        """A row that won't violate a unique constraint - regenerated until it doesn't (or we give up and say why)."""
        for _ in range(MAX_ATTEMPTS):
            ctx = FakeContext(self.fake, index, self.options, model, self.db)
            row = self._build_row(model, ctx, depth)
            clash = self._clash(model, row)
            if clash is None:
                self._remember(model, row)
                return row
        raise FakeDataError(
            f"couldn't generate a unique value for {model.__name__}.{clash} after {MAX_ATTEMPTS} tries - "
            f"the column is too constrained for random data (give it a factory in factories.py)"
        )

    def _build_row(self, model: type, ctx: FakeContext, depth: int) -> Dict[str, Any]:
        overrides = self.service.overrides(model)
        foreign_keys = {f.column: f for f in model._fields.values() if f.is_relation and not f.is_virtual}
        for name in model._column_names:
            field = model._fields[name]
            if name in overrides and name not in SPECIAL_KEYS:
                value = _call(overrides[name], ctx)
            elif name in foreign_keys:
                value = self._related_key(foreign_keys[name], field, ctx, depth)
            else:
                value = column_value(name, field, ctx)
            if value is not SKIP:
                ctx.row[name] = value
        return dict(ctx.row)

    # ---- relations ---------------------------------------------------- #
    def _pool(self, target: type, column: str) -> List[Any]:
        key = (target, column)
        if key not in self.pools:
            try:
                self.pools[key] = list(target.objects(self.db).order_by(column).values_list(column, flat=True)[:POOL_SIZE])
            except Exception:
                self.pools[key] = []
        return self.pools[key]

    def _related_key(self, fk: Any, column_field: Any, ctx: FakeContext, depth: int):
        target = fk.target_model_if_ready()
        model = ctx.model
        if target is None:
            if column_field.null:
                return None
            raise FakeDataError(f"{model.__name__}.{fk.name}: the related model for '{fk.references_table}' isn't loaded")

        pool = self._pool(target, fk.references_column)
        if not pool and not self.dry_run and target is not model and self.options.create_related \
                and depth < MAX_RELATED_DEPTH and self.service.is_fakeable(target):
            self.create_many(target, self.options.related_count, depth + 1)
            pool = self._pool(target, fk.references_column)

        if not pool:
            if column_field.null or self.dry_run:
                return None
            raise FakeDataError(
                f"{model.__name__}.{fk.name} needs rows in {target.__name__}, and there are none - generate {target.__name__} "
                f"first (or allow related rows to be created)"
            )
        if column_field.null and self.fake.random.random() < 0.2:
            return None
        return self.fake.random.choice(pool)

    def _attach_many_to_many(self, instance: Any, depth: int) -> None:
        model = type(instance)
        for name, field in model._fields.items():
            if not field.is_virtual:
                continue
            target = field.target_model_if_ready()
            if target is None:
                continue
            pool = self._pool(target, target.primary_key)
            if not pool and self.options.create_related and depth < MAX_RELATED_DEPTH and self.service.is_fakeable(target):
                self.create_many(target, self.options.related_count, depth + 1)
                pool = self._pool(target, target.primary_key)
            if not pool:
                continue
            chosen = self.fake.random.sample(pool, self.fake.random.randint(0, min(3, len(pool))))
            if chosen:
                getattr(instance, name).add(*chosen)
                self.links.append({"through": field.through_table, "source_column": field.source_column,
                                   "keys": [_jsonable(instance.attributes.get(model.primary_key))]})

    # ---- uniqueness --------------------------------------------------- #
    def _clash(self, model: type, row: Dict[str, Any]) -> Optional[str]:
        """The name of a unique column (or constraint) this row would violate, or None."""
        for name, field in model._fields.items():
            if field.is_constraint or field.is_virtual:
                continue
            if not (field.unique or (field.primary_key and not isinstance(field, AutoField))):
                continue
            value = row.get(name)
            if value is None:
                continue
            if self._seen_has(model, name, value) or model.objects(self.db).filter(**{name: value}).exists():
                return name

        for name, field in model._fields.items():
            if isinstance(field, UniqueTogether):
                combo = tuple(row.get(column) for column in field.columns)
                if None in combo:
                    continue
                if self._seen_has(model, tuple(field.columns), combo) or \
                        model.objects(self.db).filter(**dict(zip(field.columns, combo))).exists():
                    return ", ".join(field.columns)
        return None

    def _seen_has(self, model: type, key: Any, value: Any) -> bool:
        try:
            return value in self.seen.get((model.table_name, key), set())
        except TypeError:  # an unhashable value (a JSON dict) - can't be tracked, the database check still applies
            return False

    def _remember(self, model: type, row: Dict[str, Any]) -> None:
        for name, field in model._fields.items():
            if field.is_constraint or field.is_virtual:
                continue
            if (field.unique or (field.primary_key and not isinstance(field, AutoField))) and row.get(name) is not None:
                try:
                    self.seen.setdefault((model.table_name, name), set()).add(row[name])
                except TypeError:
                    pass
        for field in model._fields.values():
            if isinstance(field, UniqueTogether):
                combo = tuple(row.get(c) for c in field.columns)
                if None not in combo:
                    try:
                        self.seen.setdefault((model.table_name, tuple(field.columns)), set()).add(combo)
                    except TypeError:
                        pass

    # ---- the log ------------------------------------------------------ #
    def save_run(self, note: str) -> Optional[str]:
        if not self.created:
            return None
        run_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        self.service.log.add(
            {
                "id": run_id,
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "database": database_id(),
                "locale": self.options.locale,
                "seed": self.options.seed,
                "note": note,
                "items": [{"model": m.__name__, "table": m.table_name, "pk": m.primary_key, "ids": ids}
                          for m, ids in self.created.items()],
                "links": self.links,
            }
        )
        return run_id


def _call(spec: Any, ctx: FakeContext) -> Any:
    """An override can be a constant, a callable(fake) or a callable(fake, ctx)."""
    if not callable(spec):
        return spec
    try:
        positional = [p for p in inspect.signature(spec).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        positional = [None]
    if not positional:
        return spec()
    return spec(ctx.fake) if len(positional) == 1 else spec(ctx.fake, ctx)


def _jsonable(value: Any) -> Any:
    """A primary key as something the JSON run log can hold (UUIDs and Decimals become text)."""
    return value if isinstance(value, (int, str)) or value is None else str(value)
