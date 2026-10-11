"""
DatabaseConnection
-------------------
Reads DB_DRIVER (and the matching credentials) from the project's .env file,
opens a connection to whichever engine is configured, and hands back the
right tool for that engine:

    DB_DRIVER=mysql     -> connect() returns a MySQLQueryBuilder
    DB_DRIVER=postgres  -> connect() returns a PostgresQueryBuilder
    DB_DRIVER=sqlite    -> connect() returns a SQLiteQueryBuilder
    DB_DRIVER=mongodb   -> connect() returns the pymongo Database (native API)

Project layout assumed:
    project/
        .env
        core/
            database/
                DatabaseConnection.py   <- this file
                query_builders/
                    BaseQueryBuilder.py
                    MySQLQueryBuilder.py
                    PostgresQueryBuilder.py
                    SQLiteQueryBuilder.py
        public/
            main.py                    <- webroot / entry point

Expected .env keys
-------------------
DB_DRIVER=mysql | postgres | sqlite | mongodb

mysql / postgres:
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

sqlite:
    DB_NAME (path to the .sqlite file, relative to project root, or absolute)

mongodb:
    DB_URI                       (preferred - full connection string), OR
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

Usage
-----
    from core.database.DatabaseConnection import DatabaseConnection

    db = DatabaseConnection()

    # mysql / postgres / sqlite -> connect() gives you a QueryBuilder directly
    query = db.connect()
    users = query.table("users").where("active", 1).get()
    posts = query.table("posts").where("author_id", 5).get()   # reusable, resets on .table()

    # mongodb -> connect() gives you the pymongo Database, use its native API
    mongo = db.connect()
    users = mongo["users"].find({"active": True})

    # DDL, same pattern - schema() gives you a SchemaBuilder (SQL) or MongoSchemaBuilder (mongo)
    schema = db.schema()
    schema.create_table("users", ["id INTEGER PRIMARY KEY", "name TEXT NOT NULL"])
    schema.empty_table("users")   # TRUNCATE / DELETE-all, depending on engine
    schema.drop_table("users")

    db.close()

Or, as a context manager (the value bound is whatever connect() would return):

    with DatabaseConnection() as query:
        rows = query.table("users").get()
"""

import os
from typing import Optional, Union

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional - .env can also be read from the real env
    load_dotenv = None

from core.database.query_builders.BaseQueryBuilder import BaseQueryBuilder
from core.database.query_builders.MySQLQueryBuilder import MySQLQueryBuilder
from core.database.query_builders.PostgresQueryBuilder import PostgresQueryBuilder
from core.database.query_builders.SQLiteQueryBuilder import SQLiteQueryBuilder
from core.database.schema_builders.MongoSchemaBuilder import MongoSchemaBuilder
from core.database.schema_builders.MySQLSchemaBuilder import MySQLSchemaBuilder
from core.database.schema_builders.PostgresSchemaBuilder import PostgresSchemaBuilder
from core.database.schema_builders.SQLiteSchemaBuilder import SQLiteSchemaBuilder

SQL_DRIVERS = ("mysql", "postgres", "postgresql", "sqlite")
MONGO_DRIVERS = ("mongodb", "mongo")
SUPPORTED_DRIVERS = SQL_DRIVERS + MONGO_DRIVERS

QUERY_BUILDER_MAP = {
    "mysql": MySQLQueryBuilder,
    "postgres": PostgresQueryBuilder,
    "postgresql": PostgresQueryBuilder,
    "sqlite": SQLiteQueryBuilder,
}

SCHEMA_BUILDER_MAP = {
    "mysql": MySQLSchemaBuilder,
    "postgres": PostgresSchemaBuilder,
    "postgresql": PostgresSchemaBuilder,
    "sqlite": SQLiteSchemaBuilder,
}


