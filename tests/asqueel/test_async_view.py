# Copyright 2025 Softwell S.r.l.
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

"""The async view of asqueel on SQLite: async routes, the pool, the event loop."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from typing import Any

import pytest
from asqueel import (
    AsqueelDb,
    SessionDb,
    SqlCursor,
    SqlDatabaseConfig,
    SqlQuery,
    SqlTable,
    UnsupportedFeatureError,
)
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn_db.asqueel import (
    AsqueelDbHandler,
    AsyncCursorView,
    AsyncQueryView,
    AsyncSessionView,
    AsyncTableView,
)
from tests.conftest import LifespanRunner, asqueel_closed, ask_app

pytestmark = pytest.mark.asqueel

# The public methods of asqueel 0.9.0 that do no I/O of their own: they pass
# through the view as asqueel has them. table(), query() and session() pass
# through too, wrapped in a view.
NOT_IO = {
    SessionDb: {
        "clearCurrentEnv",
        "deferAfterCommit",
        "deferToCommit",
        "deferredRaise",
        "environment_snapshot",
        "grammar",
        "onDbCommitted",
        "package",
        "schema",
        "session",
        "setLocale",
        "table",
        "tempEnv",
        "updateEnv",
        "usingMainConnection",
    },
    SqlTable: {
        "adaptRecord",
        "checkPkey",
        "column",
        "exception",
        "newPkeyValue",
        "pkeyValue",
        "protect_delete",
        "protect_update",
        "protect_validate",
        "query",
        "sql_formula___invalid_reasons",
        "sql_formula___protecting_reasons",
        "trigger_assignCounters",
        "trigger_hierarchical_after",
        "trigger_hierarchical_before",
        "trigger_insertLinkedHierarchicalRoot",
        "trigger_onDeleted",
        "trigger_onDeleting",
        "trigger_onInserted",
        "trigger_onInserting",
        "trigger_onUpdated",
        "trigger_onUpdating",
        "trigger_releaseCounters",
        "trigger_setCurrentUser",
        "trigger_setRelidx",
        "trigger_setRowCounter",
        "trigger_setTSNow",
        "trigger_updateLinkedHierarchicalRoot",
    },
    SqlQuery: {"handleBagColumns", "test"},
    SqlCursor: set(),
}

VIEWS = {
    SessionDb: AsyncSessionView,
    SqlTable: AsyncTableView,
    SqlQuery: AsyncQueryView,
    SqlCursor: AsyncCursorView,
}


class AsyncNotes(RoutedApplication):
    """Async routes on the view of the call's session."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.taken: list[Any] = []
        self.fetch_threads: list[int] = []

    @route()
    async def write(self, _request=None) -> list[str]:
        db = _request.async_db()
        self.taken.append(_request.db())
        notes = db.table("app.note")
        await notes.insert({"id": "n1", "text": "first"})
        await notes.insert({"id": "n2", "text": "second"})
        await db.commit()
        return await notes.query(columns="$id", order_by="$id").fetchPkeys()

    @route()
    async def fetch(self, _request=None) -> int:
        db = _request.async_db()
        self.fetch_threads.append(threading.get_ident())
        return len(await db.table("app.note").query().fetch())

    @route()
    async def read_cursor(self, _request=None) -> list[str]:
        db = _request.async_db()
        cursor = await db.execute("SELECT 'one' UNION ALL SELECT 'two'")
        rows = await cursor.fetchall()
        await cursor.close()
        return [row[0] for row in rows]

    @route()
    async def slow(self, _request=None) -> int:
        db = _request.async_db()
        cursor = await db.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000000) "
            "SELECT count(*) FROM n"
        )
        [(count,)] = await cursor.fetchall()
        await cursor.close()
        return count


def async_server(source: SqlDatabaseConfig) -> AsgiServer:
    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.databases(handler_class=AsqueelDbHandler).database(code="default", source=source)
            cfg.applications().application(code="notes", mount="", app_class=AsyncNotes)

    return AsgiServer(config=Recipe)


