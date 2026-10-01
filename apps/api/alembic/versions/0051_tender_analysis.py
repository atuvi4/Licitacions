"""Analisi d'un plec contra el coneixement actual d'una empresa (fase 3A, pas 4).

Set taules, totes DERIVADES: `tender_analysis_runs` (una per execucio: empresa,
licitacio i les versions de tot el que ha llegit, amb `inputs_hash`) i els seus
resultats (`tender_fit_assessments`, `capability_need_assessments`,
`criterion_assessments`, `delivery_candidates`, `analysis_findings`,
`analysis_questions`), que pengen de la run amb CASCADE.

**Neutralitat.** Cap INSERT, UPDATE ni DELETE: cap run es crea en migrar, cap
backfill. L'analisi nomes s'executa a peticio i nomes escriu aqui.

**Downgrade: diferent de 0050, i a proposit.** 0050 guardava dades font (el que
diu el plec) i per aixo el seu downgrade refusa perdre-les. 0051 guarda dades
DERIVADES, reproduibles a partir de l'estructura del plec, de la fotografia del
coneixement i de la versio de regles que cada run fixa: tornar enrere les
elimina, i tornar endavant les torna a calcular. No hi ha cap font de veritat
en aquestes taules.

Revision ID: 0051_tender_analysis
Revises: 0050_tender_semantics
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0051_tender_analysis'
down_revision: str | None = '0050_tender_semantics'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_KNOWLEDGE_SOURCES = (
    'current_verified', 'current_declared', 'historical', 'none', 'mixed', 'not_applicable',
)
_PROOF_STATUSES = ('sufficient', 'partial', 'missing', 'unknown', 'not_required')
_FIT_RESULTS = ('satisfies', 'does_not_satisfy', 'unknown', 'not_applicable')
_TERRITORY_VERDICTS = ('matched', 'unmatched', 'unknown', 'not_applicable')
_CRITERION_RESULTS = ('achievable', 'not_achievable', 'needs_review', 'not_assessable')
_POINTS_STATUSES = ('secured', 'at_risk', 'lost', 'mixed', 'not_assessable')
_VERIFICATIONS = ('verified', 'not_verified', 'unknown', 'not_applicable')
_CANDIDATE_TYPES = ('own_current', 'partner_current', 'subcontract_unknown', 'own_future')
_CANDIDATE_ORIGINS = ('current_knowledge', 'hypothesis')
_READINESS = (
    'available', 'pending_coverage', 'pending_partner', 'pending_validation', 'unavailable',
)
_FINDING_TYPES = (
    'blocker_potential', 'review_required', 'missing_information', 'evidence_gap',
    'territory_gap', 'delivery_gap', 'warning',
)
_AUDIENCES = ('buyer', 'company')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _id() -> sa.Column:
    return sa.Column('id', sa.Uuid(), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                     nullable=False)


def _run_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(['run_id'], ['tender_analysis_runs.id'], ondelete='CASCADE')


def _codes() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column('rule_code', sa.String(length=80), nullable=False),
        sa.Column('explanation_code', sa.String(length=80), nullable=False),
    )


def upgrade() -> None:
    # 1. La run: immutable, amb les versions de tot el que llegeix.
    op.create_table(
        'tender_analysis_runs',
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('tender_revision_id', sa.Uuid(), nullable=True),
        sa.Column('extraction_run_id', sa.Uuid(), nullable=True),
        sa.Column('classification_run_id', sa.Uuid(), nullable=True),
        sa.Column('review_revision_hash', sa.String(length=64), nullable=True),
        sa.Column('tender_structure_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('knowledge_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('rules_version', sa.String(length=40), nullable=False),
        sa.Column('inputs_hash', sa.String(length=64), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        _created_at(),
        _id(),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_revision_id'], ['tender_revisions.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['extraction_run_id'], ['requirement_extraction_runs.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['classification_run_id'],
                                ['tender_document_classification_runs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'tender_id', 'inputs_hash',
                            name='uq_tender_analysis_runs_inputs'),
    )
    op.create_index('uq_tender_analysis_runs_current', 'tender_analysis_runs',
                    ['company_id', 'tender_id'], unique=True,
                    postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.create_index('ix_tender_analysis_runs_scope', 'tender_analysis_runs',
                    ['company_id', 'tender_id', 'created_at'])

    # 2. Una fila per requisit.
    op.create_table(
        'tender_fit_assessments',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('fit_result', _enum(_FIT_RESULTS, 'fit_result'), nullable=False),
        sa.Column('knowledge_source', _enum(_KNOWLEDGE_SOURCES, 'analysis_knowledge_source'),
                  nullable=False),
        sa.Column('proof_status', _enum(_PROOF_STATUSES, 'proof_status'), nullable=False),
        sa.Column('territory_verdict', _enum(_TERRITORY_VERDICTS, 'territory_verdict'),
                  nullable=False),
        sa.Column('verification_status', _enum(_VERIFICATIONS, 'assessment_verification'),
                  nullable=False),
        *_codes(),
        sa.Column('support_json', sa.JSON(), nullable=True),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('fit_result', _FIT_RESULTS), name='fit_result_valid'),
        sa.CheckConstraint(_in_list('knowledge_source', _KNOWLEDGE_SOURCES),
                           name='knowledge_source_valid'),
        sa.CheckConstraint(_in_list('proof_status', _PROOF_STATUSES), name='proof_status_valid'),
        sa.CheckConstraint(_in_list('territory_verdict', _TERRITORY_VERDICTS),
                           name='territory_verdict_valid'),
        sa.CheckConstraint(_in_list('verification_status', _VERIFICATIONS),
                           name='verification_status_valid'),
        _run_fk(),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'requirement_id', name='uq_tender_fit_assessments_req'),
    )
    op.create_index('ix_tender_fit_assessments_requirement', 'tender_fit_assessments',
                    ['requirement_id'])

    # 3. Una fila per necessitat tecnica.
    op.create_table(
        'capability_need_assessments',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('capability_need_id', sa.Uuid(), nullable=False),
        sa.Column('fit_result', _enum(_FIT_RESULTS, 'fit_result'), nullable=False),
        sa.Column('knowledge_source', _enum(_KNOWLEDGE_SOURCES, 'analysis_knowledge_source'),
                  nullable=False),
        sa.Column('verification_status', _enum(_VERIFICATIONS, 'assessment_verification'),
                  nullable=False),
        *_codes(),
        sa.Column('support_json', sa.JSON(), nullable=True),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('fit_result', _FIT_RESULTS), name='fit_result_valid'),
        sa.CheckConstraint(_in_list('knowledge_source', _KNOWLEDGE_SOURCES),
                           name='knowledge_source_valid'),
        sa.CheckConstraint(_in_list('verification_status', _VERIFICATIONS),
                           name='verification_status_valid'),
        _run_fk(),
        sa.ForeignKeyConstraint(['capability_need_id'], ['requirement_capability_needs.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'capability_need_id',
                            name='uq_capability_need_assess_need'),
    )
    op.create_index('ix_capability_need_assessments_need', 'capability_need_assessments',
                    ['capability_need_id'])

    # 4. Una fila per criteri, amb els punts en quatre cubells.
    op.create_table(
        'criterion_assessments',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('award_criterion_id', sa.Uuid(), nullable=False),
        sa.Column('criterion_result', _enum(_CRITERION_RESULTS, 'criterion_result'),
                  nullable=False),
        sa.Column('knowledge_source', _enum(_KNOWLEDGE_SOURCES, 'analysis_knowledge_source'),
                  nullable=False),
        sa.Column('proof_status', _enum(_PROOF_STATUSES, 'proof_status'), nullable=False),
        sa.Column('points_status', _enum(_POINTS_STATUSES, 'points_status'), nullable=False),
        sa.Column('max_points', sa.Float(), nullable=False),
        sa.Column('secured_points', sa.Float(), nullable=False),
        sa.Column('at_risk_points', sa.Float(), nullable=False),
        sa.Column('lost_points', sa.Float(), nullable=False),
        sa.Column('not_assessable_points', sa.Float(), nullable=False),
        *_codes(),
        sa.Column('support_json', sa.JSON(), nullable=True),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('criterion_result', _CRITERION_RESULTS),
                           name='criterion_result_valid'),
        sa.CheckConstraint(_in_list('knowledge_source', _KNOWLEDGE_SOURCES),
                           name='knowledge_source_valid'),
        sa.CheckConstraint(_in_list('proof_status', _PROOF_STATUSES), name='proof_status_valid'),
        sa.CheckConstraint(_in_list('points_status', _POINTS_STATUSES),
                           name='points_status_valid'),
        sa.CheckConstraint(
            'max_points >= 0 AND secured_points >= 0 AND at_risk_points >= 0 '
            'AND lost_points >= 0 AND not_assessable_points >= 0',
            name='points_not_negative',
        ),
        _run_fk(),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'award_criterion_id',
                            name='uq_criterion_assessments_crit'),
    )
    op.create_index('ix_criterion_assessments_criterion', 'criterion_assessments',
                    ['award_criterion_id'])

    # 5. Vies de prestacio, nomes des dels serveis actuals.
    op.create_table(
        'delivery_candidates',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('scope_item_id', sa.Uuid(), nullable=False),
        sa.Column('company_service_id', sa.Uuid(), nullable=True),
        sa.Column('partner_role_id', sa.Uuid(), nullable=True),
        sa.Column('candidate_type', _enum(_CANDIDATE_TYPES, 'candidate_type'), nullable=False),
        sa.Column('origin', _enum(_CANDIDATE_ORIGINS, 'candidate_origin'), nullable=False),
        sa.Column('readiness', _enum(_READINESS, 'candidate_readiness'), nullable=False),
        sa.Column('territory_verdict', _enum(_TERRITORY_VERDICTS, 'territory_verdict'),
                  nullable=False),
        sa.Column('area_verdicts_json', sa.JSON(), nullable=True),
        *_codes(),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('candidate_type', _CANDIDATE_TYPES),
                           name='candidate_type_valid'),
        sa.CheckConstraint(_in_list('origin', _CANDIDATE_ORIGINS), name='origin_valid'),
        sa.CheckConstraint(_in_list('readiness', _READINESS), name='readiness_valid'),
        sa.CheckConstraint(_in_list('territory_verdict', _TERRITORY_VERDICTS),
                           name='territory_verdict_valid'),
        sa.CheckConstraint("candidate_type <> 'own_future' OR origin = 'hypothesis'",
                           name='own_future_is_hypothesis'),
        _run_fk(),
        sa.ForeignKeyConstraint(['scope_item_id'], ['tender_scope_items.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_service_id'], ['company_services.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['partner_role_id'], ['partner_roles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_delivery_candidates_scope_item', 'delivery_candidates', ['scope_item_id'])

    # 6. Troballes: mai `blocker_confirmed` (no es al CHECK).
    op.create_table(
        'analysis_findings',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('finding_type', _enum(_FINDING_TYPES, 'finding_type'), nullable=False),
        *_codes(),
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('criterion_id', sa.Uuid(), nullable=True),
        sa.Column('scope_item_id', sa.Uuid(), nullable=True),
        sa.Column('details_json', sa.JSON(), nullable=True),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('finding_type', _FINDING_TYPES), name='finding_type_valid'),
        _run_fk(),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['criterion_id'], ['award_criteria.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['scope_item_id'], ['tender_scope_items.id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_analysis_findings_run_type', 'analysis_findings',
                    ['run_id', 'finding_type'])

    # 7. Preguntes, deduplicades per clau determinista.
    op.create_table(
        'analysis_questions',
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('audience', _enum(_AUDIENCES, 'question_audience'), nullable=False),
        sa.Column('question_code', sa.String(length=80), nullable=False),
        sa.Column('rule_code', sa.String(length=80), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('criterion_id', sa.Uuid(), nullable=True),
        sa.Column('scope_item_id', sa.Uuid(), nullable=True),
        sa.Column('params_json', sa.JSON(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=64), nullable=False),
        _created_at(),
        _id(),
        sa.CheckConstraint(_in_list('audience', _AUDIENCES), name='audience_valid'),
        _run_fk(),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['criterion_id'], ['award_criteria.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['scope_item_id'], ['tender_scope_items.id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'dedupe_key', name='uq_analysis_questions_dedupe'),
    )


def downgrade() -> None:
    # Dades derivades: tornar enrere les elimina; tornar endavant les recalcula.
    op.drop_table('analysis_questions')
    op.drop_index('ix_analysis_findings_run_type', table_name='analysis_findings')
    op.drop_table('analysis_findings')
    op.drop_index('ix_delivery_candidates_scope_item', table_name='delivery_candidates')
    op.drop_table('delivery_candidates')
    op.drop_index('ix_criterion_assessments_criterion', table_name='criterion_assessments')
    op.drop_table('criterion_assessments')
    op.drop_index('ix_capability_need_assessments_need', table_name='capability_need_assessments')
    op.drop_table('capability_need_assessments')
    op.drop_index('ix_tender_fit_assessments_requirement', table_name='tender_fit_assessments')
    op.drop_table('tender_fit_assessments')
    op.drop_index('ix_tender_analysis_runs_scope', table_name='tender_analysis_runs')
    op.drop_index('uq_tender_analysis_runs_current', table_name='tender_analysis_runs')
    op.drop_table('tender_analysis_runs')
