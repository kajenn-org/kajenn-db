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

"""AsqueelDbHandler on PostgreSQL: transactions, cursors, the warning, migration."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import pytest
from asqueel import AsqueelDb, SqlDatabaseConfig
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn_db.asqueel import AsqueelDbHandler
from tests.conftest import LifespanRunner, ask_app, pg_note_source

pytestmark = [pytest.mark.asqueel, pytest.mark.postgresql]

WAIT = 10


class Notes(RoutedApplication):
    """Routes that fail, forget to commit, hold a transaction open, read with a cursor."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.inserted = threading.Event()
        self.checked = threading.Event()

    @route()
    def fail(self, _request=None) -> None:
        _request.db().table("app.note").insert({"id": "n1", "text": "lost"})
        raise RuntimeError("the route fails after its insert")

    @route()
    def forget(self, _request=None) -> dict[str, Any]:
        _request.db().table("app.note").insert({"id": "n1", "text": "never committed"})
        return {}

    @route()
    def hold(self, _request=None) -> dict[str, Any]:
        db = _request.db()
        db.table("app.note").insert({"id": "held", "text": "held"})
        self.inserted.set()
        assert self.checked.wait(WAIT)
        db.commit()
        return {}

    @route()
    def peek(self, _request=None) -> list[str]:
        assert self.inserted.wait(WAIT)
        ids = note_ids(_request.db())
        self.checked.set()
        return ids

    @route()
    def ids(self, _request=None) -> list[str]:
        return note_ids(_request.db())

    @route()
    async def read_cursor(self, _request=None) -> list[str]:
        notes = _request.async_db().table("app.note")
        await notes.insert({"id": "c1", "text": "one"})
        await notes.insert({"id": "c2", "text": "two"})
        cursor = await notes.query(columns="$id", order_by="$id").cursor()
        rows = await cursor.fetchall()
        await cursor.close()
        return [row["id"] for row in rows]


class TwoDatabases(RoutedApplication):
    """A sync and an async route writing to a PostgreSQL and an SQLite database."""

    @route()
    def sync_write(self, _request=None) -> dict[str, list[str]]:
        written = {}
        for name in ("pg", "lite"):
            db = _request.db(name)
            db.table("app.note").insert({"id": "sync", "text": name})
            db.commit()
            written[name] = note_ids(db)
        return written

    @route()
    async def async_write(self, _request=None) -> dict[str, list[str]]:
        written = {}
        for name in ("pg", "lite"):
            db = _request.async_db(name)
            notes = db.table("app.note")
            await notes.insert({"id": "async", "text": name})
            await db.commit()
            written[name] = await notes.query(columns="$id", order_by="$id").fetchPkeys()
        return written


def note_ids(db: Any) -> list[str]:
    return db.table("app.note").query(columns="$id", order_by="$id").fetchPkeys()


def server_of(app_class: type, **sources: SqlDatabaseConfig) -> AsgiServer:
    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            databases = cfg.databases(handler_class=AsqueelDbHandler)
            for code, source in sources.items():
                databases.database(code=code, source=source)
            cfg.applications().application(code="notes", mount="", app_class=app_class)

    return AsgiServer(config=Recipe)


def stored_ids(source: SqlDatabaseConfig) -> list[str]:
    db = AsqueelDb(source)
    with db.session() as session:
        ids = note_ids(session)
    db.close()
    return ids


async def test_a_route_that_fails_leaves_no_rows(pg_source: SqlDatabaseConfig) -> None:
    server = server_of(Notes, default=pg_source)
    runner = LifespanRunner(server)
    await runner.startup()
    assert (await ask_app(server, "/fail"))["status"] == 500
    await runner.shutdown()
    assert stored_ids(pg_source) == []


async def test_a_route_that_does_not_commit_logs_asqueel_warning(
    pg_source: SqlDatabaseConfig, caplog: pytest.LogCaptureFixture
) -> None:
    server = server_of(Notes, default=pg_source)
    runner = LifespanRunner(server)
    await runner.startup()
    with caplog.at_level(logging.WARNING, logger="asqueel.application"):
        assert (await ask_app(server, "/forget"))["status"] == 200
    await runner.shutdown()
    assert "closed with uncommitted work, rolled back" in caplog.text
    assert stored_ids(pg_source) == []


async def test_two_concurrent_requests_have_independent_transactions(
    pg_source: SqlDatabaseConfig,
) -> None:
    server = server_of(Notes, default=pg_source)
    runner = LifespanRunner(server)
    await runner.startup()
    held, peeked = await asyncio.gather(ask_app(server, "/hold"), ask_app(server, "/peek"))
    assert held["status"] == 200
    assert json.loads(peeked["body"]) == []
    assert json.loads((await ask_app(server, "/ids"))["body"]) == ["held"]
    await runner.shutdown()


async def test_an_async_route_reads_a_cursor(pg_source: SqlDatabaseConfig) -> None:
    server = server_of(Notes, default=pg_source)
    runner = LifespanRunner(server)
    await runner.startup()
    answer = await ask_app(server, "/read_cursor")
    await runner.shutdown()
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["c1", "c2"]


async def test_migrate_from_scratch_then_the_plan_is_empty(pg_dsn: str, pg_schema: str) -> None:
    server = server_of(Notes, default=pg_note_source(pg_dsn, pg_schema))
    handler = server.databases["default"]
    assert handler.capabilities == {"migration_plan", "migrate"}
    runner = LifespanRunner(server)
    await runner.startup()
    assert not (await server.run_sync(handler.migration_plan, "default"))["empty"]
    await server.run_sync(handler.migrate, "default")
    assert (await server.run_sync(handler.migration_plan, "default"))["empty"]
    await runner.shutdown()


async def test_a_postgresql_and_an_sqlite_database_serve_sync_and_async_routes(
    pg_source: SqlDatabaseConfig, sqlite_source: SqlDatabaseConfig
) -> None:
    server = server_of(TwoDatabases, pg=pg_source, lite=sqlite_source)
    runner = LifespanRunner(server)
    await runner.startup()
    synced = await ask_app(server, "/sync_write")
    awaited = await ask_app(server, "/async_write")
    await runner.shutdown()
    assert json.loads(synced["body"]) == {"pg": ["sync"], "lite": ["sync"]}
    assert json.loads(awaited["body"]) == {"pg": ["async", "sync"], "lite": ["async", "sync"]}
    assert stored_ids(pg_source) == stored_ids(sqlite_source) == ["async", "sync"]