@pytest.mark.parametrize("cls", list(VIEWS), ids=lambda cls: cls.__name__)
def test_every_public_method_is_a_terminal_blocked_or_not_io(cls: type) -> None:
    view = VIEWS[cls]
    public = {
        name
        for name, value in inspect.getmembers(cls)
        if not name.startswith("_") and callable(value)
    }
    classified = set(view.terminals) | set(view.blocked) | NOT_IO[cls]
    assert public == classified
    assert not set(view.terminals) & set(view.blocked)


async def test_an_async_route_inserts_with_two_terminals_and_commits(
    sqlite_source: SqlDatabaseConfig,
) -> None:
    server = async_server(sqlite_source)
    runner = LifespanRunner(server)
    await runner.startup()
    answer = await ask_app(server, "/write")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["n1", "n2"]
    [taken] = server.applications["notes"].taken
    assert asqueel_closed(taken)
    await runner.shutdown()
    db = AsqueelDb(sqlite_source)
    with db.session() as session:
        assert session.table("app.note").query(columns="$id").count() == 2
    db.close()


async def test_a_query_fetch_runs_on_a_pool_thread(
    sqlite_source: SqlDatabaseConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched_on: list[int] = []
    original = SqlQuery.fetch

    def recording_fetch(self: SqlQuery, *args: Any, **kwargs: Any) -> Any:
        fetched_on.append(threading.get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SqlQuery, "fetch", recording_fetch)
    server = async_server(sqlite_source)
    answer = await ask_app(server, "/fetch")
    assert answer["status"] == 200
    [route_thread] = server.applications["notes"].fetch_threads
    [fetch_thread] = fetched_on
    assert fetch_thread != route_thread


async def test_execute_gives_a_cursor_view_read_with_awaits(
    sqlite_source: SqlDatabaseConfig,
) -> None:
    server = async_server(sqlite_source)
    answer = await ask_app(server, "/read_cursor")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["one", "two"]


def test_views_wrap_tables_queries_and_named_sessions(sqlite_source: SqlDatabaseConfig) -> None:
    server = async_server(sqlite_source)
    handler = server.databases["default"]
    with handler.databases["default"].session() as session:
        view = handler.async_view(session)
        assert isinstance(view, AsyncSessionView)
        assert isinstance(view.table("app.note"), AsyncTableView)
        assert isinstance(view.table("app.note").query(), AsyncQueryView)
        assert isinstance(view.session("aux"), AsyncSessionView)
        assert view.environment_snapshot() == session.environment_snapshot()


@pytest.mark.parametrize(
    ("level", "name", "hint"),
    [
        ("session", "insert", "table(name).insert()"),
        ("session", "close", "the end of the call closes the session"),
        ("session", "migrate", "the handler's migrate(name)"),
        ("query", "iterfetch", "cursor()"),
        ("query", "serverfetch", "cursor()"),
        ("table", "record", "fetchRecord()"),
    ],
)
def test_a_blocked_method_raises_naming_what_to_use(
    sqlite_source: SqlDatabaseConfig, level: str, name: str, hint: str
) -> None:
    server = async_server(sqlite_source)
    handler = server.databases["default"]
    with handler.databases["default"].session() as session:
        view = handler.async_view(session)
        target = {
            "session": view,
            "table": view.table("app.note"),
            "query": view.table("app.note").query(),
        }[level]
        with pytest.raises(UnsupportedFeatureError) as raised:
            getattr(target, name)
        assert f"{name}()" in str(raised.value)
        assert hint in str(raised.value)


async def test_a_terminal_does_not_block_the_event_loop(sqlite_source: SqlDatabaseConfig) -> None:
    server = async_server(sqlite_source)
    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    ticking = asyncio.ensure_future(ticker())
    started = time.perf_counter()
    answer = await ask_app(server, "/slow")
    elapsed = time.perf_counter() - started
    done.set()
    await ticking
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == 3000000
    assert elapsed > 0.2
    assert max(gaps) < 0.1
