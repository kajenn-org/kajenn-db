# kajenn-db

Database handlers for [kajenn](https://github.com/kajenn-org/kajenn): the
implementations of kajenn's `DbHandlerBase` contract, one module per backend.

- `kajenn_db.asqueel.AsqueelDbHandler` — [asqueel](https://pypi.org/project/asqueel/) 0.9.
- `kajenn_db.sqlalchemy.SqlAlchemyDbHandler` — SQLAlchemy 2, a probe.
- `kajenn_db.django.DjangoDbHandler` — Django, a probe.

The probes are incomplete by declaration: they run kajenn's handler contract
on another backend and stop there (see [Probes](#probes)).

## Install

```bash
pip install "kajenn-db[asqueel]"
pip install "kajenn-db[sqlalchemy]"
pip install "kajenn-db[django]"
```

The quotes keep shells such as zsh from reading the brackets as a pattern.
The `asqueel` extra installs `asqueel[postgresql,migration]`: the PostgreSQL
driver and asqueel's migration support come with it.

Importing a backend module without its extra raises an `ImportError` that
names the command above.

## Declare an asqueel database

A site recipe declares the application and its databases:

```python
from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn_db.asqueel import AsqueelDbHandler


class Notes(RoutedApplication):
    @route()
    def count(self, _request=None):
        return _request.db().table("app.note").query().count()


class Configuration(AsgiConfigBuilder):
    default_config = False

    def main(self, root):
        cfg = root.configuration()
        cfg.applications().application(code="notes", app_class=Notes)
        dbs = cfg.databases(handler_class=AsqueelDbHandler)
        dbs.database(code="default", source="models/shop.py")


server = AsgiServer(config=Configuration)
```

- `default_config = False` — the recipe has no defaults layer of its own. With
  `None`, kajenn layers the recipe over `<base_dir>/config.py` when that file
  exists.
- A route that declares `_request` receives the live `Request` of the call.
- `models/shop.py` is an asqueel recipe: a module that defines exactly one
  `SqlDatabaseConfig` subclass.

```python
from asqueel import SqlDatabaseConfig


class Shop(SqlDatabaseConfig):
    def main(self, root):
        db = root.db("shop", implementation="sqlite")
        db.connection("main", database="shop.sqlite")
        package = db.packages().package("app", sqlschema="main", sqlprefix=False)
        note = package.table("note", pkey="id")
        note.column("id", size=":22")
        note.column("text")
```

Each database accepts:

- `source` — anything `AsqueelDb(source, parents=...)` accepts:
  - a path to a recipe `.py` file, a directory holding `configure.py`, or a
    `.tytx` file; a relative path resolves against the process's current
    directory;
  - a name without `/` and without `.py`, looked up in asqueel's registry;
  - a recipe class or instance, or a mounted `db` node.
- `parents` — passed to `AsqueelDb` as it is.
- `db_class` — an `AsqueelDb` subclass to build instead of `AsqueelDb`.

The server builds one `AsqueelDbHandler` and hands it every database declared
with it. No database is opened at declaration time.

## One session per call

- `request.db(name)` takes the main `SessionDb` of the call (`db.session()`),
  once per name per call.
- `name` is optional. Without it, kajenn takes the application's own default
  database: its `db_name` when it declares one, else `<app>:default` when that
  database exists, else `default`. The same rule holds for
  `request.async_db()`.
- At the end of the call the handler closes the named sessions of the call,
  then the main one. Work not committed is rolled back, and asqueel logs a
  warning.
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

- session — awaitable: `execute`, `executemany`, `commit`, `rollback`,
  `vacuum`, `analyze`. `table(name)` and `session(name)` return views.
- table — awaitable: `insert`, `update`, `delete`, `insertOrUpdate`, `lock`.
  `query(...)` returns a view.
- query — awaitable: `fetch`, `fetchRecord`, `fetchPkeys`, `fetchAsJson`,
  `fetchAsDict`, `fetchAsBag`, `fetchGrouped`, `count`, `delete`, `update`,
  `execute`, `cursor`.
- cursor — returned by the session's `execute` and `executemany` and by the
  query's `execute` and `cursor`; awaitable: `fetchone`, `fetchmany`,
  `fetchall`, `close`.

Every other I/O method raises `UnsupportedFeatureError` on the view, naming
what to use instead: `iterfetch` and `serverfetch` name `cursor()`; `close`
belongs to the end of the call; `migrate` and `migration_plan` belong to the
handler. Methods that do no I/O pass through as asqueel has them.

Rules:

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

## Probes

### SQLAlchemy

```python
from kajenn_db.sqlalchemy import SqlAlchemyDbHandler

dbs = cfg.databases(handler_class=SqlAlchemyDbHandler)
dbs.database(code="default", url="sqlite:///shop.sqlite")
```

- `url` — the SQLAlchemy database URL; one shared `Engine` per database,
  disposed at server shutdown.
- `request.db(name)` takes a `Session` of the call; the end of the call closes
  it, and a closed `Session` refuses any further use.
- `request.async_db(name)` is kajenn's `AsyncDbView` over the same `Session`,
  so one call has one transaction in a sync and in an async route. Awaitable:
  `execute`, `scalars`, `scalar`, `get`, `get_one`, `merge`, `flush`,
  `refresh`, `commit`, `rollback`. Every other attribute passes through as it
  is, I/O included: `query()` and its `Query.all()`, `delete()`, `connection()`
  and `begin()` run on the event loop and block it, with no error. Unlike the
  asqueel view, the probe refuses nothing.
- A `Result` reads its rows from the cursor when it is iterated, on the event
  loop: keep the results of an async route small.
- No `migrate`: SQLAlchemy has no migration of its own (Alembic is a separate
  tool).

### Django

```python
from kajenn_db.django import DjangoDbHandler

dbs = cfg.databases(handler_class=DjangoDbHandler)
dbs.database(code="default", alias="default")
```

- Django is configured by the process (`DJANGO_SETTINGS_MODULE` or
  `settings.configure()`, then `django.setup()`), not by the handler.
- `alias` — a key of Django's `DATABASES`; the database's own name by default.
- `request.db(name)` takes `connections[alias]`, the connection Django's ORM
  uses on the calling thread: the ORM in a sync route works on it. The end of
  the call closes it on the same thread.
- Sync routes only. Django binds a connection to the thread that opened it,
  and kajenn takes a database of an `async def` route on the event-loop
  thread: `request.async_db(name)` raises `NotImplementedError`. An async route
  uses Django's async ORM, whose connections kajenn does not release.
- Not for sync work outside a route either: a task, a kbus call or a WSX
  message takes the connection on one pool thread, and kajenn releases it with
  `server.run_sync`, possibly on another. Django refuses to close it there
  (`DatabaseError`, logged by kajenn) and the connection stays open on the
  thread that opened it.
- `migration_plan(name)` lists the migrations not yet applied, as
  `app_label.name`; `migrate(name)` applies them (`manage.py migrate
  --database alias`). Both are sync and close the calling thread's connection
  at the end.

## Tests

```bash
pip install -e ".[dev,asqueel,sqlalchemy,django]"
pytest
```

- The suite needs the `asqueel` extra. The SQLAlchemy and Django tests are
  skipped when their extra is missing.
- The PostgreSQL tests read the server from `GNR_TEST_PG_HOST` (default
  `127.0.0.1`), `GNR_TEST_PG_PORT` (`5432`), `GNR_TEST_PG_USER` (`postgres`)
  and `GNR_TEST_PG_PASSWORD`. They are skipped when the server is not
  reachable.
