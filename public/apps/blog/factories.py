"""
How the fake-data tools should build blog posts (python console.py fake:generate Post, or the dashboard's Fake data
page). Without this they'd still work - titles, bodies and unique slugs are guessed from the field names - but this
gives posts that read like posts: a headline, a slug made from that headline, and a few paragraphs.

Format and rules: see core/apps/AppManager.py (factories.py) and core/fakedata/service.py.
"""

import re

from public.apps.blog.models.Post import Post


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


FACTORIES = {
    Post: {
        "__count__": 20,
        "title": lambda fake: fake.sentence(nb_words=fake.random_int(4, 9)).rstrip("."),
        # ctx.row holds the columns generated so far (title comes before slug in the model), so the slug matches the title;
        # a random suffix keeps two posts with similar headlines from colliding
        "slug": lambda fake, ctx: f"{slugify(ctx.row['title'])[:60]}-{fake.random_int(100, 999)}",
        "body": lambda fake: "\n\n".join(fake.paragraphs(nb=fake.random_int(3, 6))),
    },
}