class DatabaseConnection:
    def __init__(self, env_path: Optional[str] = None, driver: Optional[str] = None):
        """
        env_path: optional explicit path to a .env file. If omitted, DatabaseConnection
                  walks up from core/database/ to the project root and loads .env there.
        driver:   optional override for DB_DRIVER (useful for tests / multiple connections).
        """
        self._load_env(env_path)

        self.driver = (driver or os.getenv("DB_DRIVER", "")).strip().lower()
        if self.driver not in SUPPORTED_DRIVERS:
            raise ValueError(
                f"Unsupported or missing DB_DRIVER '{self.driver}'. "
                f"Expected one of: mysql, postgres, sqlite, mongodb"
            )

        self._connection = None        # raw DB-API connection for mysql/postgres/sqlite
        self._query_builder = None     # BaseQueryBuilder subclass instance, bound to _connection
        self._schema_builder = None    # BaseSchemaBuilder subclass instance, bound to _connection
        self._mongo_client = None      # pymongo.MongoClient for mongodb
        self._mongo_db = None          # pymongo Database handle returned to callers
        self._mongo_schema = None      # MongoSchemaBuilder, bound to _mongo_db

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _project_root(self) -> str:
        # this file lives at project/core/database/DatabaseConnection.py
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "../", ".."))

    def _load_env(self, env_path: Optional[str]) -> None:
        if load_dotenv is None:
            return
        path = env_path or os.path.join(self._project_root(), ".env")

        if os.path.exists(path):
            load_dotenv(path)

    # ------------------------------------------------------------------ #
    # connecting
    # ------------------------------------------------------------------ #
    def connect(self):
        """
        Opens the connection for the configured driver and returns the tool
        you actually query with:
          - mysql/postgres/sqlite -> a QueryBuilder bound to the connection
          - mongodb               -> the pymongo Database (native API)
        Safe to call more than once - reuses the existing connection/builder.
        """
        if self.driver in MONGO_DRIVERS:
            if self._mongo_db is None:
                self._mongo_db = self._connect_mongo()
            return self._mongo_db

        if self._connection is None:
            self._connection = self._open_sql_connection()
        if self._query_builder is None:
            builder_class = QUERY_BUILDER_MAP[self.driver]
            self._query_builder = builder_class(self._connection)
        return self._query_builder

    def schema(self):
        """
        Returns the schema (DDL) tool for the configured driver:
          - mysql/postgres/sqlite -> a SchemaBuilder: create_table/drop_table/empty_table/has_table
          - mongodb               -> a MongoSchemaBuilder: create_collection/drop_collection/
                                      empty_collection/has_collection (table-named aliases included)
        Safe to call more than once - reuses the existing connection/builder.
        """
        if self.driver in MONGO_DRIVERS:
            if self._mongo_db is None:
                self._mongo_db = self._connect_mongo()
            if self._mongo_schema is None:
                self._mongo_schema = MongoSchemaBuilder(self._mongo_db)
            return self._mongo_schema

        if self._connection is None:
            self._connection = self._open_sql_connection()
        if self._schema_builder is None:
            builder_class = SCHEMA_BUILDER_MAP[self.driver]
            self._schema_builder = builder_class(self._connection)
        return self._schema_builder

    def _open_sql_connection(self):
        return {
            "mysql": self._connect_mysql,
            "postgres": self._connect_postgres,
            "postgresql": self._connect_postgres,
            "sqlite": self._connect_sqlite,
        }[self.driver]()

    def _connect_mysql(self):
        import mysql.connector  # pip install mysql-connector-python

        return mysql.connector.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME"),
        )

    def _connect_postgres(self):
        import psycopg2  # pip install psycopg2-binary
        import psycopg2.extras

        return psycopg2.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5432)),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD", ""),
            dbname=os.getenv("DB_NAME"),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def _connect_sqlite(self):
        import sqlite3

        db_name = os.getenv("DB_NAME") or os.getenv("DB_PATH") or "database.sqlite"
        db_path = db_name if os.path.isabs(db_name) else os.path.join(self._project_root(), db_name)
        if os.path.dirname(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_mongo(self):
        from pymongo import MongoClient  # pip install pymongo

        uri = os.getenv("DB_URI")
        if not uri:
            user = os.getenv("DB_USER")
            password = os.getenv("DB_PASSWORD")
            host = os.getenv("DB_HOST", "127.0.0.1")
            port = os.getenv("DB_PORT", "27017")
            auth = f"{user}:{password}@" if user else ""
            uri = f"mongodb://{auth}{host}:{port}/"

        self._mongo_client = MongoClient(uri)
        return self._mongo_client[os.getenv("DB_NAME")]

    # ------------------------------------------------------------------ #
    # access
    # ------------------------------------------------------------------ #
    def get_connection(self):
        """Returns the *raw* handle: DB-API connection for sql drivers, pymongo Database for mongo."""
        if self.driver in MONGO_DRIVERS:
            if self._mongo_db is None:
                self._mongo_db = self._connect_mongo()
            return self._mongo_db
        if self._connection is None:
            self._connection = self._open_sql_connection()
        return self._connection

    def close(self) -> None:
        if self.driver in MONGO_DRIVERS:
            if self._mongo_client:
                self._mongo_client.close()
            self._mongo_client = None
            self._mongo_db = None
            self._mongo_schema = None
        else:
            if self._connection:
                self._connection.close()
            self._connection = None
            self._query_builder = None
            self._schema_builder = None

    # ------------------------------------------------------------------ #
    # context manager support - yields whatever connect() would return
    # ------------------------------------------------------------------ #
    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
