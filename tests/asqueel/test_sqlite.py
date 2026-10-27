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

"""AsqueelDbHandler on SQLite: a real server, a sync route, the session's end."""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

import pytest
from asqueel import AsqueelDb, SqlDatabaseConfig
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn_db.asqueel import AsqueelDbHandler
from tests.conftest import LifespanRunner, asqueel_closed, ask_app

pytestmark = pytest.mark.asqueel


class Notes(RoutedApplication):
    """A sync route that writes, commits and reads back."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.taken: list[Any] = []

    @route()
    def write(self, _request=None) -> list[str]:
        db = _request.db()
        self.taken.append(db)
        db.table("app.note").insert({"id": "n1", "text": "first"})
        db.commit()
        return db.table("app.note").query(columns="$id").fetchPkeys()

    @route()
    def named(self, _request=None) -> dict[str, Any]:
        db = _request.db()
        self.taken.extend([db, db.session("aux")])
        return {}

    @route()
    def refused(self, _request=None) -> dict[str, Any]:
        db = _request.db()
        refusing, other = db.session("refusing"), db.session("other")

        def close() -> None:
            raise RuntimeError("close refused")

        refusing.close = close
        self.taken.extend([db, refusing, other])
        return {}


class NotesDb(AsqueelDb):
    """An ``AsqueelDb`` subclass, to be picked by ``db_class``."""


def notes_server(source: SqlDatabaseConfig, **params: Any) -> AsgiServer:
    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.databases(handler_class=AsqueelDbHandler).database(
                code="default", source=source, **params
            )
            cfg.applications().application(code="notes", mount="", app_class=Notes)

    return AsgiServer(config=Recipe)


async def test_a_sync_route_inserts_commits_and_reads_back(
    sqlite_source: SqlDatabaseConfig,
) -> None:
    server = notes_server(sqlite_source)
    runner = LifespanRunner(server)
    await runner.startup()
    answer = await ask_app(server, "/write")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["n1"]
    [taken] = server.applications["notes"].taken
    assert asqueel_closed(taken)
    await runner.shutdown()
    db = AsqueelDb(sqlite_source)
    with db.session() as session:
        assert session.table("app.note").query(columns="$id").fetchPkeys() == ["n1"]
    db.close()


async def test_the_end_of_the_call_closes_its_named_sessions_too(
    sqlite_source: SqlDatabaseConfig,
) -> None:
    server = notes_server(sqlite_source)
    runner = LifespanRunner(server)
    await runner.startup()
    assert (await ask_app(server, "/named"))["status"] == 200
    main, aux = server.applications["notes"].taken
    assert aux is not main
    assert asqueel_closed(main) and asqueel_closed(aux)
    await runner.shutdown()


async def test_a_refused_close_does_not_leave_the_other_sessions_open(
    sqlite_source: SqlDatabaseConfig, caplog: pytest.LogCaptureFixture
) -> None:
    server = notes_server(sqlite_source)
    runner = LifespanRunner(server)
    await runner.startup()
    assert (await ask_app(server, "/refused"))["status"] == 200
    main, refusing, other = server.applications["notes"].taken
    assert asqueel_closed(main) and asqueel_closed(other)
    assert not asqueel_closed(refusing)
    assert "close refused" in caplog.text
    del refusing.close
    refusing.close()
    await runner.shutdown()


def test_db_class_picks_the_asqueel_db_subclass(sqlite_source: SqlDatabaseConfig) -> None:
    server = notes_server(sqlite_source, db_class=NotesDb)
    assert type(server.databases["default"].databases["default"]) is NotesDb


def test_importing_without_asqueel_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "asqueel", None)
    monkeypatch.delitem(sys.modules, "kajenn_db.asqueel")
    with pytest.raises(ImportError, match=r"pip install kajenn-db\[asqueel\]"):
        importlib.import_module("kajenn_db.asqueel")
