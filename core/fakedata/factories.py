"""
factories (framework models)
------------------------------
Field overrides for the framework's own models, where "any plausible value" isn't good enough. Most models need none
- the generators work from the field types - but a fake User must be able to actually sign in.

The format is the same one an app uses in its own factories.py (see core/fakedata/service.py):

    {Model: {"column": value_or_callable, "__count__": 10, "__after__": callable}}

A callable receives `fake` (a Faker) - or `fake, ctx` if it takes a second argument; ctx.row holds the columns
generated so far, ctx.index the position in the batch, ctx.options the run's options. Return None for NULL, or
`SKIP` (from core.fakedata.generators) to leave the column to the database / the model.
"""

from typing import Any, Dict


def core_factories() -> Dict[type, Dict[str, Any]]:
    from core.fakedata.generators import SKIP
    from core.models.Role import Role
    from core.models.User import User

    return {
        User: {
            "__count__": 10,
            # a name people can log in with, unique-enough (a clash just regenerates the row)
            "name": lambda fake: fake.user_name()[:60],
            # every fake account gets the same known password (--password, default "password") so you can sign in as any of them
            "password": lambda fake, ctx: ctx.options.password,
            "status": lambda fake: fake.random.choices(["active", "pending", "suspended"], weights=[85, 10, 5])[0],
            # (ctx.row only has columns declared BEFORE this one, and is_verified comes before status - so don't read status here)
            "is_verified": lambda fake: fake.random.random() < 0.85,
            "role": Role.DEFAULT_KEY,
            # a JSON list the User model fills in from the role when it saves - a made-up value would corrupt it.
            # SKIP means "leave this column alone" (None would mean NULL).
            "permissions_raw": SKIP,
        },
    }
