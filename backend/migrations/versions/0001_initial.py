from pathlib import Path
from alembic import op

revision = "0001_initial"
down_revision = None


def upgrade():
    ddl = Path(__file__).with_name("0001_schema.sql").read_text()
    for statement in ddl.split(";"):
        if statement.strip():
            op.execute(statement.strip())


def downgrade():
    for name in ("jobs", "receipts", "events", "documents", "attempts", "cases"):
        op.drop_table(name)
    op.drop_table("deliveries", schema="sim")
    op.drop_table("merchants", schema="sim")
    op.execute("DROP SCHEMA sim")
