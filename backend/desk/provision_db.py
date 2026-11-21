"""Grant runtime roles access to their own tables after applying the schema migration."""

from pathlib import Path

from psycopg import sql

from .db import engine
from .settings import key_material


def provision():
    with engine.begin() as transaction:
        connection = transaction.connection.driver_connection
        connection.execute("SELECT pg_advisory_xact_lock(2676001)")
        for role, directory in (
            ("desk_workflow", "workflow-login"),
            ("desk_provider", "provider-login"),
        ):
            password = key_material(Path("/var/lib") / directory / "login.key").hex()
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
            )

        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO desk_workflow")
        connection.execute(
            "GRANT SELECT, INSERT, UPDATE ON cases, documents, attempts, jobs, receipts TO desk_workflow"
        )
        connection.execute("GRANT SELECT, INSERT ON events TO desk_workflow")
        connection.execute("GRANT USAGE ON SEQUENCE events_id_seq TO desk_workflow")
        connection.execute("GRANT USAGE ON SCHEMA sim TO desk_provider")
        connection.execute("GRANT SELECT, INSERT, UPDATE ON sim.deliveries TO desk_provider")
        connection.execute("GRANT SELECT, INSERT ON sim.merchants TO desk_provider")


if __name__ == "__main__":
    provision()
