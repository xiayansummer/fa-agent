"""investors_add_qmingpian_person_id

Revision ID: 30cc917a9faf
Revises: 6e30809e653d
Create Date: 2026-05-11 18:20:02.231654

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30cc917a9faf'
down_revision: Union[str, None] = '6e30809e653d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'investors',
        sa.Column('qmingpian_person_id', sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        'uq_investors_qmingpian_person_id', 'investors', ['qmingpian_person_id']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_investors_qmingpian_person_id', 'investors', type_='unique'
    )
    op.drop_column('investors', 'qmingpian_person_id')
