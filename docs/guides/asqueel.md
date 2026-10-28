# asqueel

`kajenn_db.asqueel.AsqueelDbHandler` serves databases built with
[asqueel](https://pypi.org/project/asqueel/) 0.9. It is the supported handler
of this package.

```bash
pip install "kajenn-db[asqueel]"
```

## Declare a database

```python
from kajenn_db.asqueel import AsqueelDbHandler

dbs = cfg.databases(handler_class=AsqueelDbHandler)
dbs.database(code="default", source="models/shop.py")
```

Each database accepts:

- `source`: anything `AsqueelDb(source, parents=...)` accepts:
  - a path to a recipe `.py` file, a directory holding `configure.py`, or a
    `.tytx` file; a relative path resolves against the process's current
    directory;
  - a name without `/` and without `.py`, looked up in asqueel's registry;
  - a recipe class or instance, or a mounted `db` node.
- `parents`: passed to `AsqueelDb` as it is.
- `db_class`: an `AsqueelDb` subclass to build instead of `AsqueelDb`.

The handler builds one `AsqueelDb` per database when the database is declared.
No connection is opened at that point. At server shutdown the handler closes
every `AsqueelDb`.

## One session per call

- `request.db(name)` takes the main `SessionDb` of the call
  (`AsqueelDb.session()`), once per name per call.
- `session(name)` on the main `SessionDb` opens a named session of the same
  call.
- At the end of the call the handler closes the named sessions of the call,
  then the main one. Every session is closed even when one of them raises;
  the first error is raised at the end, and kajenn logs it.
- Work not committed is rolled back, and asqueel logs a warning.
- In 0.1.0 a session opens with no environment: `currentUser` is `None`, and
  `workdate` and `locale` take asqueel's defaults. `AsqueelDb` accepts an
  `environment` argument, but the handler does not pass one: a database
  declaration has no parameter for it in this release.

## Async routes

An `async def` route takes `request.async_db(name)`: the async view of the same
main `SessionDb`. Its I/O methods are awaitable and run on the server pool, so
the event loop keeps serving other calls.

```python
class Notes(RoutedApplication):
    @route()
    async def add(self, _request=None):
        db = _request.async_db()
        notes = db.table("app.note")
        await notes.insert({"id": "n1", "text": "first"})
        await db.commit()
        return await notes.query(columns="$id").fetchPkeys()
```

The view has three levels, plus the cursor:

| Level | Awaitable | Returns a view |
|---|---|---|
| session | `execute`, `executemany`, `commit`, `rollback`, `vacuum`, `analyze` | `table(name)`, `session(name)` |
| table | `insert`, `update`, `delete`, `insertOrUpdate`, `lock` | `query(...)` |
| query | `fetch`, `fetchRecord`, `fetchPkeys`, `fetchAsJson`, `fetchAsDict`, `fetchAsBag`, `fetchGrouped`, `count`, `delete`, `update`, `execute`, `cursor` | |
| cursor | `fetchone`, `fetchmany`, `fetchall`, `close` | |

- The cursor view is returned by the session's `execute` and `executemany` and
  by the query's `execute` and `cursor`.
- Every other I/O method raises `UnsupportedFeatureError` on the view, naming
  what to use instead. For example, `iterfetch` and `serverfetch` name
  `cursor()`; `close` belongs to the end of the call; `migrate` and
  `migration_plan` belong to the handler.
- Methods that do no I/O pass through as asqueel has them.

The refused methods and what to use instead:

| Level | Refused | Use instead |
|---|---|---|
| session | `insert`, `update`, `delete` | `await table(name).insert()`, `.update()`, `.delete()` |
| session | `cursor` | `await table(name).query(...).cursor()` |
| session | `migrate`, `migration_plan` | the handler's `migrate(name)`, `migration_plan(name)` |
| session | `close`, `closeConnection` | the end of the call closes the session |
| session | `reloadModel` | not offered in a call |
| session | `setConstraintsDeferred` | `await execute()` with the statement |
| table | `record` | `await query(...).fetchRecord()` (`record` is not available in asqueel 0.9.0) |
| table | `sql_insert`, `sql_update`, `sql_delete` | `await insert()`, `update()`, `delete()` |
| table | `deleteRelated`, `archiveRelatedRecords` | `await delete()`, which applies it |
| table | `updateRelated` | `await update()`, which applies it |
| query | `iterfetch`, `serverfetch`, `servercursor` | `await cursor()` and read it |
| query | `selection` | `await fetch()` (`selection` is not available in asqueel 0.9.0) |
| query | `sql_delete`, `sql_update` | `await delete()`, `update()` |

### Rules

- In an async route, never call a `SessionDb` method on `request.db()`: it
  runs on the event loop and blocks every other call, with no error.
- Consume a cursor before the call ends: the end of the call closes the
  session, and the cursor with it.
- A streaming response that reads a cursor registers the release after its
  last chunk.
- `query(...).fetch()` loads every row in memory. For large results, read a
  server-side cursor (`cursorname`) in chunks.

## Serialising rows

kajenn serialises a `dict`/`list` result with `genro_tytx.json_dumps`
(genro-tytx 0.16.4). Rows returned as asqueel gives them do not survive it:

- a row of `fetch()` or `fetchAsDict()` (`NamedList`) becomes a bare list of
  values: the column names are lost;
- `date`, `datetime`, `Decimal` and `UUID` values raise `TypeError`.

Convert in the route: `dict(row.items())` keeps the column names, and the
values of those four types need converting too. `fetchAsJson()` returns the
JSON text itself: kajenn serves a `str` result as `text/plain`. The handler
converts nothing, so a query gives the same types in a sync and in an async
route.

## Migrations

`capabilities` is `{"migration_plan", "migrate"}`:

- `migration_plan(name)` returns the DDL that would bring database `name` to
  its model, as asqueel's plan `dict`. It changes nothing.
- `migrate(name)` applies it.
- Both are sync and work on a connection of their own, not on a call's
  session.

```python
handler = server.databases["default"]
plan = await server.run_sync(handler.migration_plan, "default")
if not plan["empty"]:
    await server.run_sync(handler.migrate, "default")
```

Both are tested on PostgreSQL and SQLite.
