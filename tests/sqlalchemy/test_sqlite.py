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

"""SqlAlchemyDbHandler on SQLite: sync and async routes, one Session per call."""

from __future__ import annotations

import importlib
import json
import sys
import threading
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from tests.conftest import Backend, LifespanRunner, ask_app, sqlalchemy_closed

sqlalchemy = pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.sqlalchemy


class Notes(RoutedApplication):
    """A sync and an async route that write, commit and read back."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.taken: list[Any] = []
        self.threads: list[int] = []

    @route()
    def write(self, _request=None) -> list[str]:
        db = _request.db()
        self.taken.append(db)
        db.execute(sqlalchemy.text("INSERT INTO note (id, text) VALUES ('n1', 'first')"))
        db.commit()
        return list(db.scalars(sqlalchemy.text("SELECT id FROM note")))

    @route()
    async def async_write(self, _request=None) -> list[str]:
        self.taken.append(_request.db())
        db = _request.async_db()
        insert = "INSERT INTO note (id, text) VALUES (:id, :text)"
        await db.execute(sqlalchemy.text(insert), {"id": "a1", "text": "one"})
        await db.execute(sqlalchemy.text(insert), {"id": "a2", "text": "two"})
        await db.commit()
        self.threads.append(threading.get_ident())
        return list(await db.scalars(sqlalchemy.text("SELECT id FROM note ORDER BY id")))


@pytest.fixture
def notes_server(sqlalchemy_backend: Backend) -> AsgiServer:
    engine = sqlalchemy.create_engine(sqlalchemy_backend.params["url"])
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("CREATE TABLE note (id TEXT PRIMARY KEY, text TEXT)"))
    engine.dispose()

    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.databases(handler_class=sqlalchemy_backend.handler_class).database(
                code="default", **sqlalchemy_backend.params
            )
            cfg.applications().application(code="notes", mount="", app_class=Notes)

    return AsgiServer(config=Recipe)


async def test_a_sync_route_inserts_commits_and_reads_back(notes_server: AsgiServer) -> None:
    runner = LifespanRunner(notes_server)
    await runner.startup()
    answer = await ask_app(notes_server, "/write")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["n1"]
    [taken] = notes_server.applications["notes"].taken
    assert sqlalchemy_closed(taken)
    assert notes_server.databases["default"].engines["default"].pool.checkedout() == 0
    await runner.shutdown()


async def test_an_async_route_writes_through_awaited_terminals(notes_server: AsgiServer) -> None:
    runner = LifespanRunner(notes_server)
    await runner.startup()
    answer = await ask_app(notes_server, "/async_write")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["a1", "a2"]
    notes = notes_server.applications["notes"]
    [taken] = notes.taken
    assert sqlalchemy_closed(taken)
    assert notes.threads == [threading.get_ident()]
    assert notes_server.databases["default"].engines["default"].pool.checkedout() == 0
    await runner.shutdown()


def test_importing_without_sqlalchemy_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sqlalchemy", None)
    monkeypatch.delitem(sys.modules, "kajenn_db.sqlalchemy")
    with pytest.raises(ImportError, match=r"pip install kajenn-db\[sqlalchemy\]"):
        importlib.import_module("kajenn_db.sqlalchemy")
