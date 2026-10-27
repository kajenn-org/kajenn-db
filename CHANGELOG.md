# Changelog

All notable changes to kajenn-db are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-10-27

First release. Requires `kajenn>=0.6.0`, the first kajenn with the
`DbHandlerBase` contract.

### Added

- `kajenn_db.asqueel.AsqueelDbHandler` (extra `[asqueel]`, asqueel 0.9): one
  `AsqueelDb` per database, built from `source`, `parents` and `db_class`; one
  main `SessionDb` per name per call; the end of the call closes the call's
  named sessions, then the main one.
- The asqueel async view: session, table, query and cursor levels, with
  awaitable terminals run on the server pool and blocked methods that name
  what to use instead.
- `AsqueelDbHandler.migration_plan(name)` and `migrate(name)`, tested on
  PostgreSQL and SQLite.
- `kajenn_db.sqlalchemy.SqlAlchemyDbHandler` (extra `[sqlalchemy]`), a probe:
  one `Engine` per database, one `Session` per call, kajenn's `AsyncDbView`
  over the same `Session` for async routes; no migration.
- `kajenn_db.django.DjangoDbHandler` (extra `[django]`), a probe:
  `connections[alias]` per call, closed at the end of the call; sync routes
  only; `migration_plan(name)` and `migrate(name)`.
- A contract suite that runs kajenn's handler contract on every installed
  backend.

### Known limits

- A session opens with no environment: `currentUser` is `None`.
- Rows as asqueel returns them do not survive kajenn's JSON serialisation:
  convert them in the route.
- The Django probe cannot serve `async def` routes: kajenn takes their
  databases on the event-loop thread, and Django binds a connection to its
  thread.
