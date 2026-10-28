# Django

`kajenn_db.django.DjangoDbHandler` serves the databases of Django's
`DATABASES`. It is a probe: it implements kajenn's handler contract and
nothing more (see [Probes](../concepts.md#probes)).

```bash
pip install "kajenn-db[django]"
```

## Declare a database

```python
from kajenn_db.django import DjangoDbHandler

dbs = cfg.databases(handler_class=DjangoDbHandler)
dbs.database(code="default", alias="default")
```

- Django is configured by the process (`DJANGO_SETTINGS_MODULE` or
  `settings.configure()`, then `django.setup()`), not by the handler.
- `alias`: a key of Django's `DATABASES`; the database's own name by default.

## One connection per call

- `request.db(name)` takes `connections[alias]`, the connection Django's ORM
  uses on the calling thread: the ORM in a sync route works on it.
- The end of the call closes it on the same thread.

## Limits

- Sync routes only. Django binds a connection to the thread that opened it,
  and kajenn takes a database of an `async def` route on the event-loop
  thread: `request.async_db(name)` raises `NotImplementedError`. An async route
  uses Django's async ORM, whose connections kajenn does not release.
- Not for sync work outside a route either: a task, a kbus call or a WSX
  message takes the connection on one pool thread, and kajenn releases it with
  `server.run_sync`, possibly on another. Django refuses to close it there
  (`DatabaseError`, logged by kajenn) and the connection stays open on the
  thread that opened it. This is tracked in
  [issue #1](https://github.com/kajenn-org/kajenn-db/issues/1).

## Migrations

`capabilities` is `{"migration_plan", "migrate"}`:

- `migration_plan(name)` lists the migrations not yet applied, as
  `app_label.name`.
- `migrate(name)` applies them (`manage.py migrate --database alias`).
- Both are sync and close the calling thread's connection at the end.
