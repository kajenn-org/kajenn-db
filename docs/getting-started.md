# Getting started

This page builds a kajenn site with one asqueel database on SQLite: a table,
a sync route that reads it and an async route that writes it.

## Install

```bash
pip install "kajenn-db[asqueel]"
```

The quotes keep shells such as zsh from reading the brackets as a pattern.
The `asqueel` extra installs `asqueel[postgresql,migration]`: the PostgreSQL
driver and asqueel's migration support come with it. The other backends have
their own extra, `sqlalchemy` and `django`.

Each backend module is imported on its own (`kajenn_db.asqueel`). Importing it
without its extra raises an `ImportError` that names the install command.

## Declare the tables

`models/shop.py` is an asqueel recipe: a module that defines exactly one
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

## Create the tables

`migrate.py` creates `shop.sqlite` and its tables from the recipe:

```python
from asqueel import AsqueelDb

db = AsqueelDb("models/shop.py")
db.migrate()
db.close()
```

```bash
python migrate.py
```

A running server can do the same through the handler: see
[Migrations](guides/asqueel.md#migrations).

## Declare the site

`app.py` declares the application and its database:

```python
from uuid import uuid4

from genro_routes import route
from kajenn import AsgiServer, RoutedApplication
from kajenn.config import AsgiConfigBuilder
from kajenn_db.asqueel import AsqueelDbHandler


class Notes(RoutedApplication):
    @route()
    def count(self, _request=None):
        return {"notes": _request.db().table("app.note").query().count()}

    @route()
    async def add(self, text: str, _request=None):
        db = _request.async_db()
        await db.table("app.note").insert({"id": uuid4().hex[:22], "text": text})
        await db.commit()
        return {"added": text}


class Configuration(AsgiConfigBuilder):
    default_config = False

    def main(self, root):
        cfg = root.configuration()
        cfg.applications().application(code="notes", app_class=Notes)
        dbs = cfg.databases(handler_class=AsqueelDbHandler)
        dbs.database(code="default", source="models/shop.py")


server = AsgiServer(config=Configuration)

if __name__ == "__main__":
    server.serve(host="127.0.0.1", port=8000)
```

- `default_config = False`: the recipe has no defaults layer of its own. With
  `None`, kajenn layers the recipe over `<base_dir>/config.py` when that file
  exists.
- `cfg.databases(handler_class=AsqueelDbHandler)` sets the handler of every
  database declared under it.
- `source` is resolved against the process's current directory.
- A route that declares `_request` receives the live request of the call.
- `_request.db()` returns the call's `SessionDb`. In an `async def` route,
  `_request.async_db()` returns its async view.
- No database is opened when the server starts. The first `db()` of a call
  opens a session; the end of the call closes it.

## Run it

```bash
python app.py
```

In another terminal:

```bash
curl http://127.0.0.1:8000/notes/count
```

```bash
curl "http://127.0.0.1:8000/notes/add?text=first"
```

The first call answers `{"notes":0}`, the second `{"added":"first"}`; `count`
then answers `{"notes":1}`.

## Next

- {doc}`concepts`: what kajenn asks of a handler and when it calls it.
- {doc}`guides/asqueel`: sessions, the async view, rows and migrations.
