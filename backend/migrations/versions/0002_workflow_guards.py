from alembic import op
import sqlalchemy as sa

revision = "0002_workflow_guards"
down_revision = "0001_initial"


def upgrade():
    op.add_column("cases", sa.Column("requested_at", sa.DateTime(timezone=True)))
    op.execute("""
        UPDATE cases c SET requested_at = COALESCE(
            (SELECT MAX(e.at) FROM events e WHERE e.case_id = c.id AND e.actor = 'Operator'
             AND e.title = 'Current ' || c.requested_kind || ' evidence requested'), c.state_since)
        WHERE c.requested_kind IS NOT NULL
    """)
    op.alter_column("attempts", "deadline", nullable=True)
    op.execute("""
        UPDATE attempts a SET deadline = NULL FROM jobs j
        WHERE j.source = a.id AND j.kind = 'dispatch' AND j.status = 'READY' AND j.tries = 0
          AND a.is_current AND a.status = 'PENDING' AND a.provider_ref IS NULL
    """)
    op.add_column("deliveries", sa.Column("failure", sa.String()), schema="sim")
    op.add_column("deliveries", sa.Column("request_digest", sa.String()), schema="sim")


def downgrade():
    # Restore timeout jobs that the original worker expects at enqueue time.
    op.execute("""
        UPDATE attempts SET deadline = CURRENT_TIMESTAMP + interval '15 seconds' WHERE deadline IS NULL
    """)
    op.execute("""
        INSERT INTO jobs (id, case_id, kind, source, operation_key, status, at, tries)
        SELECT gen_random_uuid()::text, case_id, 'timeout', id, 'timeout/' || id, 'READY', deadline, 0
        FROM attempts WHERE is_current AND status = 'PENDING'
        ON CONFLICT (operation_key) DO UPDATE SET at = EXCLUDED.at, status = 'READY',
             lease_token = NULL, lease_until = NULL
    """)
    op.alter_column("attempts", "deadline", nullable=False)
    op.drop_column("cases", "requested_at")
    op.drop_column("deliveries", "failure", schema="sim")
    op.drop_column("deliveries", "request_digest", schema="sim")
