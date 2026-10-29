"""PostgreSQL tooling: migrations, row <-> model mapping, JSON import.

* ``db.migrate``     - apply ``db/migrations/*.sql`` to a database
* ``db.mapping``     - convert pydantic models to table rows and back
                       (used by ``repositories/pg_*`` and the importer)
* ``db.import_json`` - load the legacy ``backend/data/*.json`` into the database

See ``docs/database.md`` and ``docs/database-schema-plan.md``.
"""
