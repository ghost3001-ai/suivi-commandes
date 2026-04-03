"""Initial schema baseline.

Revision ID: 20260403_0001
Revises:
Create Date: 2026-04-03 09:15:00
"""

from alembic import op

from models import db


# revision identifiers, used by Alembic.
revision = '20260403_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    db.metadata.create_all(bind=bind)


def downgrade():
    bind = op.get_bind()
    db.metadata.drop_all(bind=bind)
