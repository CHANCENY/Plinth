"""
MongoSchemaBuilder
--------------------
Collection-oriented equivalent of the SQL SchemaBuilders. Doesn't subclass
BaseSchemaBuilder since MongoDB has no columns/DDL in the SQL sense - it just
wraps pymongo's Database methods with the same create/drop/empty/has naming
used by the SQL schema builders, so calling code doesn't need to branch on
driver. table()-named methods are aliases for the collection()-named ones.
"""


class MongoSchemaBuilder:
    def __init__(self, database):
        self.database = database  # pymongo Database

    # ------------------------------- create ------------------------------ #
    def create_collection(self, name: str, **options):
        """options are passed straight through to pymongo, e.g. capped=True, size=..., validator=..."""
        return self.database.create_collection(name, **options)

    def create_table(self, name: str, **options):
        return self.create_collection(name, **options)

    # -------------------------------- drop --------------------------------- #
    def drop_collection(self, name: str):
        return self.database.drop_collection(name)

    def drop_table(self, name: str):
        return self.drop_collection(name)

    # -------------------------------- empty -------------------------------- #
    def empty_collection(self, name: str):
        """Removes all documents but keeps the collection (and its indexes) in place."""
        return self.database[name].delete_many({})

    def empty_table(self, name: str):
        return self.empty_collection(name)

    # -------------------------------- exists -------------------------------- #
    def has_collection(self, name: str) -> bool:
        return name in self.database.list_collection_names()

    def has_table(self, name: str) -> bool:
        return self.has_collection(name)
