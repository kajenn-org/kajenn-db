# Copyright 2026 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared test helpers: driving a kajenn server, and the backends under test.

``LifespanRunner`` and ``ask_app`` are copied from kajenn's own
``tests/conftest.py`` (not installed with kajenn). ``Backend`` is what the
contract suite is parametrised over: one fixture per backend, named
``<backend>_backend``.

The SQLAlchemy and Django probes run when their extra is installed; their
backend fixtures skip, naming the extra, when it is not. Django is configured
once per test session: an SQLite database in a temporary directory, with
``django.contrib.contenttypes`` as the one installed application.

PostgreSQL connection parameters come from ``GNR_TEST_PG_HOST``,
``GNR_TEST_PG_PORT``, ``GNR_TEST_PG_USER`` and ``GNR_TEST_PG_PASSWORD``, with
asqueel's defaults (the local server on 127.0.0.1:5432, user ``postgres``). A
server that does not answer skips the PostgreSQL tests, naming why.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import psycopg
import pytest
from asqueel import AsqueelDb, SqlDatabaseConfig, TransactionStateError

from kajenn.config import HOME_ENV
from kajenn_db.asqueel import AsqueelDbHandler

try:
    from sqlalchemy import text
    from sqlalchemy.exc import InvalidRequestError

    from kajenn_db.sqlalchemy import SqlAlchemyDbHandler
except ImportError:
    SqlAlchemyDbHandler = None

try:
    import django
    from django.conf import settings

    from kajenn_db.django import DjangoDbHandler
except ImportError:
    DjangoDbHandler = None


@pytest.fixture(autouse=True)
def kajenn_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Autouse: point ``KAJENN_HOME`` at an empty per-test directory."""
    home = tmp_path / "kajenn_home"
    home.mkdir()
    monkeypatch.setenv(HOME_ENV, str(home))
    return home


class LifespanRunner:
    """The lifespan, driven the way a real ASGI runner drives it."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.sent: list[dict[str, Any]] = []
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._living: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        self._living = asyncio.ensure_future(
            self.server({"type": "lifespan"}, self._inbox.get, self._send)
        )
        await self._inbox.put({"type": "lifespan.startup"})
        await self._answered("lifespan.startup.")

    async def shutdown(self) -> None:
        await self._inbox.put({"type": "lifespan.shutdown"})
        await self._living

    async def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def _answered(self, prefix: str) -> None:
        while not any(message["type"].startswith(prefix) for message in self.sent):
            await asyncio.sleep(0)


async def ask_app(app: Any, path: str) -> dict[str, Any]:
    """One ASGI GET, answered: ``status`` and ``body``."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(b"host", b"site.example")],
        "scheme": "http",
        "client": ("10.0.0.1", 5555),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return {"status": start["status"], "body": body}


@dataclass
class Backend:
    """One backend under the contract: its handler, a database's params, a closed check,
    and whether it serves ``async def`` routes."""

    handler_class: type
    params: dict[str, Any]
    closed: Callable[[Any], bool]
    async_routes: bool = True


def note_source(path: Path) -> SqlDatabaseConfig:
    """An SQLite database at ``path`` with the table ``app.note`` (``id``, ``text``)."""
    source = SqlDatabaseConfig()
    root = source.source.db("notes", implementation="sqlite")
    root.connection("main", database=str(path))
    note = (
        root.packages().package("app", sqlschema="main", sqlprefix=False).table("note", pkey="id")
    )
    note.column("id", size=":22")
    note.column("text")
    return source


@pytest.fixture
def sqlite_source(tmp_path: Path) -> SqlDatabaseConfig:
    """``note_source`` on a file in ``tmp_path``, already migrated."""
    source = note_source(tmp_path / "notes.sqlite")
    db = AsqueelDb(source)
    with db.session() as session:
        session.migrate()
        session.commit()
    db.close()
    return source


def asqueel_closed(session: Any) -> bool:
    try:
        session.execute("SELECT 1")
    except TransactionStateError:
        return True
    return False


@pytest.fixture
def asqueel_backend(sqlite_source: SqlDatabaseConfig) -> Backend:
    return Backend(AsqueelDbHandler, {"source": sqlite_source}, asqueel_closed)


def sqlalchemy_closed(session: Any) -> bool:
    try:
        session.execute(text("SELECT 1"))
    except InvalidRequestError:
        return True
    return False


@pytest.fixture
def sqlalchemy_backend(tmp_path: Path) -> Backend:
    if SqlAlchemyDbHandler is None:
        pytest.skip("SQLAlchemy not installed: pip install kajenn-db[sqlalchemy]")
    url = f"sqlite:///{tmp_path / 'notes.sqlite'}"
    return Backend(SqlAlchemyDbHandler, {"url": url}, sqlalchemy_closed)


@pytest.fixture(scope="session")
def django_configured(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Django's settings for the whole test session: ``default`` and ``fresh``, two
    SQLite databases; ``fresh`` is left to the migration test."""
    if DjangoDbHandler is None:
        pytest.skip("Django not installed: pip install kajenn-db[django]")
    folder = tmp_path_factory.mktemp("django")
    settings.configure(
        DATABASES={
            alias: {"ENGINE": "django.db.backends.sqlite3", "NAME": str(folder / f"{alias}.sqlite")}
            for alias in ("default", "fresh")
        },
        INSTALLED_APPS=["django.contrib.contenttypes"],
        USE_TZ=True,
    )
    django.setup()


