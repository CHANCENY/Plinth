"""
generators
------------
Turns a model field into a plausible fake value, using what the field itself declares - its type, max_length,
digits, choices, nullability - plus its name (an `email` column gets an email address, `price` a sensible price,
`created_at` a past date). Nothing here knows about any particular model; a model that needs something specific
overrides single fields in a factories.py (see core/fakedata/service.py).

Everything random goes through Faker's own random generator (`fake.random`), so FakeOptions.seed makes a whole run
reproducible: the same seed on the same data gives the same rows.

Auto-managed columns are left alone so the framework keeps doing its job: the auto-increment id, updated_at,
deleted_at, and UUIDs a model generates itself (UUIDField(auto=True)). created_at is back-dated into the past so
lists of fake rows don't all show "just now".
"""

import datetime
import decimal
import re
import uuid
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Optional

from core.database.fields import (
    AutoField, BigIntegerField, BinaryField, BooleanField, CharField, DateField, DateTimeField, DecimalField,
    EnumField, FloatField, IntegerField, JSONField, SmallIntegerField, TextField, TimeField, UUIDField,
)

SKIP = object()  # "leave this column to the database / the model" - not the same as None (NULL)

# names whose NULL-ness would look silly (a post with no title); null_ratio never blanks these
NEVER_NULL = {"name", "title", "slug", "email", "username", "label", "first_name", "last_name", "full_name"}
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
FUTURE_HINTS = ("expires", "expiry", "due", "deadline", "end", "ends", "until", "scheduled", "valid_until")


@dataclass
class FakeOptions:
    locale: str = "en_US"
    seed: Optional[int] = None
    password: str = "password"        # the plain password fake accounts get (hashed by the User model)
    null_ratio: float = 0.1           # how often a nullable column is left NULL
    create_related: bool = True       # make rows in a related table when it has none yet
    related_count: int = 3            # ... this many


@dataclass
class FakeContext:
    """What a field factory can look at: `fake` (a Faker), `index` (0-based position in this batch), `row` (the
    columns generated so far, in declaration order), `options`, `model`, and `db`."""

    fake: Any
    index: int
    options: FakeOptions
    model: Any
    db: Any
    row: Dict[str, Any] = dataclass_field(default_factory=dict)


# ---------------------------------------------------------------------- #
# name hints
# ---------------------------------------------------------------------- #
def _has(name: str, *keys: str) -> bool:
    lowered = name.lower()
    tokens = lowered.split("_")
    return any(key == lowered or key in tokens for key in keys)


def _fit(text: str, max_length: Optional[int]) -> str:
    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def char_value(name: str, field: CharField, ctx: FakeContext) -> str:
    fake = ctx.fake
    if _has(name, "email"):
        value = fake.email()
    elif _has(name, "first", "firstname", "given"):
        value = fake.first_name()
    elif _has(name, "last", "lastname", "surname", "family"):
        value = fake.last_name()
    elif _has(name, "username", "login", "handle", "nickname"):
        value = fake.user_name()
    elif _has(name, "fullname"):
        value = fake.name()
    elif name == "name":
        person = any(key in ctx.model._fields for key in ("email", "first_name", "last_name", "username", "password"))
        value = fake.name() if person else " ".join(w.capitalize() for w in fake.words(nb=2))
    elif _has(name, "title", "subject", "headline", "label"):
        value = fake.sentence(nb_words=5).rstrip(".")
    elif _has(name, "slug"):
        value = fake.slug()
    elif _has(name, "phone", "mobile", "tel", "telephone", "fax"):
        value = fake.phone_number()
    elif _has(name, "street", "address"):
        value = fake.street_address()
    elif _has(name, "city", "town"):
        value = fake.city()
    elif _has(name, "state", "province", "region"):
        value = fake.state() if hasattr(fake, "state") else fake.city()
    elif _has(name, "country"):
        value = fake.country()
    elif _has(name, "zip", "postal", "postcode"):
        value = fake.postcode()
    elif _has(name, "url", "website", "link", "homepage", "site"):
        value = fake.url()
    elif _has(name, "domain", "hostname", "host"):
        value = fake.domain_name()
    elif _has(name, "company", "organization", "organisation", "employer", "business"):
        value = fake.company()
    elif _has(name, "job", "position", "occupation"):
        value = fake.job()
    elif _has(name, "color", "colour"):
        value = fake.color_name()
    elif _has(name, "ip", "ipaddress"):
        value = fake.ipv4()
    elif _has(name, "agent", "useragent"):
        value = fake.user_agent()
    elif _has(name, "currency"):
        value = fake.currency_code()
    elif _has(name, "filename", "file"):
        value = fake.file_name()
    elif _has(name, "mime", "mimetype"):
        value = fake.mime_type()
    elif _has(name, "language", "lang", "locale"):
        value = fake.language_code()
    elif _has(name, "timezone", "tz"):
        value = fake.timezone()
    elif _has(name, "password", "secret", "token"):
        value = fake.password(length=14)
    elif _has(name, "tag", "category", "keyword", "genre", "type", "kind"):
        value = fake.word()
    elif _has(name, "description", "summary", "bio", "about", "excerpt", "comment", "message", "note", "notes", "body", "content"):
        value = fake.sentence(nb_words=12)
    else:
        limit = field.max_length or 255
        if limit < 12:
            value = fake.word()
        elif limit < 60:
            value = fake.sentence(nb_words=3).rstrip(".")
        else:
            value = fake.sentence(nb_words=6).rstrip(".")
    return _fit(str(value), field.max_length)


