# Contributing

Source and issues: <https://github.com/kajenn-org/kajenn-db>.

## Set up

```bash
pip install -e ".[dev,asqueel,sqlalchemy,django]"
```

## Run the tests

```bash
pytest
```

- The suite needs the `asqueel` extra. The SQLAlchemy and Django tests are
  skipped when their extra is missing.
- The PostgreSQL tests read the server from `GNR_TEST_PG_HOST` (default
  `127.0.0.1`), `GNR_TEST_PG_PORT` (`5432`), `GNR_TEST_PG_USER` (`postgres`)
  and `GNR_TEST_PG_PASSWORD`. They are skipped when the server is not
  reachable.
- `tests/contract/` runs kajenn's handler contract on every installed
  backend. Its async tests are skipped for Django, which serves sync routes
  only.

Coverage, as the CI measures it:

```bash
pytest --cov=kajenn_db --cov-report=term-missing
```

## Lint

```bash
ruff check src/ tests/
```

## Build the documentation

```bash
pip install -e ".[docs,asqueel,sqlalchemy,django]"
sphinx-build -W -b html docs docs/_build/html
```

Read the Docs builds with the same extras and fails on any warning.

## Continuous integration

- `tests.yml` runs ruff and pytest with PostgreSQL 16 on every push and pull
  request to `main`, and once a day against the latest kajenn and backend
  releases. It uploads coverage to Codecov.
- `publish.yml` publishes to PyPI when a `v*` tag is pushed, through PyPI's
  trusted publisher, and creates the GitHub release.
