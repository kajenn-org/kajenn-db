# How a handler works

kajenn defines the contract in `kajenn.db.DbHandlerBase`. Every handler of this
package is a subclass of it. This page describes what kajenn calls, and when.

## One handler per class

- A database is declared with a `handler_class`. The first database of a class
  builds the handler, `handler_class(server)`; the next ones reuse it.
- kajenn hands each database to the handler with
  `add_database(name, params)`. `params` are the arguments of the
  `database(...)` declaration, without `code` and `handler_class`. No I/O
  happens here.
- `open()` runs at lifespan startup and `close()` at lifespan shutdown. The
  handlers of this package open nothing at startup; at shutdown they close
  what they built (the `AsqueelDb`, the SQLAlchemy `Engine`).

```python
dbs = cfg.databases(handler_class=AsqueelDbHandler)
dbs.database(code="default", source="models/shop.py")
dbs.database(code="archive", source="models/archive.py")
```

Both databases are served by the same `AsqueelDbHandler`.

## Database names

- A database declared at server level is registered under its `code`.
- A database declared inside an application `chat` is registered as
  `chat:<code>`.
- `request.db()` without a name takes the application's default: its
  `db_name` when it declares one, else `<app>:default` when that database
  exists, else `default`. `request.async_db()` follows the same rule.

## One object per name per call

- `request.db(name)` calls `take(name, call)` the first time a call asks for
  `name`. The next `db(name)` of the same call returns the same object.
- `take` is sync and does no I/O. What it returns depends on the backend: a
  `SessionDb`, a `Session`, a Django connection.
- A call is a request, a task, a kbus call or a WSX message. A `db()` outside a
  call raises `RuntimeError`; an unknown name raises `LookupError`.

## Release at the end of the call

- At the end of the call kajenn calls `release(call)` once for every handler
  the call took from, on a pool thread.
- `call.databases` maps each name to the object taken. A handler releases only
  the names it serves.
- A `release` that raises is logged by kajenn, and the next handler is still
  released.

## Async routes

- `request.async_db(name)` takes the same object as `request.db(name)` and
  returns `handler.async_view(obj)`.
- In the default view, `kajenn.db.AsyncDbView`, the methods named in the
  handler's `terminals` are awaitable: each call runs with
  `server.run_sync`, on a pool thread, with the call's context. Every other
  attribute passes through unchanged.
- `db()` and `async_db()` of one call share one object, so one transaction.
- Each handler decides its own view. {doc}`guides/asqueel` has a view of its
  own; {doc}`guides/sqlalchemy` uses kajenn's; {doc}`guides/django` refuses
  async routes.

## Migrations

- `capabilities` names the optional operations a handler offers:
  `migration_plan` and `migrate`, or none.
- kajenn does not call them. The site calls them on the handler, with the
  database name.
- They are sync. An async caller runs them with `server.run_sync`:

```python
handler = server.databases["default"]
if "migrate" in handler.capabilities:
    await server.run_sync(handler.migrate, "default")
```

## Probes

`SqlAlchemyDbHandler` and `DjangoDbHandler` are probes. They were written to
check that the contract above works on libraries other than asqueel:

- they implement `add_database`, `take`, `release` and `async_view`, and
  nothing more;
- their limits are deliberate and are not planned to be lifted;
- the contract suite, `tests/contract/`, runs the same tests on the three
  handlers.