def text_value(name: str, field: TextField, ctx: FakeContext) -> str:
    fake = ctx.fake
    if _has(name, "excerpt", "summary", "bio", "about", "description", "comment", "message", "note", "notes"):
        return " ".join(fake.sentences(nb=2))
    paragraphs = fake.random_int(2, 4)
    return "\n\n".join(fake.paragraphs(nb=paragraphs))


# ---------------------------------------------------------------------- #
# numbers
# ---------------------------------------------------------------------- #
def _int_range(name: str, field: Any) -> tuple:
    if _has(name, "age"):
        low, high = 18, 90
    elif _has(name, "year"):
        low, high = 1990, 2026
    elif _has(name, "rating", "score", "stars", "rank"):
        low, high = 1, 5
    elif _has(name, "percent", "percentage", "discount"):
        low, high = 0, 100
    elif _has(name, "quantity", "qty", "stock", "count", "number", "views", "likes", "downloads", "inventory"):
        low, high = 0, 500
    else:
        low, high = 0, 10_000
    if isinstance(field, SmallIntegerField):
        high = min(high, 30_000)
    return low, high


def _decimal_range(name: str) -> tuple:
    if _has(name, "price", "cost", "amount", "total", "salary", "balance", "fee", "subtotal", "revenue"):
        return 1.0, 999.0
    if _has(name, "percent", "percentage", "discount", "rate", "tax"):
        return 0.0, 100.0
    if _has(name, "rating", "score", "stars"):
        return 1.0, 5.0
    if _has(name, "weight", "height", "length", "width", "size"):
        return 1.0, 200.0
    return 0.0, 1000.0


def decimal_value(name: str, field: DecimalField, ctx: FakeContext) -> decimal.Decimal:
    places = field.decimal_places
    limit = 10 ** (field.max_digits - places) - 10 ** -places  # the largest value the column can hold
    low, high = _decimal_range(name)
    high = min(high, float(limit))
    low = min(low, high)
    raw = ctx.fake.random.uniform(low, high)
    return decimal.Decimal(str(round(raw, places))).quantize(decimal.Decimal(1).scaleb(-places))


def float_value(name: str, field: FloatField, ctx: FakeContext) -> float:
    if _has(name, "lat", "latitude"):
        return float(ctx.fake.latitude())
    if _has(name, "lng", "lon", "longitude"):
        return float(ctx.fake.longitude())
    low, high = _decimal_range(name)
    return round(ctx.fake.random.uniform(low, high), 2)


# ---------------------------------------------------------------------- #
# dates
# ---------------------------------------------------------------------- #
def _is_future(name: str) -> bool:
    return any(_has(name, hint) for hint in FUTURE_HINTS)


def date_value(name: str, field: DateField, ctx: FakeContext) -> datetime.date:
    if _is_future(name):
        return ctx.fake.date_between(start_date="today", end_date="+1y")
    if _has(name, "birth", "birthday", "dob"):
        return ctx.fake.date_of_birth(minimum_age=18, maximum_age=80)
    return ctx.fake.date_between(start_date="-5y", end_date="today")


def datetime_value(name: str, field: DateTimeField, ctx: FakeContext) -> str:
    if _is_future(name):
        moment = ctx.fake.date_time_between(start_date="now", end_date="+1y")
    else:
        moment = ctx.fake.date_time_between(start_date="-2y", end_date="now")
    return moment.strftime(DATETIME_FORMAT)  # as text, the way the framework's own code stores these columns


# ---------------------------------------------------------------------- #
# dispatch
# ---------------------------------------------------------------------- #
def column_value(name: str, field: Any, ctx: FakeContext) -> Any:
    """A fake value for one ordinary column, or SKIP to leave it to the database / model."""
    fake = ctx.fake

    if isinstance(field, AutoField) or field.auto_default or name in ("updated_at", "deleted_at"):
        return SKIP
    if name == "created_at" and isinstance(field, DateTimeField):
        return datetime_value("created_at", field, ctx) if ctx.model.timestamps else SKIP

    if field.null and name not in NEVER_NULL and not field.unique and fake.random.random() < ctx.options.null_ratio:
        return None

    if isinstance(field, EnumField):
        return fake.random.choice(field.choices)
    if isinstance(field, BooleanField):
        return fake.boolean(chance_of_getting_true=65)
    if isinstance(field, DecimalField):
        return decimal_value(name, field, ctx)
    if isinstance(field, FloatField):
        return float_value(name, field, ctx)
    if isinstance(field, (IntegerField, BigIntegerField, SmallIntegerField)):
        low, high = _int_range(name, field)
        return fake.random_int(low, high)
    if isinstance(field, UUIDField):
        return uuid.UUID(fake.uuid4())
    if isinstance(field, DateTimeField):  # TimestampField is a DateTimeField
        return datetime_value(name, field, ctx)
    if isinstance(field, DateField):
        return date_value(name, field, ctx)
    if isinstance(field, TimeField):
        return fake.time_object()
    if isinstance(field, JSONField):
        return {"tags": fake.words(nb=3), "score": fake.random_int(0, 100), "note": fake.sentence(nb_words=6)}
    if isinstance(field, BinaryField):
        length = min(field.max_length or 32, 64)
        return fake.binary(length=length)
    if isinstance(field, TextField):
        return text_value(name, field, ctx)
    if isinstance(field, CharField):
        return char_value(name, field, ctx)
    return SKIP  # a field type this module doesn't know - leave it to its database default
