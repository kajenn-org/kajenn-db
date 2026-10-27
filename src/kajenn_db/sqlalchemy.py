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

"""The SQLAlchemy probe: kajenn databases served by an SQLAlchemy ``Engine``.

A probe, incomplete by declaration: it runs kajenn's handler contract on
SQLAlchemy and offers no migration. One shared ``Engine`` per database; a call
takes its own ``Session``, closed at the end of the call.

An ``async def`` route works on the core's ``AsyncDbView`` over the same
``Session``: its ``terminals`` are awaitable and run on the server pool, so
``db()`` and ``async_db()`` of one call share one transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kajenn import DbHandlerBase

try:
    from sqlalchemy import Engine, create_engine
    from sqlalchemy.orm import Session
except ImportError as error:
    raise ImportError(
        "kajenn_db.sqlalchemy needs SQLAlchemy: pip install kajenn-db[sqlalchemy]"
    ) from error

if TYPE_CHECKING:
    from kajenn.request_registry import RegisteredRequest

__all__ = ["SqlAlchemyDbHandler"]


class SqlAlchemyDbHandler(DbHandlerBase):
    """SQLAlchemy engines by name; one ``Session`` per name per call.

    A database's params: ``url``, the SQLAlchemy database URL.
    """

    terminals = (
        "execute",
        "scalars",
        "scalar",
        "get",
        "get_one",
        "merge",
        "flush",
        "refresh",
        "commit",
        "rollback",
    )

    def __init__(self, server: Any) -> None:
        super().__init__(server)
        self.engines: dict[str, Engine] = {}

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        self.engines[name] = create_engine(params["url"])

    async def close(self) -> None:
        for engine in self.engines.values():
            await self.server.run_sync(engine.dispose)

    def take(self, name: str, call: RegisteredRequest) -> Session:
        # close_resets_only=False: a released Session refuses any further use.
        return Session(self.engines[name], close_resets_only=False)

    def release(self, call: RegisteredRequest) -> None:
        for name, session in call.databases.items():
            if self.server.databases[name] is self:
                session.close()
