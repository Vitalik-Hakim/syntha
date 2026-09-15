"""M9 prep (PRD v3 §4.1, §4.2 and the three M9 decisions in docs/DECISIONS.md).

- `census_runs`, `census_markets`, `census_observations`: the calibration census (D1), one
  `census_id` per month sample; rebuilt bit-identically from `raw/` and hashed like the
  normalized layer.
- `catch_all_reviews`: a market is a catch-all only after a person reviewed it. Durable: not a
  normalized table, so `make rebuild-normalized` leaves it alone.
- `markets.deadline_type` (BY | ON | NONE, D4 subset) and `markets.neg_risk_other` (Gamma's
  flag for the neg-risk "Other" market, verified live 2026-09-15), filled by the next rebuild.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSTZ = sa.DateTime(timezone=True)
PRICE = sa.Numeric(8, 6)
MONEY = sa.Numeric(18, 6)


def upgrade() -> None:
    op.add_column("markets", sa.Column("deadline_type", sa.Text))
    op.add_column("markets", sa.Column("neg_risk_other", sa.Boolean))
    op.create_table(
        "catch_all_reviews",
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("is_catch_all", sa.Boolean, nullable=False),
        sa.Column("reviewed_at", TSTZ, nullable=False),
        sa.Column("note", sa.Text),
        sa.PrimaryKeyConstraint("condition_id", name="pk_catch_all_reviews"),
    )
    op.create_table(
        "census_runs",
        sa.Column("census_id", sa.Text, nullable=False),
        sa.Column("built_at", TSTZ, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("n_markets", sa.Integer, nullable=False),
        sa.Column("n_observations", sa.Integer, nullable=False),
        sa.Column("manifest", pg.JSONB, nullable=False),
        sa.Column("quality", pg.JSONB),
        sa.PrimaryKeyConstraint("census_id", name="pk_census_runs"),
    )
    op.create_table(
        "census_markets",
        sa.Column("census_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("duration_class", sa.Text),
        sa.Column("structure", sa.Text),
        sa.Column("deadline_type", sa.Text),
        sa.Column("n_outcomes", sa.Integer),
        sa.Column("prints_truncated", sa.Boolean),
        sa.Column("resolution", sa.Text),
        sa.Column("neg_risk", sa.Boolean),
        sa.Column("open_ts", TSTZ),
        sa.Column("end_ts", TSTZ),
        sa.Column("tick_size", PRICE),
        sa.PrimaryKeyConstraint("census_id", "condition_id", name="pk_census_markets"),
    )
    op.create_table(
        "census_observations",
        sa.Column("census_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text),
        sa.Column("outcome_index", sa.Integer, nullable=False),
        sa.Column("offset_label", sa.Text, nullable=False),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("mid", PRICE),
        sa.Column("staleness_minutes", sa.Integer),
        sa.Column("fill_10m", PRICE),
        sa.Column("fill_30m", PRICE),
        sa.Column("fill_size_10m", MONEY),
        sa.Column("liq_24h", sa.Numeric(18, 2)),
        sa.Column("fee_entry", MONEY),
        sa.Column("fee_entry_current", MONEY),
        sa.Column("payout", PRICE),
        sa.PrimaryKeyConstraint(
            "census_id", "clob_token_id", "offset_label", name="pk_census_observations"
        ),
    )
    op.create_index(
        "ix_census_observations_census_id_condition_id",
        "census_observations",
        ["census_id", "condition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_census_observations_census_id_condition_id", "census_observations")
    op.drop_table("census_observations")
    op.drop_table("census_markets")
    op.drop_table("census_runs")
    op.drop_table("catch_all_reviews")
    op.drop_column("markets", "neg_risk_other")
    op.drop_column("markets", "deadline_type")
