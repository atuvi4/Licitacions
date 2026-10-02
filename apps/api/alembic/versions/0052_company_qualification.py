"""Qualificacio de l'empresa i mitjans operatius (fase 3A, pas 5).

El que un plec demana per QUALIFICAR una empresa (solvencia economica,
referencies d'execucio, assegurances, registres sectorials, perfils de l'equip,
instruments) ja tenia taula al perfil 4C, pero fora del coneixement actual: sense
procedencia, sense entrar a la fotografia ni a l'empremta, i sense l'abast o els
codis estructurats que permeten comparar-ho amb un plec. Aquesta migracio ho
incorpora amb el minim de canvis:

* `company_financial_years`, `company_projects`, `company_insurances`,
  `company_registrations` i `company_people` guanyen l'origen del coneixement
  (`knowledge_origin`, `verification_status`, `lifecycle`, amb valors per defecte
  neutres: `declared`, pendent, `current`) i `as_of` EXPLICIT i nul·lable: nul =
  ningu no ha dit de quan es la dada. Cap backfill: les files que ja existien
  queden amb `as_of` nul (la fotografia hi posa la data de creacio en UTC).
  `valid_until` no cal: l'any, la data de fi i `expires_at` ja diuen la vigencia.
* `company_project_annual_amounts` (nova): import EXECUTAT d'un projecte per any
  (UNIQUE projecte + any), font de veritat per a «l'any de major execucio».
* `company_registrations.scope_service_lines` (abast estructurat de la
  inscripcio, com a les certificacions) i el tipus `sector_operator_registry`
  (l'emissor es a `issuer`: res de cap registre concret al codi).
* `company_people.profile_codes` (perfils professionals del vocabulari).
* `company_operating_facts.value_number` i la clau `ipv4_assignable_count`: un
  fet quantitatiu es un fet, no una taula nova. El nombre nomes es permes a les
  claus numeriques i mai negatiu.
* `company_insurance_limits.kind` admet `employer_per_victim` (RC patronal per
  victima, que no es el sublimit general per victima): la columna passa de
  VARCHAR(16) a VARCHAR(19).
* `company_equipment` (nova): equips i instruments adscrivibles, amb mode
  d'acces, calibratge i la procedencia completa.

**Neutralitat de l'upgrade.** Cap INSERT, UPDATE ni DELETE. Cap fila canvia de
significat; cap empremta d'una empresa sense files en aquestes taules canvia.

**Downgrade: refusa, no reinterpreta.** 0051 no pot guardar una verificacio, un
cicle de vida, un origen promogut, un `as_of` explicit, un abast o uns perfils
estructurats, un fet numeric, un limit de RC patronal, cap import anual ni cap
equip. La guarda decideix per representabilitat (valors explicits), no per cap
comparacio de dates: no depen de cap zona horaria. Abans de tocar
l'esquema comprova que no hi hagi cap fila aixi; si n'hi ha, s'atura amb
`downgrade_0052_incompatible_rows` i no fa res (al SQL offline, com a bloc `DO`).
Qui de debo hagi de tornar a 0051 resol aquestes files a ma abans.

Revision ID: 0052_company_qualification
Revises: 0051_tender_analysis
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = '0052_company_qualification'
down_revision: str | None = '0051_tender_analysis'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_ORIGINS = ('declared', 'promoted_observation')
_VERIFICATIONS = ('pending_verification', 'verified')
_LIFECYCLES = ('current', 'in_progress', 'retired')
_FACT_KEYS_BEFORE = (
    'telecom_operator_cnmc', 'own_number_range_nrn', 'datacenter_tier3',
    'own_ftth_infrastructure', 'support_24x7',
)
_NUMERIC_FACT_KEYS = ('ipv4_assignable_count',)
_FACT_KEYS = (*_FACT_KEYS_BEFORE, *_NUMERIC_FACT_KEYS)
_EQUIPMENT_TYPES = (
    'otdr', 'olts', 'fusion_splicer', 'optical_power_meter', 'light_source',
    'cable_certifier', 'spectrum_analyzer', 'other',
)
_LIMIT_KINDS_BEFORE = (
    'total', 'per_claim', 'per_victim', 'annual_aggregate', 'sublimit', 'unknown',
)
_LIMIT_KINDS = (*_LIMIT_KINDS_BEFORE, 'employer_per_victim')
_ACCESS_MODES = ('owned', 'leased', 'rented', 'partner_provided', 'other')
#: Taules del perfil que passen a ser coneixement actual, amb origen.
_QUALIFIED_TABLES = (
    'company_financial_years', 'company_projects', 'company_insurances',
    'company_registrations', 'company_people',
)
_ORIGIN_DEFAULTS = (
    "knowledge_origin <> 'declared' OR verification_status <> 'pending_verification' "
    "OR lifecycle <> 'current' OR as_of IS NOT NULL"
)

#: Files que 0051 no pot representar fidelment: (taula, condicio, que vol dir).
INCOMPATIBLE_WITH_0051 = (
    *(
        (table, _ORIGIN_DEFAULTS, f'files de {table} amb origen, verificacio, cicle de vida '
                                  'o data que 0051 no pot guardar')
        for table in _QUALIFIED_TABLES
    ),
    ('company_registrations',
     "scope_service_lines IS NOT NULL OR type = 'sector_operator_registry'",
     'registres amb abast estructurat o de tipus sector_operator_registry'),
    ('company_people', 'profile_codes IS NOT NULL', 'persones amb perfils estructurats'),
    ('company_operating_facts', "value_number IS NOT NULL OR key = 'ipv4_assignable_count'",
     'fets operatius quantitatius'),
    ('company_insurance_limits', "kind = 'employer_per_victim'",
     'limits de RC patronal per victima'),
    ('company_project_annual_amounts', 'TRUE', 'imports executats per any'),
    ('company_equipment', 'TRUE', 'equips o instruments declarats'),
)
DOWNGRADE_BLOCKED = 'downgrade_0052_incompatible_rows'


class DowngradeBlockedError(RuntimeError):
    """El downgrade s'atura perque hi ha files que 0051 no pot guardar."""

    code = DOWNGRADE_BLOCKED