def django_closed(connection: Any) -> bool:
    return connection.connection is None


@pytest.fixture
def django_backend(django_configured: None) -> Backend:
    return Backend(DjangoDbHandler, {"alias": "default"}, django_closed, async_routes=False)


def pg_params() -> dict[str, str]:
    """psycopg connection kwargs for the test PostgreSQL server."""
    params = {
        "host": os.environ.get("GNR_TEST_PG_HOST", "127.0.0.1"),
        "port": os.environ.get("GNR_TEST_PG_PORT", "5432"),
        "user": os.environ.get("GNR_TEST_PG_USER", "postgres"),
    }
    password = os.environ.get("GNR_TEST_PG_PASSWORD")
    if password:
        params["password"] = password
    return params


def _drop_pg_database(params: dict[str, str], dbname: str) -> None:
    """Terminate leftover backends and drop the test database."""
    with psycopg.connect(dbname="postgres", autocommit=True, **params) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """One PostgreSQL database for the whole test session, dropped at its end."""
    params = pg_params()
    try:
        psycopg.connect(dbname="postgres", connect_timeout=3, **params).close()
    except psycopg.OperationalError as error:
        pytest.skip(f"PostgreSQL not reachable at {params['host']}:{params['port']}: {error}")
    dbname = f"test_kajenn_db_{uuid4().hex[:16]}"
    with psycopg.connect(dbname="postgres", autocommit=True, **params) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    yield psycopg.conninfo.make_conninfo(dbname=dbname, **params)
    _drop_pg_database(params, dbname)


@pytest.fixture
def pg_schema(pg_dsn: str) -> Iterator[str]:
    """A schema name of this test's own, dropped after the test."""
    schema = f"t_{uuid4().hex[:16]}"
    yield schema
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def pg_note_source(dsn: str, schema: str) -> SqlDatabaseConfig:
    """``note_source`` on PostgreSQL: the table ``app.note`` in ``schema``."""
    source = SqlDatabaseConfig()
    root = source.source.db("notes", implementation="postgres", conninfo=dsn)
    note = (
        root.packages().package("app", sqlschema=schema, sqlprefix=False).table("note", pkey="id")
    )
    note.column("id", size=":22")
    note.column("text")
    return source


@pytest.fixture
def pg_source(pg_dsn: str, pg_schema: str) -> SqlDatabaseConfig:
    """``pg_note_source`` in this test's schema, already migrated."""
    source = pg_note_source(pg_dsn, pg_schema)
    db = AsqueelDb(source)
    db.migrate()
    db.close()
    return source
