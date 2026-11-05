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

- `request.db(name)` takes a connection of the call, created for it
  (`connections.create_connection(alias)`).
- `take` installs it as `connections[alias]` on the calling thread: Django's
  ORM in the call works on it. The next call on the same thread installs its
  own.
- The connection is shared between threads (`inc_thread_sharing()`). kajenn
  releases a task, a kbus call or a WSX message on any pool thread, and the
  end of the call closes the connection there.

## Limits

- No `async def` routes. kajenn takes a database of an async route on the
  event-loop thread, where Django's ORM does not run:
  `request.async_db(name)` raises `NotImplementedError`. An async route uses
  Django's async ORM, whose connections kajenn does not release.
- One connection per call: Django's persistent connections (`CONN_MAX_AGE`)
  are not reused across calls.

## Migrations

`capabilities` is `{"migration_plan", "migrate"}`:

- `migration_plan(name)` lists the migrations not yet applied, as
  `app_label.name`.
- `migrate(name)` applies them (`manage.py migrate --database alias`).
- Both are sync and close the calling thread's connection at the end.
