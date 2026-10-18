"""
relations
-----------
The runtime half of model relationships (the field declarations are ForeignKey, OneToOneField and
ManyToManyField in core/database/fields.py): the reverse accessors installed on the related model, and
the many-to-many queryset with its add / remove / set / clear.

    class Media(BaseModel):
        owner = ForeignKey(column="uid", references=User, related_name="media_items")

    user.media_items                  # QuerySet of that user's Media - same API as any other
    user.media_items.create(name="x") # fills in uid for you
    media.owner                       # forward: the User

When are reverse accessors installed? As soon as both models exist: when the model holding the
ForeignKey is defined after the one it points at, immediately; otherwise the moment the pointed-at
model is defined (BaseModel keeps a table-name registry for this). It follows that `user.media_items`
exists once Media's module has been imported - import your models (or at least the ones that point at
a model) before relying on a reverse accessor.
"""

from typing import Any, Iterable, List, Set

from core.models.QuerySet import QuerySet


def default_related_name(owner) -> str:
    return f"{owner.__name__.lower()}_set"


def connect_reverse(field, owner) -> None:
    """Called for each relation field when its model is built: install the reverse accessor now, or park it
    until the related model is defined."""
    from core.models.BaseModel import BaseModel

    if field.related_name == "+":
        return
    target = field.target_model_if_ready()
    if target is not None:
        install_reverse(field, owner, target)
    else:
        BaseModel._pending_relations.setdefault(field.references_table, []).append((owner, field))


def resolve_pending(model) -> None:
    """A model was just defined: finish the relations other models declared towards its table."""
    from core.models.BaseModel import BaseModel

    for owner, field in BaseModel._pending_relations.pop(model.table_name, []):
        install_reverse(field, owner, model)


def install_reverse(field, owner, target) -> None:
    name = field.related_name or default_related_name(owner)

    if field.is_virtual:
        descriptor = ManyToManyDescriptor(field, owner)
    elif field.single_reverse:
        descriptor = ReverseOneToOne(owner, field)
    else:
        descriptor = ReverseForeignKey(owner, field)

    if hasattr(target, name):
        current = target.__dict__.get(name)
        same_declaration = (
            isinstance(current, (ReverseForeignKey, ReverseOneToOne, ManyToManyDescriptor))
            and current.owner.__name__ == owner.__name__
            and current.owner.table_name == owner.table_name
        )
        if not same_declaration:  # (a model class redefined under the same name replaces its own accessor)
            raise ValueError(
                f"{owner.__name__}.{field.name}: reverse accessor name '{name}' clashes with an existing "
                f"attribute of {target.__name__} - pick another with related_name=..."
            )
    setattr(target, name, descriptor)


# ---------------------------------------------------------------------- #
# reverse accessors
# ---------------------------------------------------------------------- #
class ReverseForeignKey:
    """user.media_items -> Media.objects.filter(uid=user.uid), which also create()s with that uid."""

    def __init__(self, owner, field):
        self.owner = owner
        self.field = field

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        key = getattr(instance, self.field.references_column)
        queryset = self.owner.objects(instance.db)
        if key is None:  # an unsaved object has no related rows (and NULL must not match rows with a NULL key)
            return queryset.none()
        queryset = queryset.filter(**{self.field.column: key})
        queryset._create_defaults = {self.field.column: key}
        return queryset

    def __set__(self, instance, value) -> None:
        raise AttributeError("a reverse relation is read-only - set the foreign key on the other side")


class ReverseOneToOne(ReverseForeignKey):
    """user.profile -> the single Profile (or None)."""

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        queryset = super().__get__(instance, cls)
        return queryset.first()


# ---------------------------------------------------------------------- #
# many-to-many
# ---------------------------------------------------------------------- #
class ManyToManyDescriptor:
    """The reverse side of a ManyToManyField (tag.posts) - same API as the forward side."""

    def __init__(self, field, owner):
        self.field = field
        self.owner = owner

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        return many_related_queryset(self.field, instance, forward=False)

    def __set__(self, instance, value) -> None:
        raise AttributeError("a many-to-many relation: use .add(), .remove(), .set() or .clear()")


def many_related_queryset(field, instance, forward: bool) -> "ManyRelatedQuerySet":
    """The QuerySet of related objects for `instance`, with add/remove/set/clear."""
    if forward:
        target = field.related_model()
        near, far = field.source_column, field.target_column
    else:
        target = field.owner
        near, far = field.target_column, field.source_column
    far_key = target.primary_key
    near_value = getattr(instance, instance.primary_key)
    through_pk = getattr(field.through, "primary_key", "id")

    queryset = ManyRelatedQuerySet(instance.db, target)
    queryset._m2m = (field.through_table, near, far, near_value, far_key, through_pk)
    if near_value is None:
        return queryset.none()  # unsaved: nothing related yet (add() will say to save first)
    key_sql = f"{target.table_name}.{far_key}"
    queryset._conditions.append(("__m2m__", "m2m", (key_sql, field.through_table, near, far, near_value), False))
    return queryset


class ManyRelatedQuerySet(QuerySet):
    """A QuerySet of related objects through a join table, plus the methods that edit the links."""

    _m2m = None  # (through table, near column, far column, this object's key, related model's key name, through pk name)

    def _link_context(self):
        through, near, far, near_value, far_key, through_pk = self._m2m
        if near_value is None:
            raise ValueError("save() the object before changing its many-to-many relations")
        return through, near, far, near_value, far_key, through_pk

    def _keys(self, objects: Iterable[Any], far_key: str) -> List[Any]:
        from core.models.BaseModel import BaseModel

        keys = []
        for item in objects:
            if isinstance(item, BaseModel):
                key = item.attributes.get(far_key)
                if key is None:
                    raise ValueError(f"save() the related {type(item).__name__} before linking it")
                keys.append(key)
            else:
                keys.append(item)  # a raw key
        return keys

    def _linked(self, through: str, near: str, far: str, near_value: Any) -> Set[Any]:
        rows = self.db.connect().table(through).select(far).where(near, "=", near_value).get()
        return {dict(row)[far] for row in rows}

    def add(self, *objects: Any, **through_defaults: Any) -> None:
        """Links the objects (instances or keys); already-linked ones are skipped. Extra keyword arguments
        fill other columns of the join table for the new links."""
        through, near, far, near_value, far_key, through_pk = self._link_context()
        linked = self._linked(through, near, far, near_value)
        for key in self._keys(objects, far_key):
            if key in linked:
                continue
            self.db.connect().table(through).insert({near: near_value, far: key, **through_defaults}, primary_key=through_pk)
            linked.add(key)

    def remove(self, *objects: Any) -> int:
        """Unlinks the objects (instances or keys); returns how many links were removed."""
        through, near, far, near_value, far_key, _ = self._link_context()
        keys = self._keys(objects, far_key)
        if not keys:
            return 0
        return self.db.connect().table(through).where(near, "=", near_value).where_in(far, keys).delete()

    def clear(self) -> int:
        """Unlinks everything; returns how many links were removed."""
        through, near, far, near_value, far_key, _ = self._link_context()
        return self.db.connect().table(through).where(near, "=", near_value).delete()

    def set(self, objects: Iterable[Any], **through_defaults: Any) -> None:
        """Makes the linked objects exactly `objects`: adds the missing, removes the extra."""
        through, near, far, near_value, far_key, _ = self._link_context()
        wanted = set(self._keys(objects, far_key))
        linked = self._linked(through, near, far, near_value)
        if linked - wanted:
            self.remove(*(linked - wanted))
        if wanted - linked:
            self.add(*(wanted - linked), **through_defaults)
