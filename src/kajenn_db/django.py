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

"""The Django probe: kajenn databases served by Django's ``connections``.

A probe, incomplete by declaration: it runs kajenn's handler contract on
Django, for sync work only. Django is configured by the process (settings and
``django.setup()``), not by the handler.

A call takes a connection of its own, installed as ``connections[alias]`` on
the calling thread: Django's ORM in the call works on it. The connection is
shared between threads, so the end of the call closes it on whatever pool
thread the core releases it from; the next call on the same thread installs a
new one.

The core takes a database of an ``async def`` route on the event-loop thread,
where Django's ORM does not run: ``async_view`` raises, naming Django's async
ORM.
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
    """Django database aliases by name; one connection per name per call.

    A database's params: ``alias``, a key of Django's ``DATABASES`` (the
    database's own name by default).
    """

    def __init__(self, server: Any) -> None:
        super().__init__(server)
        self.aliases: dict[str, str] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        """The optional operations offered, ``migration_plan`` and ``migrate``."""
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
        # connections[alias] is one wrapper per thread, shared by every call
        # the thread serves: the call gets a wrapper of its own instead, and
        # the ORM on this thread works on it until the next call replaces it.
        alias = self.aliases[name]
        connection = connections.create_connection(alias)
        connection.inc_thread_sharing()
        connections[alias] = connection
        return connection

    def release(self, call: RegisteredRequest) -> None:
        # Every connection is closed even when one refuses; the first error is
        # raised at the end.
        error = None
        for name, connection in call.databases.items():
            if self.server.databases[name] is not self:
                continue
            try:
                connection.close()
            except BaseException as failure:
                if error is None:
                    error = failure
            finally:
                connection.dec_thread_sharing()
        if error is not None:
            raise error

    def async_view(self, obj: BaseDatabaseWrapper) -> Any:
        raise NotImplementedError(
            "DjangoDbHandler serves sync routes only: an async def route uses Django's"
            " async ORM (Model.objects.aget(), acreate(), ...)"
        )
