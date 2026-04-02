"""Add KB document metadata columns

Revision ID: fc2cd260291c
Revises: b71772679c31
Create Date: 2026-04-02 13:06:29.970945

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fc2cd260291c'
down_revision: Union[str, Sequence[str], None] = 'b71772679c31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add new columns to knowledge_base
    op.add_column('knowledge_base', sa.Column('source_type', sa.String(), nullable=False, server_default='faq'))
    op.add_column('knowledge_base', sa.Column('source_name', sa.String(), nullable=True))
    op.add_column('knowledge_base', sa.Column('storage_path', sa.String(), nullable=True))
    op.add_column('knowledge_base', sa.Column('chunk_index', sa.Integer(), nullable=True))
    
    # Create indexes for the new columns
    op.create_index(op.f('ix_knowledge_base_chunk_index'), 'knowledge_base', ['chunk_index'], unique=False)
    op.create_index(op.f('ix_knowledge_base_source_name'), 'knowledge_base', ['source_name'], unique=False)
    op.create_index(op.f('ix_knowledge_base_source_type'), 'knowledge_base', ['source_type'], unique=False)
    op.create_index(op.f('ix_knowledge_base_storage_path'), 'knowledge_base', ['storage_path'], unique=False)
    
    # Ensure source_type doesn't have a server default if not needed after initial backfill
    op.alter_column('knowledge_base', 'source_type', server_default=None)

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_knowledge_base_storage_path'), table_name='knowledge_base')
    op.drop_index(op.f('ix_knowledge_base_source_type'), table_name='knowledge_base')
    op.drop_index(op.f('ix_knowledge_base_source_name'), table_name='knowledge_base')
    op.drop_index(op.f('ix_knowledge_base_chunk_index'), table_name='knowledge_base')
    op.drop_column('knowledge_base', 'chunk_index')
    op.drop_column('knowledge_base', 'storage_path')
    op.drop_column('knowledge_base', 'source_name')
    op.drop_column('knowledge_base', 'source_type')
