"""Migration operations that only some databases can carry out.

Production is Postgres and development is SQLite, which is fine until a
migration reaches for something only one of them has. Rather than let the
migration graph fork — two histories is a much worse problem than one missing
index — the operation stays in the graph, keeps its place in model state, and
simply does nothing where the database can't oblige.
"""

from django.db import migrations


class AddIndexIfPostgres(migrations.AddIndex):
    """`AddIndex` that is a no-op anywhere but Postgres.

    GIN has no equivalent in SQLite, which refuses the `CREATE INDEX` outright.
    The index still belongs in `Meta.indexes` — it is real, production has it,
    and leaving it out would make `makemigrations --check` ask for it on every
    run — so only the SQL is skipped. Development loses nothing that matters:
    search there is a `LIKE` scan over a few hundred rows.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)
