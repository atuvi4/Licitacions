"""L'estructura de l'oferta economica: quatre taules i cap xifra calculada.

    economic_offers            una per candidatura
    └── economic_offer_lots    un per lot del plec, o un de global
        └── economic_offer_sections
            └── economic_offer_lines

El que NO hi ha aqui es tan important com el que hi ha: cap columna de total, de
marge, de markup, d'equivalent mensual, d'import d'IVA ni de "supera el maxim".
Tot aixo es calcula a partir d'aquestes columnes cada vegada. Guardar-ne una
copia voldria dir tenir dues versions del mateix numero i cap manera de saber
quina mana el dia que discrepin.

**Els rangs els imposa la base.** Els CHECK son explicits, amb nom i amb els
valors congelats en aquest fitxer: `sa.Enum(..., native_enum=False)` nomes genera
un VARCHAR, i confiar-hi per protegir `pricing_basis` donaria un fals verd, com
va passar amb la solucio tecnica fins a 0024.

**Zero es valid; negatiu no.** Una linia a mig preparar te quantitat zero, i un
preu ofert de zero pot ser una decisio comercial legitima. El que no vol dir res
en cap d'aquests camps es un numero negatiu.

**Les dades economiques no s'esborren** (C-9): lots, seccions i linies porten
`archived_at`. Els CASCADE hi son per integritat de l'agregat —si mor la
candidatura no han de quedar preus orfes—, no com a operacio d'usuari.

`tender_lot_id` va amb RESTRICT i no amb SET NULL: si el lot del plec pogues
desapareixer deixant la fila, es perdria de quin lot son aquests preus, i un
bloc sense lot ja vol dir una altra cosa (tota la licitacio).

Revision ID: 0025_economic_offer
Revises: 0024_technical_solution
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0025_economic_offer'
down_revision: str | None = '0024_technical_solution'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OFFERS = 'economic_offers'
_LOTS = 'economic_offer_lots'
_SECTIONS = 'economic_offer_sections'
_LINES = 'economic_offer_lines'

#: Congelats aqui a proposit: una migracio no ha d'importar els enums de l'app,
#: que canvien, si el que ha de descriure es l'esquema d'aquest moment.
_PRICING_BASES = ('one_off', 'monthly', 'annual', 'per_unit')

#: Actiu vol dir no arxivat. Les dues unicitats de lot son parcials per aixo:
#: arxivar un bloc i tornar a comencar no ha d'obligar a reescriure la historia.
_ACTIVE = 'archived_at IS NULL'
_WITH_TENDER_LOT = f'tender_lot_id IS NOT NULL AND {_ACTIVE}'
_WITHOUT_TENDER_LOT = f'tender_lot_id IS NULL AND {_ACTIVE}'


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _actor_columns() -> list[sa.Column]:
    return [
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _actor_constraints(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'],
            name=op.f(f'fk_{table}_company_id_companies'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name=op.f(f'fk_{table}_created_by_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by'], ['users.id'],
            name=op.f(f'fk_{table}_updated_by_users'),
            ondelete='SET NULL',
        ),
    ]


def upgrade() -> None:
    # --- L'oferta: arrel unica de preus de la candidatura --------------------
    op.create_table(
        _OFFERS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        # Compta modificacions perque S5 hi pugui exigir `expected_revision`.
        # Aqui no l'incrementa ningu.
        sa.Column('revision', sa.Integer(), nullable=False),
        *_actor_columns(),
        sa.CheckConstraint(
            'revision >= 0', name=op.f('ck_economic_offers_revision_non_negative')
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['bid_workspaces.id'],
            name=op.f('fk_economic_offers_workspace_id_bid_workspaces'),
            ondelete='CASCADE',
        ),
        *_actor_constraints(_OFFERS),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_economic_offers')),
        # Una per candidatura: no hi ha cap estat en que dues convisquin, aixi
        # que aquesta unicitat no es parcial.
        sa.UniqueConstraint('workspace_id', name=op.f('uq_economic_offers_workspace')),
    )
    op.create_index('ix_economic_offers_company_id', _OFFERS, ['company_id'])

    # --- El bloc de preus d'un lot del plec, o el bloc global ----------------
    op.create_table(
        _LOTS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('offer_id', sa.Uuid(), nullable=False),
        sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
        sa.Column('label', sa.String(length=300), nullable=False),
        # Manual, mai inferida (C-3): es la durada per defecte de les linies.
        sa.Column('duration_months', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        *_actor_columns(),
        sa.CheckConstraint(
            'duration_months IS NULL OR duration_months >= 1',
            name=op.f('ck_economic_offer_lots_duration_at_least_one'),
        ),
        sa.CheckConstraint(
            'position >= 0', name=op.f('ck_economic_offer_lots_position_non_negative')
        ),
        sa.ForeignKeyConstraint(
            ['offer_id'], ['economic_offers.id'],
            name=op.f('fk_economic_offer_lots_offer_id_economic_offers'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['tender_lot_id'], ['tender_lots.id'],
            name=op.f('fk_economic_offer_lots_tender_lot_id_tender_lots'),
            ondelete='RESTRICT',
        ),
        *_actor_constraints(_LOTS),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_economic_offer_lots')),
    )
    op.create_index('ix_economic_offer_lots_company_id', _LOTS, ['company_id'])
    op.create_index('ix_economic_offer_lots_offer', _LOTS, ['offer_id', 'position'])
    op.create_index(
        'uq_economic_offer_lots_tender_lot',
        _LOTS,
        ['offer_id', 'tender_lot_id'],
        unique=True,
        postgresql_where=sa.text(_WITH_TENDER_LOT),
        sqlite_where=sa.text(_WITH_TENDER_LOT),
    )
    op.create_index(
        'uq_economic_offer_lots_global',
        _LOTS,
        ['offer_id'],
        unique=True,
        postgresql_where=sa.text(_WITHOUT_TENDER_LOT),
        sqlite_where=sa.text(_WITHOUT_TENDER_LOT),
    )

    # --- Com s'agrupa l'oferta perque una persona la pugui llegir ------------
    op.create_table(
        _SECTIONS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('offer_lot_id', sa.Uuid(), nullable=False),
        # El titol es una dada, no un enum: cada plec s'estructura com demana.
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        *_actor_columns(),
        sa.CheckConstraint(
            'position >= 0', name=op.f('ck_economic_offer_sections_position_non_negative')
        ),
        sa.ForeignKeyConstraint(
            ['offer_lot_id'], ['economic_offer_lots.id'],
            name=op.f('fk_economic_offer_sections_offer_lot_id_economic_offer_lots'),
            ondelete='CASCADE',
        ),
        *_actor_constraints(_SECTIONS),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_economic_offer_sections')),
    )
    op.create_index('ix_economic_offer_sections_company_id', _SECTIONS, ['company_id'])
    op.create_index('ix_economic_offer_sections_lot', _SECTIONS, ['offer_lot_id', 'position'])

    # --- La linia de preu ----------------------------------------------------
    op.create_table(
        _LINES,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('section_id', sa.Uuid(), nullable=False),
        sa.Column('concept', sa.String(length=500), nullable=False),
        # Vincles opcionals: si una extraccio nova se'ls emporta, el preu escrit
        # es conserva sense vincle.
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('technical_solution_id', sa.Uuid(), nullable=True),
        # Text i no claus foranes: el cataleg queda fora de l'MVP (C-10).
        sa.Column('supplier_name', sa.String(length=300), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        # Quatre decimals als unitaris: arrodonir abans de multiplicar canviaria
        # el total. L'arrodoniment es fa en tancar els imports (C-7).
        sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            'pricing_basis',
            sa.Enum(*_PRICING_BASES, name='pricing_basis', native_enum=False),
            nullable=False,
        ),
        sa.Column('duration_months_override', sa.Integer(), nullable=True),
        sa.Column('unit_cost_ex_vat', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            'offered_unit_price_ex_vat', sa.Numeric(precision=18, scale=4), nullable=True
        ),
        sa.Column(
            'tender_max_unit_price_ex_vat', sa.Numeric(precision=18, scale=4), nullable=True
        ),
        # En tant per cent (21,00), mai un factor.
        sa.Column('vat_rate', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        *_actor_columns(),
        sa.CheckConstraint(
            _in_clause('pricing_basis', _PRICING_BASES),
            name=op.f('ck_economic_offer_lines_basis_valid'),
        ),
        sa.CheckConstraint(
            'quantity >= 0', name=op.f('ck_economic_offer_lines_quantity_non_negative')
        ),
        sa.CheckConstraint(
            'duration_months_override IS NULL OR duration_months_override >= 1',
            name=op.f('ck_economic_offer_lines_duration_override_at_least_one'),
        ),
        sa.CheckConstraint(
            'unit_cost_ex_vat IS NULL OR unit_cost_ex_vat >= 0',
            name=op.f('ck_economic_offer_lines_unit_cost_non_negative'),
        ),
        sa.CheckConstraint(
            'offered_unit_price_ex_vat IS NULL OR offered_unit_price_ex_vat >= 0',
            name=op.f('ck_economic_offer_lines_offered_price_non_negative'),
        ),
        sa.CheckConstraint(
            'tender_max_unit_price_ex_vat IS NULL OR tender_max_unit_price_ex_vat >= 0',
            name=op.f('ck_economic_offer_lines_tender_max_non_negative'),
        ),
        sa.CheckConstraint(
            'vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 100)',
            name=op.f('ck_economic_offer_lines_vat_rate_range'),
        ),
        sa.CheckConstraint(
            'position >= 0', name=op.f('ck_economic_offer_lines_position_non_negative')
        ),
        sa.ForeignKeyConstraint(
            ['section_id'], ['economic_offer_sections.id'],
            name=op.f('fk_economic_offer_lines_section_id_economic_offer_sections'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['requirement_id'], ['requirements.id'],
            name=op.f('fk_economic_offer_lines_requirement_id_requirements'),
            ondelete='SET NULL',
        ),
        # Nom explicit i curt: el que generaria la convencio passa dels 63
        # caracters de PostgreSQL i quedaria truncat amb un hash.
        sa.ForeignKeyConstraint(
            ['technical_solution_id'], ['workspace_technical_solutions.id'],
            name=op.f('fk_economic_offer_lines_technical_solution'),
            ondelete='SET NULL',
        ),
        *_actor_constraints(_LINES),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_economic_offer_lines')),
    )
    op.create_index('ix_economic_offer_lines_company_id', _LINES, ['company_id'])
    op.create_index('ix_economic_offer_lines_section', _LINES, ['section_id', 'position'])
    op.create_index('ix_economic_offer_lines_requirement', _LINES, ['requirement_id'])
    op.create_index(
        'ix_economic_offer_lines_technical_solution', _LINES, ['technical_solution_id']
    )


def downgrade() -> None:
    # De la fulla a l'arrel: linies, seccions, lots i oferta.
    op.drop_index('ix_economic_offer_lines_technical_solution', table_name=_LINES)
    op.drop_index('ix_economic_offer_lines_requirement', table_name=_LINES)
    op.drop_index('ix_economic_offer_lines_section', table_name=_LINES)
    op.drop_index('ix_economic_offer_lines_company_id', table_name=_LINES)
    op.drop_table(_LINES)

    op.drop_index('ix_economic_offer_sections_lot', table_name=_SECTIONS)
    op.drop_index('ix_economic_offer_sections_company_id', table_name=_SECTIONS)
    op.drop_table(_SECTIONS)

    op.drop_index(
        'uq_economic_offer_lots_global',
        table_name=_LOTS,
        postgresql_where=sa.text(_WITHOUT_TENDER_LOT),
        sqlite_where=sa.text(_WITHOUT_TENDER_LOT),
    )
    op.drop_index(
        'uq_economic_offer_lots_tender_lot',
        table_name=_LOTS,
        postgresql_where=sa.text(_WITH_TENDER_LOT),
        sqlite_where=sa.text(_WITH_TENDER_LOT),
    )
    op.drop_index('ix_economic_offer_lots_offer', table_name=_LOTS)
    op.drop_index('ix_economic_offer_lots_company_id', table_name=_LOTS)
    op.drop_table(_LOTS)

    op.drop_index('ix_economic_offers_company_id', table_name=_OFFERS)
    op.drop_table(_OFFERS)
