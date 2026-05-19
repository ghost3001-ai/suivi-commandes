"""Add reception store to purchase orders.

Revision ID: 20260518_0002
Revises: 20260403_0001
Create Date: 2026-05-18 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = '20260518_0002'
down_revision = '20260403_0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'commandes' not in inspector.get_table_names():
        return

    existing_columns = {column['name'] for column in inspector.get_columns('commandes')}
    if 'magasin_reception' not in existing_columns:
        op.add_column('commandes', sa.Column('magasin_reception', sa.String(length=120), nullable=True))

    existing_indexes = {index['name'] for index in inspector.get_indexes('commandes')}
    if 'idx_commandes_magasin_reception' not in existing_indexes:
        op.create_index('idx_commandes_magasin_reception', 'commandes', ['magasin_reception'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'commandes' not in inspector.get_table_names():
        return

    existing_indexes = {index['name'] for index in inspector.get_indexes('commandes')}
    if 'idx_commandes_magasin_reception' in existing_indexes:
        op.drop_index('idx_commandes_magasin_reception', table_name='commandes')

    existing_columns = {column['name'] for column in inspector.get_columns('commandes')}
    if 'magasin_reception' in existing_columns:
        op.drop_column('commandes', 'magasin_reception')
