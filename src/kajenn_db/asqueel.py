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

"""The asqueel handler: kajenn databases served by ``AsqueelDb``.

One ``AsqueelDbHandler`` serves every database declared with it. A database is
an ``AsqueelDb`` built from its ``source`` (and ``parents``); a call takes its
main ``SessionDb`` with ``db.session()``, and the end of the call closes the
call's named sessions, then the main one.

An ``async def`` route works on the async view of the main ``SessionDb``
(``request.async_db()``): three levels — session, table, query — plus the
cursor, each with its own ``terminals`` (awaitable, run on the server pool) and
its own ``blocked`` methods (refused, naming what to use instead). Every other
attribute passes through as asqueel has it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kajenn import DbHandlerBase

try:
    from asqueel import AsqueelDb, SessionDb, UnsupportedFeatureError
except ImportError as error:
    raise ImportError("kajenn_db.asqueel needs asqueel: pip install kajenn-db[asqueel]") from error

if TYPE_CHECKING:
    from kajenn.request_registry import RegisteredRequest

__all__ = [
    "AsqueelDbHandler",
    "AsyncAsqueelView",
    "AsyncCursorView",
    "AsyncQueryView",
    "AsyncSessionView",
    "AsyncTableView",
]


class AsqueelDbHandler(DbHandlerBase):
    """``AsqueelDb`` databases by name; one main ``SessionDb`` per name per call.

    A database's params: ``source`` (anything ``AsqueelDb`` accepts),
    ``parents`` and ``db_class`` (an ``AsqueelDb`` subclass, ``AsqueelDb`` by
    default).
    """

    def __init__(self, server: Any) -> None:
        super().__init__(server)
        self.databases: dict[str, AsqueelDb] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset({"migration_plan", "migrate"})

    def migration_plan(self, name: str) -> dict[str, Any]:
        """The DDL that would bring database ``name`` to its model; changes nothing.

        Sync, on a connection of its own: an async caller uses ``server.run_sync``.
        """
        return self.databases[name].migration_plan()

    def migrate(self, name: str) -> dict[str, Any]:
        """Bring database ``name`` to its model; sync, on a connection of its own."""
        return self.databases[name].migrate()

    def add_database(self, name: str, params: dict[str, Any]) -> None:
        db_class = params.get("db_class", AsqueelDb)
        self.databases[name] = db_class(params.get("source"), parents=params.get("parents"))

    async def close(self) -> None:
        for db in self.databases.values():
            await self.server.run_sync(db.close)

    def take(self, name: str, call: RegisteredRequest) -> SessionDb:
        return self.databases[name].session()

    def release(self, call: RegisteredRequest) -> None:
        # asqueel 0.9.0 has no public "close the whole call": the call's
        # sessions are reached through the main one, as asqueel.hosting does.
        # Every session is closed even when one refuses; the first error is
        # raised at the end.
        error = None
        for name, main in call.databases.items():
            if self.server.databases[name] is not self:
                continue
            named = [s for s in main._call_sessions.values() if s is not main]
            for session in [*named, main]:
                try:
                    session.close()
                except BaseException as failure:
                    if error is None:
                        error = failure
        if error is not None:
            raise error

    def async_view(self, obj: SessionDb) -> AsyncSessionView:
        return AsyncSessionView(obj, self)


class AsyncAsqueelView:
    """An asqueel object with its ``terminals`` awaitable and its ``blocked`` refused.

    A terminal runs with ``server.run_sync``, on a pool thread with the call's
    context. ``views`` names the methods whose result comes back wrapped in
    another view: a terminal's result once awaited, any other method's at once.
    """

    terminals: tuple[str, ...] = ()
    blocked: dict[str, str] = {}
    views: dict[str, type[AsyncAsqueelView]] = {}

    __slots__ = ("_obj", "_handler")

    def __init__(self, obj: Any, handler: AsqueelDbHandler) -> None:
        self._obj = obj
        self._handler = handler

    def __getattr__(self, name: str) -> Any:
        # Underscore names are never forwarded, as in kajenn's AsyncDbView.
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.blocked:
            raise UnsupportedFeatureError(
                f"{name}() is not offered on the async view: {self.blocked[name]}"
            )
        value = getattr(self._obj, name)
        view = self.views.get(name)
        handler = self._handler
        if name in self.terminals:

            async def terminal(*args: Any, **kwargs: Any) -> Any:
                result = await handler.server.run_sync(lambda: value(*args, **kwargs))
                return result if view is None else view(result, handler)

            return terminal
        if view is None:
            return value

        def wrapped(*args: Any, **kwargs: Any) -> AsyncAsqueelView:
            return view(value(*args, **kwargs), handler)

        return wrapped

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._obj!r})"


class AsyncCursorView(AsyncAsqueelView):
    """A ``SqlCursor``: its reads and ``close`` awaitable."""

    terminals = ("fetchone", "fetchmany", "fetchall", "close")


class AsyncQueryView(AsyncAsqueelView):
    """A ``SqlQuery``: every fetch awaitable; ``execute``/``cursor`` give a cursor view."""

    terminals = (
        "fetch",
        "fetchRecord",
        "fetchPkeys",
        "fetchAsJson",
        "fetchAsDict",
        "fetchAsBag",
        "fetchGrouped",
        "count",
        "delete",
        "update",
        "execute",
        "cursor",
    )
    blocked = {
        "iterfetch": "await cursor() and read it",
        "serverfetch": "await cursor() and read it",
        "servercursor": "await cursor() and read it",
        "selection": "not available in asqueel 0.9.0; await fetch()",
        "sql_delete": "await delete()",
        "sql_update": "await update()",
    }
    views = {"execute": AsyncCursorView, "cursor": AsyncCursorView}


class AsyncTableView(AsyncAsqueelView):
    """A ``SqlTable``: its writes and ``lock`` awaitable; ``query()`` gives a query view."""

    terminals = ("insert", "update", "delete", "insertOrUpdate", "lock")
    blocked = {
        "record": "not available in asqueel 0.9.0; await query(...).fetchRecord()",
        "sql_insert": "await insert()",
        "sql_update": "await update()",
        "sql_delete": "await delete()",
        "deleteRelated": "await delete(), which applies it",
        "updateRelated": "await update(), which applies it",
        "archiveRelatedRecords": "await delete(), which applies it",
    }
    views = {"query": AsyncQueryView}


class AsyncSessionView(AsyncAsqueelView):
    """A ``SessionDb``: statements and the transaction awaitable; tables and named
    sessions as views."""

    terminals = ("execute", "executemany", "commit", "rollback", "vacuum", "analyze")
    blocked = {
        "insert": "await table(name).insert()",
        "update": "await table(name).update()",
        "delete": "await table(name).delete()",
        "cursor": "await table(name).query(...).cursor()",
        "migrate": "the handler's migrate(name)",
        "migration_plan": "the handler's migration_plan(name)",
        "close": "the end of the call closes the session",
        "closeConnection": "the end of the call closes the session",
        "reloadModel": "not offered in a call",
        "setConstraintsDeferred": "await execute() with the statement",
    }
    views = {"execute": AsyncCursorView, "executemany": AsyncCursorView, "table": AsyncTableView}


AsyncSessionView.views["session"] = AsyncSessionView
