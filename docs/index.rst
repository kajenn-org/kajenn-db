kajenn-db documentation
=======================

**kajenn-db** provides the database handlers of the
`kajenn <https://github.com/kajenn-org/kajenn>`_ ASGI server. A handler
connects a database library to kajenn: each call (request, task, message) gets
its own database session, and kajenn closes it when the call ends.

kajenn itself never imports a database driver. A site declares its databases
with a handler class; this package provides those classes.

.. list-table::
   :header-rows: 1

   * - Handler
     - Library
     - Sync routes
     - Async routes
     - Migrations
     - Status
   * - :class:`~kajenn_db.asqueel.AsqueelDbHandler`
     - `asqueel <https://pypi.org/project/asqueel/>`_ 0.9
     - yes
     - yes
     - yes
     - supported
   * - :class:`~kajenn_db.sqlalchemy.SqlAlchemyDbHandler`
     - SQLAlchemy 2
     - yes
     - yes
     - no
     - probe
   * - :class:`~kajenn_db.django.DjangoDbHandler`
     - Django 4.2+
     - yes
     - no
     - yes
     - probe

- **supported**: the handler meant for real applications.
- **probe**: a handler written to check that kajenn's handler contract works
  on a library other than asqueel. It implements the contract and nothing
  more, and its limits are deliberate. Each backend guide lists them.

New here? Start with :doc:`getting-started`, then read :doc:`concepts`.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting-started
   concepts

.. toctree::
   :maxdepth: 2
   :caption: Backend guides

   guides/index

.. toctree::
   :maxdepth: 2
   :caption: API reference

   api/index

.. toctree::
   :maxdepth: 1
   :caption: Project

   contributing
   changelog

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
