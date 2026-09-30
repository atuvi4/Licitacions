"""Coneixement de l'empresa (fase 2 del motor d'analisi).

**Vocabularis globals** (sense `company_id`), omplerts aqui mateix:
`service_lines`, `capability_definitions` i `certification_standards`. Son una
copia de `app/knowledge/vocabulary.py`; `test_knowledge_vocabulary` comprova que
coincideixen. Cap API no hi escriu.

**Estat actual** (tenant): `company_operating_facts`, `company_solutions`,
`solution_capabilities`, `company_products`, `solution_products`,
`company_partners` i `partner_roles`, totes amb procedencia
(`knowledge_origin`, `as_of`, `valid_until`, `verification_status`,
`lifecycle`). Les filles referencien el pare per `(id, company_id)`.

**Taules existents que s'amplien**, en lloc de duplicar-les:

* `company_services`: `service_line_code`, `delivery_mode` i procedencia. El
  cicle de vida es el `active` que ja hi era.
* `company_certifications`: `standard_code`, `level`, `scope_service_lines`
  (abast estructurat; el text de `scope` es queda com a nota),
  `knowledge_origin`, `as_of` i `lifecycle`. La verificacio i la vigencia son les que ja hi eren
  (`verification_status`, `expires_at`).

Les files que ja existien queden `declared`, `current`, pendents de verificar
(serveis) i amb `as_of` = el dia que es van crear.

**Historic** (tenant): `historic_bids` (sense oferta, baixa ni marge) i
`knowledge_observations` (suggeriments; nomes una persona els promou).

**Downgrade.** Treu les taules noves i les columnes afegides. Es perd el
coneixement actual nou i l'historic importat; les certificacions i els serveis
es conserven amb les dades que tenien abans.

Revision ID: 0045_company_knowledge
Revises: 0044_reception_classification
Create Date: 2026-09-30
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0045_company_knowledge'
down_revision: str | None = '0044_reception_classification'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_SERVICE_LINES = (
    ('voice_pbx', 'Centraleta i veu IP'),
    ('voice_trunk', 'Troncals de veu i numeracio'),
    ('access_ftth', 'Accessos FTTH'),
    ('dedicated_network', 'Xarxa dedicada entre seus'),
    ('radio_link', 'Radioenllacos'),
    ('network_switching', 'Electronica de xarxa'),
    ('fiber_deployment', 'Desplegament i certificacio de fibra'),
    ('civil_works', 'Obra civil'),
    ('support_ops', 'Suport, operacio i nivells de servei'),
)
# (codi, linia, etiqueta, tipus, comparador, unitat, escala)
_CAPABILITIES = (
    ('pbx.concurrent_calls', 'voice_pbx', 'Trucades simultanies', 'number', 'gte', 'canals',
     None),
    ('pbx.ivr_multilevel', 'voice_pbx', 'IVR multinivell', 'boolean', 'is_true', None, None),
    ('pbx.queues', 'voice_pbx', 'Cues de trucades', 'boolean', 'is_true', None, None),
    ('pbx.blf', 'voice_pbx', 'Estat de linia (BLF)', 'boolean', 'is_true', None, None),
    ('pbx.call_park', 'voice_pbx', 'Aparcament de trucades', 'boolean', 'is_true', None, None),
    ('pbx.voicemail_email', 'voice_pbx', 'Bustia amb avis per correu', 'boolean', 'is_true',
     None, None),
    ('pbx.cdr_export', 'voice_pbx', 'CDR exportable', 'boolean', 'is_true', None, None),
    ('pbx.recording_policy', 'voice_pbx', 'Gravacio configurable per politica', 'boolean',
     'is_true', None, None),
    ('pbx.tls_srtp', 'voice_pbx', 'Xifratge TLS i SRTP', 'boolean', 'is_true', None, None),
    ('pbx.fraud_block', 'voice_pbx', 'Bloqueig contra frau', 'boolean', 'is_true', None, None),
    ('pbx.outbound_number_select', 'voice_pbx', 'Numeros de sortida seleccionables per usuari',
     'number', 'gte', 'numeros', None),
    ('pbx.extra_softphones_free', 'voice_pbx', 'Softphones addicionals sense cost', 'number',
     'gte', 'llicencies', None),
    ('pbx.queue_callback_keep_position', 'voice_pbx',
     'Retorn de trucada conservant la posicio a la cua', 'boolean', 'is_true', None, None),
    ('pbx.hot_desking', 'voice_pbx', 'Hot desking', 'boolean', 'is_true', None, None),
    ('switch.poe_standard', 'network_switching', 'Estandard PoE', 'ordinal', 'gte', None,
     ['af', 'at', 'bt']),
    ('switch.mlag', 'network_switching', 'Agregacio redundant (MLAG o equivalent)', 'boolean',
     'is_true', None, None),
    ('fiber.otdr_level', 'fiber_deployment',
     'Nivell de certificacio de fibra (1 = OLTS, 2 = OTDR)', 'ordinal', 'gte', None, ['1', '2']),
    ('ops.sla_response_minutes', 'support_ops', 'Temps de resposta', 'number', 'lte', 'minuts',
     None),
    ('ops.sla_restore_hours', 'support_ops', 'Temps de restabliment', 'number', 'lte', 'hores',
     None),
)
_STANDARDS = (
    ('ENS', 'Esquema Nacional de Seguretat', ['basic', 'medium', 'high']),
    ('ISO_9001', 'ISO 9001 (gestio de la qualitat)', None),
    ('ISO_14001', 'ISO 14001 (gestio ambiental)', None),
    ('ISO_27001', 'ISO/IEC 27001 (seguretat de la informacio)', None),
)

_VALUE_TYPES = ('boolean', 'number', 'ordinal')
_COMPARATORS = ('is_true', 'gte', 'lte')
_ORIGINS = ('declared', 'promoted_observation')
_VERIFICATION = ('pending_verification', 'verified')
_LIFECYCLES = ('current', 'in_progress', 'retired')
_FACT_KEYS = (
    'telecom_operator_cnmc', 'own_number_range_nrn', 'datacenter_tier3',
    'own_ftth_infrastructure', 'support_24x7',
)
_CAPABILITY_STATUS = ('supported', 'supported_with_limits', 'not_supported')
_DELIVERY_MODES = ('own', 'partner', 'subcontract', 'resale')
_PARTNER_ROLES = (
    'supplier', 'wholesale_operator', 'subcontractor', 'commercial_partner', 'bid_writer',
    'advisor',
)
_DECISIONS = ('submitted', 'prepared', 'not_submitted', 'undecided')
_RESULTS = ('won', 'lost', 'unknown')
_SUBJECTS = ('operating_fact', 'certification', 'service', 'solution', 'product', 'partner')
_OBSERVATION_STATUS = ('candidate', 'promoted', 'dismissed')

_NEW_CURRENT_TABLES = (
    'company_operating_facts', 'company_solutions', 'solution_capabilities',
    'company_products', 'solution_products', 'company_partners', 'partner_roles',
)


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _literal(value: object) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, list):
        value = json.dumps(value)
    return "'" + str(value).replace("'", "''") + "'"


def _insert(table: str, **values: object) -> None:
    columns = ', '.join(values)
    literals = ', '.join(_literal(v) for v in values.values())
    op.execute(f'INSERT INTO {table} ({columns}) VALUES ({literals})')


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _id() -> list:
    return [sa.Column('id', sa.Uuid(), nullable=False), sa.PrimaryKeyConstraint('id')]


def _company() -> list:
    return [
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    ]


def _provenance() -> list:
    return [
        sa.Column('knowledge_origin', _enum(_ORIGINS, 'knowledge_origin'),
                  server_default='declared', nullable=False),
        sa.Column('as_of', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('verification_status', _enum(_VERIFICATION, 'verification_status'),
                  server_default='pending_verification', nullable=False),
        sa.Column('lifecycle', _enum(_LIFECYCLES, 'knowledge_lifecycle'),
                  server_default='current', nullable=False),
        sa.CheckConstraint(_in_list('knowledge_origin', _ORIGINS), name='knowledge_origin_valid'),
        sa.CheckConstraint(_in_list('verification_status', _VERIFICATION),
                           name='verification_valid'),
        sa.CheckConstraint(_in_list('lifecycle', _LIFECYCLES), name='lifecycle_valid'),
        sa.CheckConstraint('valid_until IS NULL OR valid_until >= as_of',
                           name='valid_until_after_as_of'),
    ]


def _service_line(nullable: bool) -> list:
    return [
        sa.Column('service_line_code', sa.String(length=40), nullable=nullable),
        sa.ForeignKeyConstraint(['service_line_code'], ['service_lines.code'],
                                ondelete='RESTRICT'),
    ]


def upgrade() -> None:
    # --- Vocabularis globals --------------------------------------------------
    op.create_table(
        'service_lines',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=160), nullable=False),
        sa.PrimaryKeyConstraint('code'),
    )
    op.create_table(
        'capability_definitions',
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('service_line_code', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('value_type', _enum(_VALUE_TYPES, 'capability_value_type'), nullable=False),
        sa.Column('comparator', _enum(_COMPARATORS, 'capability_comparator'), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('ordinal_scale', _json(), nullable=True),
        sa.PrimaryKeyConstraint('code'),
        sa.CheckConstraint(_in_list('value_type', _VALUE_TYPES), name='value_type_valid'),
        sa.CheckConstraint(_in_list('comparator', _COMPARATORS), name='comparator_valid'),
        sa.ForeignKeyConstraint(['service_line_code'], ['service_lines.code'],
                                ondelete='RESTRICT'),
    )
    op.create_table(
        'certification_standards',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('levels', _json(), nullable=True),
        sa.PrimaryKeyConstraint('code'),
    )
    # Literals i no `bulk_insert`: el SQL offline no sap escriure un JSON parametritzat,
    # i un literal de text es converteix sol a JSON/JSONB en inserir-lo.
    for code, label in _SERVICE_LINES:
        _insert('service_lines', code=code, label=label)
    for code, line, label, kind, comparator, unit, scale in _CAPABILITIES:
        _insert('capability_definitions', code=code, service_line_code=line, label=label,
                value_type=kind, comparator=comparator, unit=unit, ordinal_scale=scale)
    for code, label, levels in _STANDARDS:
        _insert('certification_standards', code=code, label=label, levels=levels)

    # --- Estat actual: taules noves ---------------------------------------------
    op.create_table(
        'company_operating_facts',
        *_company(),
        sa.Column('key', _enum(_FACT_KEYS, 'operating_fact_key'), nullable=False),
        sa.Column('value', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.String(length=500), nullable=True),
        *_provenance(),
        *_id(),
        sa.CheckConstraint(_in_list('key', _FACT_KEYS), name='key_valid'),
        sa.UniqueConstraint('company_id', 'key', name='uq_company_operating_facts_key'),
    )
    op.create_table(
        'company_solutions',
        *_company(),
        *_service_line(nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('vendor', sa.String(length=160), nullable=True),
        sa.Column('edition', sa.String(length=80), nullable=True),
        *_provenance(),
        *_id(),
        sa.UniqueConstraint('company_id', 'name', name='uq_company_solutions_name'),
        sa.UniqueConstraint('id', 'company_id', name='uq_company_solutions_id_company'),
    )
    op.create_table(
        'solution_capabilities',
        *_company(),
        sa.Column('solution_id', sa.Uuid(), nullable=False),
        sa.Column('capability_code', sa.String(length=80), nullable=False),
        sa.Column('status', _enum(_CAPABILITY_STATUS, 'capability_status'), nullable=False),
        sa.Column('value_number', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('value_level', sa.String(length=20), nullable=True),
        sa.Column('limits_note', sa.Text(), nullable=True),
        *_provenance(),
        *_id(),
        sa.CheckConstraint(_in_list('status', _CAPABILITY_STATUS), name='status_valid'),
        sa.UniqueConstraint('solution_id', 'capability_code',
                            name='uq_solution_capabilities_capability'),
        sa.ForeignKeyConstraint(['solution_id', 'company_id'],
                                ['company_solutions.id', 'company_solutions.company_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['capability_code'], ['capability_definitions.code'],
                                ondelete='RESTRICT'),
    )
    op.create_table(
        'company_products',
        *_company(),
        sa.Column('manufacturer', sa.String(length=120), nullable=False),
        sa.Column('model', sa.String(length=160), nullable=False),
        *_service_line(nullable=True),
        *_provenance(),
        *_id(),
        sa.UniqueConstraint('company_id', 'manufacturer', 'model',
                            name='uq_company_products_model'),
        sa.UniqueConstraint('id', 'company_id', name='uq_company_products_id_company'),
    )
    op.create_table(
        'solution_products',
        *_company(),
        sa.Column('solution_id', sa.Uuid(), nullable=False),
        sa.Column('product_id', sa.Uuid(), nullable=False),
        *_provenance(),
        *_id(),
        sa.UniqueConstraint('solution_id', 'product_id', name='uq_solution_products_pair'),
        sa.ForeignKeyConstraint(['solution_id', 'company_id'],
                                ['company_solutions.id', 'company_solutions.company_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id', 'company_id'],
                                ['company_products.id', 'company_products.company_id'],
                                ondelete='CASCADE'),
    )
    op.create_table(
        'company_partners',
        *_company(),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('tax_id', sa.String(length=32), nullable=True),
        *_provenance(),
        *_id(),
        sa.UniqueConstraint('company_id', 'name', name='uq_company_partners_name'),
        sa.UniqueConstraint('id', 'company_id', name='uq_company_partners_id_company'),
    )
    op.create_table(
        'partner_roles',
        *_company(),
        sa.Column('partner_id', sa.Uuid(), nullable=False),
        sa.Column('role', _enum(_PARTNER_ROLES, 'partner_role'), nullable=False),
        *_service_line(nullable=True),
        sa.Column('territory', sa.String(length=160), nullable=True),
        *_provenance(),
        *_id(),
        sa.CheckConstraint(_in_list('role', _PARTNER_ROLES), name='role_valid'),
        sa.ForeignKeyConstraint(['partner_id', 'company_id'],
                                ['company_partners.id', 'company_partners.company_id'],
                                ondelete='CASCADE'),
    )

    # --- Estat actual: taules existents que s'amplien ----------------------------
    op.add_column('company_services', sa.Column('service_line_code', sa.String(length=40),
                                                nullable=True))
    op.add_column('company_services', sa.Column(
        'delivery_mode', _enum(_DELIVERY_MODES, 'delivery_mode'), nullable=True))
    op.add_column('company_services', sa.Column(
        'knowledge_origin', _enum(_ORIGINS, 'knowledge_origin'), server_default='declared',
        nullable=False))
    op.add_column('company_services', sa.Column(
        'as_of', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False))
    op.add_column('company_services', sa.Column('valid_until', sa.Date(), nullable=True))
    op.add_column('company_services', sa.Column(
        'verification_status', _enum(_VERIFICATION, 'verification_status'),
        server_default='pending_verification', nullable=False))
    op.create_foreign_key(op.f('fk_company_services_service_line_code_service_lines'),
                          'company_services', 'service_lines', ['service_line_code'], ['code'],
                          ondelete='RESTRICT')
    op.create_check_constraint(
        op.f('ck_company_services_delivery_mode_valid'), 'company_services',
        'delivery_mode IS NULL OR ' + _in_list('delivery_mode', _DELIVERY_MODES))
    op.create_check_constraint(
        op.f('ck_company_services_knowledge_origin_valid'), 'company_services',
        _in_list('knowledge_origin', _ORIGINS))
    op.create_check_constraint(
        op.f('ck_company_services_verification_valid'), 'company_services',
        _in_list('verification_status', _VERIFICATION))
    op.create_check_constraint(
        op.f('ck_company_services_valid_until_after_as_of'), 'company_services',
        'valid_until IS NULL OR valid_until >= as_of')
    op.execute('UPDATE company_services SET as_of = CAST(created_at AS DATE)')

    op.add_column('company_certifications', sa.Column('standard_code', sa.String(length=40),
                                                      nullable=True))
    op.add_column('company_certifications', sa.Column('level', sa.String(length=20),
                                                      nullable=True))
    op.add_column('company_certifications', sa.Column('scope_service_lines', _json(),
                                                      nullable=True))
    op.add_column('company_certifications', sa.Column(
        'knowledge_origin', _enum(_ORIGINS, 'knowledge_origin'), server_default='declared',
        nullable=False))
    op.add_column('company_certifications', sa.Column(
        'as_of', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False))
    op.add_column('company_certifications', sa.Column(
        'lifecycle', _enum(_LIFECYCLES, 'knowledge_lifecycle'), server_default='current',
        nullable=False))
    op.create_foreign_key(op.f('fk_company_certifications_standard_code'),
                          'company_certifications', 'certification_standards',
                          ['standard_code'], ['code'], ondelete='RESTRICT')
    op.create_check_constraint(
        op.f('ck_company_certifications_knowledge_origin_valid'), 'company_certifications',
        _in_list('knowledge_origin', _ORIGINS))
    op.create_check_constraint(
        op.f('ck_company_certifications_lifecycle_valid'), 'company_certifications',
        _in_list('lifecycle', _LIFECYCLES))
    op.create_check_constraint(
        op.f('ck_company_certifications_level_needs_standard'), 'company_certifications',
        'level IS NULL OR standard_code IS NOT NULL')
    op.execute('UPDATE company_certifications SET as_of = CAST(created_at AS DATE)')

    # --- Historic ------------------------------------------------------------------
    op.create_table(
        'historic_bids',
        *_company(),
        sa.Column('reference', sa.String(length=300), nullable=False),
        sa.Column('buyer_name', sa.String(length=300), nullable=True),
        sa.Column('bid_date', sa.Date(), nullable=True),
        sa.Column('service_line_codes', _json(), nullable=True),
        sa.Column('base_budget', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('decision', _enum(_DECISIONS, 'historic_bid_decision'), nullable=False),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.Column('result', _enum(_RESULTS, 'historic_bid_result'), nullable=False),
        sa.Column('source_ref', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        *_id(),
        sa.CheckConstraint(_in_list('decision', _DECISIONS), name='decision_valid'),
        sa.CheckConstraint(_in_list('result', _RESULTS), name='result_valid'),
        sa.UniqueConstraint('company_id', 'reference', name='uq_historic_bids_reference'),
    )
    op.create_table(
        'knowledge_observations',
        *_company(),
        sa.Column('historic_bid_id', sa.Uuid(), nullable=True),
        sa.Column('subject', _enum(_SUBJECTS, 'observation_subject'), nullable=False),
        sa.Column('subject_key', sa.String(length=160), nullable=False),
        sa.Column('statement', sa.Text(), nullable=False),
        sa.Column('proposal', _json(), nullable=False),
        sa.Column('observed_on', sa.Date(), nullable=True),
        sa.Column('source_ref', sa.String(length=500), nullable=True),
        sa.Column('status', _enum(_OBSERVATION_STATUS, 'observation_status'), nullable=False),
        sa.Column('reviewed_by', sa.Uuid(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('promoted_entity_type', sa.String(length=40), nullable=True),
        sa.Column('promoted_entity_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        *_id(),
        sa.CheckConstraint(_in_list('subject', _SUBJECTS), name='subject_valid'),
        sa.CheckConstraint(_in_list('status', _OBSERVATION_STATUS), name='status_valid'),
        sa.CheckConstraint("status <> 'promoted' OR promoted_entity_id IS NOT NULL",
                           name='promoted_has_target'),
        sa.CheckConstraint("status = 'candidate' OR reviewed_at IS NOT NULL",
                           name='reviewed_has_date'),
        sa.ForeignKeyConstraint(['historic_bid_id'], ['historic_bids.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_knowledge_observations_scope', 'knowledge_observations',
                    ['company_id', 'subject', 'status'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_observations_scope', table_name='knowledge_observations')
    op.drop_table('knowledge_observations')
    op.drop_table('historic_bids')

    for name in ('level_needs_standard', 'lifecycle_valid', 'knowledge_origin_valid'):
        op.drop_constraint(op.f(f'ck_company_certifications_{name}'), 'company_certifications',
                           type_='check')
    op.drop_constraint(op.f('fk_company_certifications_standard_code'),
                       'company_certifications', type_='foreignkey')
    for column in ('lifecycle', 'as_of', 'knowledge_origin', 'scope_service_lines', 'level',
                   'standard_code'):
        op.drop_column('company_certifications', column)

    for name in ('valid_until_after_as_of', 'verification_valid', 'knowledge_origin_valid',
                 'delivery_mode_valid'):
        op.drop_constraint(op.f(f'ck_company_services_{name}'), 'company_services', type_='check')
    op.drop_constraint(op.f('fk_company_services_service_line_code_service_lines'),
                       'company_services', type_='foreignkey')
    for column in ('verification_status', 'valid_until', 'as_of', 'knowledge_origin',
                   'delivery_mode', 'service_line_code'):
        op.drop_column('company_services', column)

    for table in reversed(_NEW_CURRENT_TABLES):
        op.drop_table(table)
    op.drop_table('certification_standards')
    op.drop_table('capability_definitions')
    op.drop_table('service_lines')
