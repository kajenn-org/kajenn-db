# SQLAlchemy

`kajenn_db.sqlalchemy.SqlAlchemyDbHandler` serves SQLAlchemy 2 databases. It is
a probe: it implements kajenn's handler contract and nothing more (see
[Probes](../concepts.md#probes)).

```bash
pip install "kajenn-db[sqlalchemy]"
```

## Declare a database

```python
from kajenn_db.sqlalchemy import SqlAlchemyDbHandler

dbs = cfg.databases(handler_class=SqlAlchemyDbHandler)
dbs.database(code="default", url="sqlite:///shop.sqlite")
```

- `url`: the SQLAlchemy database URL.
- One shared `Engine` per database, disposed at server shutdown.

## One Session per call

- `request.db(name)` takes a `Session` of the call.
- The end of the call closes it. A closed `Session` refuses any further use
  (`close_resets_only=False`).

## Async routes

- `request.async_db(name)` is kajenn's `AsyncDbView` over the same `Session`,
  so a sync and an async route of one call share one transaction.
- Awaitable: `execute`, `scalars`, `scalar`, `get`, `get_one`, `merge`,
  `flush`, `refresh`, `commit`, `rollback`.

## Limits

- Every other attribute passes through as it is, I/O included: `query()` and
  its `Query.all()`, `delete()`, `connection()` and `begin()` run on the event
  loop and block it, with no error. Unlike the asqueel view, the probe refuses
  nothing.
- A `Result` reads its rows from the cursor when it is iterated, on the event
  loop: keep the results of an async route small.
- No `migrate`: SQLAlchemy has no migration of its own (Alembic is a separate
  tool). `capabilities` is empty.