def incompatible_rows(connection) -> dict[str, int]:
    """Quantes files hi ha de cada cas. Nomes llegeix."""
    return {
        meaning: connection.execute(
            sa.text(f'SELECT count(*) FROM {table} WHERE {condition}')
        ).scalar()
        for table, condition, meaning in INCOMPATIBLE_WITH_0051
    }


def refuse_unless_representable_in_0051(connection) -> None:
    """La precondicio del downgrade: cap fila incompatible, o cap canvi."""
    found = {meaning: n for meaning, n in incompatible_rows(connection).items() if n}
    if found:
        detail = '; '.join(f'{n} {meaning}' for meaning, n in found.items())
        raise DowngradeBlockedError(
            f'{DOWNGRADE_BLOCKED}: {detail}. 0051 no ho pot representar i el downgrade no '
            'reinterpreta ni esborra res: resoleu aquestes files a ma abans de tornar enrere.'
        )


def _offline_guard() -> str:
    """La mateixa precondicio, com a SQL de PostgreSQL per a la revisio offline."""
    checks = '\n'.join(
        f"  SELECT count(*) INTO n FROM {table} WHERE {condition};\n"
        f"  IF n > 0 THEN RAISE EXCEPTION '{DOWNGRADE_BLOCKED} - % {meaning}', n; END IF;"
        for table, condition, meaning in INCOMPATIBLE_WITH_0051
    )
    return f'DO $$\nDECLARE n integer;\nBEGIN\n{checks}\nEND $$'


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _origin_columns(*, as_of_required: bool) -> tuple[sa.Column, ...]:
    as_of = (
        sa.Column('as_of', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False)
        if as_of_required
        else sa.Column('as_of', sa.Date(), nullable=True)
    )
    return (
        sa.Column('knowledge_origin', _enum(_ORIGINS, 'knowledge_origin'),
                  server_default='declared', nullable=False),
        as_of,
        sa.Column('verification_status', _enum(_VERIFICATIONS, 'verification_status'),
                  server_default='pending_verification', nullable=False),
        sa.Column('lifecycle', _enum(_LIFECYCLES, 'knowledge_lifecycle'),
                  server_default='current', nullable=False),
    )


def _origin_checks(table: str) -> tuple[tuple[str, str], ...]:
    return (
        (f'ck_{table}_knowledge_origin_valid', _in_list('knowledge_origin', _ORIGINS)),
        (f'ck_{table}_verification_valid', _in_list('verification_status', _VERIFICATIONS)),
        (f'ck_{table}_lifecycle_valid', _in_list('lifecycle', _LIFECYCLES)),
    )


