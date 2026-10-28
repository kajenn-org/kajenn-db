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

"""kajenn's database handler contract, run on every installed backend.

A backend is a ``<name>_backend`` fixture (``tests/conftest.py``) returning a
``Backend``; ``BACKENDS`` names the ones the suite runs on.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from genro_routes import route

from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from tests.conftest import Backend, LifespanRunner, ask_app

BACKENDS = ["asqueel", "sqlalchemy", "django"]


class Probe(RoutedApplication):
    """Sync and async routes that record what they took."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen: list[Any] = []
        self.meeting = threading.Barrier(2)
        self.arrived = 0
        self.all_arrived = asyncio.Event()

    @route()
    def twice(self, _request=None) -> dict[str, Any]:
        self.seen.append((_request.db(), _request.db()))
        return {}

    @route()
    def meet(self, _request=None) -> dict[str, Any]:
        self.seen.append(_request.db())
        self.meeting.wait(timeout=5)
        return {}

    @route()
    async def async_twice(self, _request=None) -> dict[str, Any]:
        assert _request.async_db() is not None
        self.seen.append((_request.db(), _request.db()))
        return {}

    @route()
    async def async_meet(self, _request=None) -> dict[str, Any]:
        self.seen.append(_request.db())
        self.arrived += 1
        if self.arrived == 2:
            self.all_arrived.set()
        await asyncio.wait_for(self.all_arrived.wait(), timeout=5)
        return {}


def serve(backend: Backend) -> AsgiServer:
    class Recipe(AsgiConfigBuilder):
        default_config = False

        def main(self, root: Any) -> None:
            cfg = root.configuration()
            cfg.databases(handler_class=backend.handler_class).database(
                code="default", **backend.params
            )
            cfg.applications().application(code="probe", mount="", app_class=Probe)

    return AsgiServer(config=Recipe)


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> Backend:
    return request.getfixturevalue(f"{request.param}_backend")


@pytest.fixture
def async_backend(backend: Backend) -> Backend:
    if not backend.async_routes:
        pytest.skip(f"{backend.handler_class.__name__} serves sync routes only")
    return backend


async def test_one_take_per_name_per_call(backend: Backend) -> None:
    server = serve(backend)
    seen = server.applications["probe"].seen
    assert (await ask_app(server, "/twice"))["status"] == 200
    [(first, again)] = seen
    assert first is again
    assert backend.closed(first)
    assert (await ask_app(server, "/twice"))["status"] == 200
    (second, again) = seen[1]
    assert second is again


async def test_release_at_the_end_of_the_call(backend: Backend) -> None:
    server = serve(backend)
    runner = LifespanRunner(server)
    await runner.startup()
    assert (await ask_app(server, "/twice"))["status"] == 200
    [(taken, _)] = server.applications["probe"].seen
    assert backend.closed(taken)
    await runner.shutdown()


async def test_two_concurrent_calls_are_isolated(backend: Backend) -> None:
    server = serve(backend)
    answers = await asyncio.gather(ask_app(server, "/meet"), ask_app(server, "/meet"))
    assert [answer["status"] for answer in answers] == [200, 200]
    one, other = server.applications["probe"].seen
    assert one is not other
    assert backend.closed(one) and backend.closed(other)


async def test_one_take_per_name_per_async_call(async_backend: Backend) -> None:
    backend = async_backend
    server = serve(backend)
    seen = server.applications["probe"].seen
    assert (await ask_app(server, "/async_twice"))["status"] == 200
    [(first, again)] = seen
    assert first is again
    assert backend.closed(first)
    assert (await ask_app(server, "/async_twice"))["status"] == 200
    (second, again) = seen[1]
    assert second is again


async def test_release_at_the_end_of_the_async_call(async_backend: Backend) -> None:
    backend = async_backend
    server = serve(backend)
    runner = LifespanRunner(server)
    await runner.startup()
    assert (await ask_app(server, "/async_twice"))["status"] == 200
    [(taken, _)] = server.applications["probe"].seen
    assert backend.closed(taken)
    await runner.shutdown()


async def test_two_concurrent_async_calls_are_isolated(async_backend: Backend) -> None:
    backend = async_backend
    server = serve(backend)
    answers = await asyncio.gather(ask_app(server, "/async_meet"), ask_app(server, "/async_meet"))
    assert [answer["status"] for answer in answers] == [200, 200]
    one, other = server.applications["probe"].seen
    assert one is not other
    assert backend.closed(one) and backend.closed(other)
