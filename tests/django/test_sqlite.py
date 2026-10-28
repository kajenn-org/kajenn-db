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

"""DjangoDbHandler on SQLite: the ORM in a sync route, the refusal of async routes,
migrations."""

from __future__ import annotations

import importlib
import json
import logging
import sys
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from tests.conftest import Backend, LifespanRunner, ask_app, django_closed

django = pytest.importorskip("django")

pytestmark = pytest.mark.django


class Notes(RoutedApplication):
    """A sync route on Django's ORM, and an async route that asks for the async view."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.taken: list[Any] = []
        self.connected: list[bool] = []

    @route()
    def write(self, _request=None) -> list[str]:
        db = _request.db()
        self.taken.append(db)
        content_type = django.apps.apps.get_model("contenttypes", "ContentType")
        content_type.objects.get_or_create(app_label="kajenn", model="note")
        self.connected.append(db.connection is not None)
        return list(content_type.objects.filter(app_label="kajenn").values_list("model", flat=True))

    @route()
    async def async_write(self, _request=None) -> dict[str, Any]:
        self.taken.append(_request.db())
        try:
            _request.async_db()
        except NotImplementedError as error:
            return {"refused": str(error)}
        return {}


def notes_server(backend: Backend, **params: Any) -> AsgiServer:
    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.databases(handler_class=backend.handler_class).database(
                code="default", **{**backend.params, **params}
            )
            cfg.applications().application(code="notes", mount="", app_class=Notes)

    return AsgiServer(config=Recipe)


async def test_a_sync_route_uses_the_orm_on_the_taken_connection(
    django_backend: Backend,
) -> None:
    server = notes_server(django_backend)
    await server.run_sync(server.databases["default"].migrate, "default")
    runner = LifespanRunner(server)
    await runner.startup()
    answer = await ask_app(server, "/write")
    assert answer["status"] == 200
    assert json.loads(answer["body"]) == ["note"]
    notes = server.applications["notes"]
    assert notes.connected == [True]
    [taken] = notes.taken
    assert django_closed(taken)
    await runner.shutdown()


async def test_an_async_route_is_refused_and_its_take_released(
    django_backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    server = notes_server(django_backend)
    with caplog.at_level(logging.ERROR):
        answer = await ask_app(server, "/async_write")
    assert answer["status"] == 200
    assert "Django's async ORM" in json.loads(answer["body"])["refused"]
    [taken] = server.applications["notes"].taken
    assert django_closed(taken)
    assert not [record for record in caplog.records if "release" in record.getMessage()]


def test_migrate_from_scratch_then_an_empty_plan(django_backend: Backend) -> None:
    handler = notes_server(django_backend, alias="fresh").databases["default"]
    assert handler.capabilities == {"migration_plan", "migrate"}
    assert "contenttypes.0001_initial" in handler.migration_plan("default")
    handler.migrate("default")
    assert handler.migration_plan("default") == []
    assert django.db.connections["fresh"].connection is None


def test_alias_defaults_to_the_database_name(django_backend: Backend) -> None:
    handler = notes_server(Backend(django_backend.handler_class, {}, django_closed)).databases[
        "default"
    ]
    assert handler.aliases == {"default": "default"}


def test_importing_without_django_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "django.db", None)
    monkeypatch.delitem(sys.modules, "kajenn_db.django")
    with pytest.raises(ImportError, match=r"pip install kajenn-db\[django\]"):
        importlib.import_module("kajenn_db.django")