def upgrade() -> None:
    # 1. Origen del coneixement a les cinc taules del perfil. Les files existents
    #    queden declarades, vigents, pendents i sense `as_of` explicit. Cap UPDATE.
    for table in _QUALIFIED_TABLES:
        for column in _origin_columns(as_of_required=False):
            op.add_column(table, column)
        for name, condition in _origin_checks(table):
            op.create_check_constraint(op.f(name), table, condition)

    # 2. Abast estructurat dels registres i perfils estructurats de les persones.
    op.add_column('company_registrations',
                  sa.Column('scope_service_lines', _json(), nullable=True))
    op.add_column('company_people', sa.Column('profile_codes', _json(), nullable=True))

    # 3. Fets quantitatius: `value_number` nomes a les claus numeriques, mai negatiu.
    op.add_column('company_operating_facts',
                  sa.Column('value_number', sa.Numeric(precision=18, scale=4), nullable=True))
    op.drop_constraint(op.f('ck_company_operating_facts_key_valid'), 'company_operating_facts',
                       type_='check')
    op.create_check_constraint(op.f('ck_company_operating_facts_key_valid'),
                               'company_operating_facts', _in_list('key', _FACT_KEYS))
    op.create_check_constraint(op.f('ck_company_operating_facts_value_number_non_negative'),
                               'company_operating_facts',
                               'value_number IS NULL OR value_number >= 0')
    op.create_check_constraint(op.f('ck_company_operating_facts_value_number_numeric_key'),
                               'company_operating_facts',
                               f"value_number IS NULL OR {_in_list('key', _NUMERIC_FACT_KEYS)}")

    # 4. Limit de RC patronal per victima: la columna s'eixampla; cap fila canvia.
    op.alter_column('company_insurance_limits', 'kind',
                    existing_type=_enum(_LIMIT_KINDS_BEFORE, 'insurance_limit_kind'),
                    type_=_enum(_LIMIT_KINDS, 'insurance_limit_kind'), existing_nullable=False)

    # 5. Imports executats per any d'un projecte.
    op.create_table(
        'company_project_annual_amounts',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.CheckConstraint('amount >= 0', name='annual_amount_not_negative'),
        sa.CheckConstraint('year >= 1900 AND year <= 2200', name='annual_amount_year_range'),
        sa.ForeignKeyConstraint(['project_id'], ['company_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'year', name='uq_company_project_annual_amounts_year'),
    )
    op.create_index(op.f('ix_company_project_annual_amounts_project_id'),
                    'company_project_annual_amounts', ['project_id'])
    op.create_index(op.f('ix_company_project_annual_amounts_company_id'),
                    'company_project_annual_amounts', ['company_id'])

    # 6. Equips i instruments.
    op.create_table(
        'company_equipment',
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('equipment_type', _enum(_EQUIPMENT_TYPES, 'equipment_type'), nullable=False),
        sa.Column('manufacturer', sa.String(length=160), nullable=True),
        sa.Column('model', sa.String(length=160), nullable=True),
        sa.Column('serial_reference', sa.String(length=120), nullable=True),
        sa.Column('access_mode', _enum(_ACCESS_MODES, 'equipment_access_mode'), nullable=True),
        sa.Column('quantity', sa.Integer(), server_default='1', nullable=False),
        sa.Column('calibration_required', sa.Boolean(), nullable=True),
        sa.Column('calibration_valid_until', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_origin_columns(as_of_required=True),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.CheckConstraint(_in_list('knowledge_origin', _ORIGINS), name='knowledge_origin_valid'),
        sa.CheckConstraint(_in_list('verification_status', _VERIFICATIONS),
                           name='verification_valid'),
        sa.CheckConstraint(_in_list('lifecycle', _LIFECYCLES), name='lifecycle_valid'),
        sa.CheckConstraint('valid_until IS NULL OR valid_until >= as_of',
                           name='valid_until_after_as_of'),
        sa.CheckConstraint(_in_list('equipment_type', _EQUIPMENT_TYPES),
                           name='equipment_type_valid'),
        sa.CheckConstraint('access_mode IS NULL OR ' + _in_list('access_mode', _ACCESS_MODES),
                           name='access_mode_valid'),
        sa.CheckConstraint('quantity >= 1', name='quantity_positive'),
        sa.CheckConstraint('calibration_valid_until IS NULL OR calibration_required IS TRUE',
                           name='calibration_date_needs_requirement'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'serial_reference', name='uq_company_equipment_serial'),
    )


def downgrade() -> None:
    # Primer la precondicio; si falla, no s'ha tocat res.
    if context.is_offline_mode():
        op.execute(_offline_guard())
    else:
        refuse_unless_representable_in_0051(op.get_bind())

    op.drop_table('company_equipment')
    op.drop_index(op.f('ix_company_project_annual_amounts_company_id'),
                  table_name='company_project_annual_amounts')
    op.drop_index(op.f('ix_company_project_annual_amounts_project_id'),
                  table_name='company_project_annual_amounts')
    op.drop_table('company_project_annual_amounts')

    op.alter_column('company_insurance_limits', 'kind',
                    existing_type=_enum(_LIMIT_KINDS, 'insurance_limit_kind'),
                    type_=_enum(_LIMIT_KINDS_BEFORE, 'insurance_limit_kind'),
                    existing_nullable=False)

    op.drop_constraint(op.f('ck_company_operating_facts_value_number_numeric_key'),
                       'company_operating_facts', type_='check')
    op.drop_constraint(op.f('ck_company_operating_facts_value_number_non_negative'),
                       'company_operating_facts', type_='check')
    op.drop_constraint(op.f('ck_company_operating_facts_key_valid'), 'company_operating_facts',
                       type_='check')
    op.create_check_constraint(op.f('ck_company_operating_facts_key_valid'),
                               'company_operating_facts', _in_list('key', _FACT_KEYS_BEFORE))
    op.drop_column('company_operating_facts', 'value_number')

    op.drop_column('company_people', 'profile_codes')
    op.drop_column('company_registrations', 'scope_service_lines')

    for table in reversed(_QUALIFIED_TABLES):
        for name, _ in reversed(_origin_checks(table)):
            op.drop_constraint(op.f(name), table, type_='check')
        for column in reversed(_origin_columns(as_of_required=False)):
            op.drop_column(table, column.name)
