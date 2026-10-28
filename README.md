# kajenn-db

[![PyPI](https://img.shields.io/pypi/v/kajenn-db)](https://pypi.org/project/kajenn-db/)
[![Tests](https://github.com/kajenn-org/kajenn-db/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kajenn-org/kajenn-db/actions/workflows/tests.yml)
[![Codecov](https://codecov.io/gh/kajenn-org/kajenn-db/branch/main/graph/badge.svg)](https://app.codecov.io/gh/kajenn-org/kajenn-db)
[![Documentation](https://readthedocs.org/projects/kajenn-db/badge/?version=latest)](https://kajenn-db.readthedocs.io/en/latest/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://github.com/kajenn-org/kajenn-db/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue)](https://github.com/kajenn-org/kajenn-db/blob/main/LICENSE)

**Database handlers for the [kajenn](https://github.com/kajenn-org/kajenn)
ASGI server.** A handler connects a database library to kajenn: each call
(request, task, message) gets its own database session, and kajenn closes it
when the call ends.

kajenn itself never imports a database driver. A site declares its databases
with a handler class; this package provides those classes.

**Status**: Alpha. Version 0.1.0 requires `kajenn>=0.6.0` and Python 3.11+.

## Which handler

| Handler | Library | Sync routes | Async routes | Migrations | Status |
|---|---|---|---|---|---|
| `kajenn_db.asqueel.AsqueelDbHandler` | [asqueel](https://pypi.org/project/asqueel/) 0.9 | yes | yes | yes | supported |
| `kajenn_db.sqlalchemy.SqlAlchemyDbHandler` | SQLAlchemy 2 | yes | yes | no | probe |
| `kajenn_db.django.DjangoDbHandler` | Django 4.2+ | yes | no | yes | probe |

- **supported**: the handler meant for real applications.
- **probe**: a handler written to check that kajenn's handler contract works
  on a library other than asqueel. It implements the contract and nothing
  more, and its limits are deliberate. The
  [backend guides](https://kajenn-db.readthedocs.io/en/latest/) list them.

## Install

Install the extra of the library you use:

```bash
pip install "kajenn-db[asqueel]"
```

```bash
pip install "kajenn-db[sqlalchemy]"
```

```bash
pip install "kajenn-db[django]"
```

Each backend module is imported on its own (`kajenn_db.asqueel`). Importing it
without its extra raises an `ImportError` that names the command above.

## Quick start

The site recipe declares an application and one asqueel database:

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
```

- `models/shop.py` is an asqueel recipe that declares the tables.
- `_request.db()` returns the call's `SessionDb`. `_request.async_db()` returns
  its async view, for `async def` routes.
- At the end of the call the handler closes the session. Work not committed is
  rolled back.

The [Getting started](https://kajenn-db.readthedocs.io/en/latest/getting-started.html)
page has the recipe, the table creation and the commands to run it.

## Documentation

The documentation is on [Read the Docs](https://kajenn-db.readthedocs.io/):

- [Getting started](https://kajenn-db.readthedocs.io/en/latest/getting-started.html)
- [How a handler works](https://kajenn-db.readthedocs.io/en/latest/concepts.html)
- Backend guides:
  [asqueel](https://kajenn-db.readthedocs.io/en/latest/guides/asqueel.html),
  [SQLAlchemy](https://kajenn-db.readthedocs.io/en/latest/guides/sqlalchemy.html),
  [Django](https://kajenn-db.readthedocs.io/en/latest/guides/django.html)
- [API reference](https://kajenn-db.readthedocs.io/en/latest/api/index.html)
- [Changelog](CHANGELOG.md)

## Project

- kajenn: https://github.com/kajenn-org/kajenn
- asqueel: https://github.com/asqueel-org/asqueel
- Organization: https://github.com/kajenn-org
- Issues: https://github.com/kajenn-org/kajenn-db/issues

## License

Apache License 2.0, copyright Softwell S.r.l. See [LICENSE](LICENSE).
