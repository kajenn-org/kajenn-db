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

"""The Django probe: kajenn databases served by Django's ``connections``.

A probe, incomplete by declaration: it runs kajenn's handler contract on
Django, for sync routes only. Django is configured by the process (settings
and ``django.setup()``), not by the handler. A call takes
``connections[alias]``, the connection Django's ORM uses on the calling thread,
and the end of the call closes it on that same thread.

Django binds a connection to the thread that opened it, and the core takes a
database of an ``async def`` route on the event-loop thread: ``async_view``
raises, naming Django's async ORM. Sync work outside a route (a task, a kbus
call, a WSX message) is released by the core with ``server.run_sync``, possibly
on another pool thread, where Django refuses the close: not supported either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kajenn import DbHandlerBase

try:
    from django.core.management import call_command
    from django.db import connections
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.migrations.executor import MigrationExecutor
except ImportError as error:
    raise ImportError("kajenn_db.django needs Django: pip install kajenn-db[django]") from error

if TYPE_CHECKING:
    from kajenn.request_registry import RegisteredRequest

__all__ = ["DjangoDbHandler"]


class DjangoDbHandler(DbHandlerBase):
    """Django database aliases by name; ``connections[alias]`` per name per call.

    A database's params: ``alias``, a key of Django's ``DATABASES`` (the
    database's own name by default).
    """

    def __init__(self, server: Any) -> None:
        super().__init__(server)
        self.aliases: dict[str, str] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"migration_plan", "migrate"})

    def migration_plan(self, name: str) -> list[str]:
        """The migrations not yet applied to database ``name``, as ``app_label.name``.

        Sync, on the calling thread's connection, closed at the end.
        """
        connection = connections[self.aliases[name]]
        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        finally:
            connection.close()
        return [f"{migration.app_label}.{migration.name}" for migration, _ in plan]

    def migrate(self, name: str) -> None:
        """Apply every pending migration to database ``name``; sync, as ``migration_plan``."""
        alias = self.aliases[name]
        try:
            call_command("migrate", database=alias, interactive=False, verbosity=0)
        finally:
            connections[alias].close()

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        self.aliases[name] = params.get("alias", name)

    def take(self, name: str, call: RegisteredRequest) -> BaseDatabaseWrapper:
        return connections[self.aliases[name]]

    def release(self, call: RegisteredRequest) -> None:
        # A wrapper taken on the event loop never connected, and closing it
        # from this pool thread would raise: only a connected one is closed.
        for name, connection in call.databases.items():
            if self.server.databases[name] is self and connection.connection is not None:
                connection.close()

    def async_view(self, obj: BaseDatabaseWrapper) -> Any:
        raise NotImplementedError(
            "DjangoDbHandler serves sync routes only: an async def route uses Django's"
            " async ORM (Model.objects.aget(), acreate(), ...)"
        )
