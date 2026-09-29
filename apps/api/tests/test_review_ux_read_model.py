"""Lectura canonica per a la UX (proves d'usuari): motius de «Cal revisar», origen del valor
i context literal. Nomes lectura, additiu, sense migracio. Tot SINTETIC, zero crides.

* `review_reasons`: codis PROPIS i estables, deduits nomes del que hi ha desat. Cap codi
  del pipeline no surt a l'API.
* `values[].source`/`source_label`: extret, corregit pel revisor o afegit manualment.
* `values[].context`: l'ancora literal del claim extret, mai inventada.
"""

from __future__ import annotations

import json

from app.models.enums import ReviewVerification
from app.schemas.review import ManualRequirementCreate, RequirementSnapshot
from app.services import review_actions
from app.services.review_view import REVIEW_REASON_LABELS, review_reasons
from tests import test_review_actions as actions
from tests.test_review_actions import INSURANCE, LATENCY, ORDINARY, _act, _item_of, _started, _url
from tests.test_review_layer import FORBIDDEN

analyzable = actions.analyzable
_clean_provider = actions._clean_provider
palamos_like = actions.palamos_like


async def _read(client, company, tid, item_id):
    response = await client.get(_url(company, tid, f"/requirements/{item_id}"))
    assert response.status_code == 200, response.text
    return response.json()


async def test_motius_i_origen_dels_valors_a_la_lectura(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    ordinary = await _item_of(session, company, palamos_like["rows"][ORDINARY])
    insurance = await _item_of(session, company, palamos_like["rows"][INSURANCE])

    body_ordinary = await _read(client_a, company, tid, ordinary.id)
    body_insurance = await _read(client_a, company, tid, insurance.id)

    # Un valor extret i sostingut: origen i context literal del plec.
    [value] = body_ordinary["values"]
    assert (value["source"], value["source_label"], value["context"]) == (
        "document", "Extret del document", "volumen anual de negocios")
    # L'assegurança: el valor no es a la cita -> motiu propi, no el del pipeline.
    codes = [r["code"] for r in body_insurance["review_reasons"]]
    assert "value_not_supported" in codes or "value_unverified" in codes, codes
    for reason in body_insurance["review_reasons"]:
        assert reason["label"] == REVIEW_REASON_LABELS[reason["code"]]
    listed = json.dumps((await client_a.get(_url(company, tid, "/requirements"))).json())
    for word in FORBIDDEN:
        assert word not in listed, word


async def test_corregit_pero_no_verificat(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][LATENCY])
    item_id = item.id

    corrected = (await _act(client_a, company, tid, item_id, "correct", 1, {
        "values": [{"key": "latencia", "operator": "<", "value": 70, "unit_label": "ms"}],
        "verification_status": "needs_review", "note": "Valor de la cita"})).json()

    assert corrected["verification_status"] == "needs_review"
    assert [r["code"] for r in corrected["review_reasons"]] == ["corrected_not_verified"]
    assert corrected["review_reasons"][0]["label"] == (
        "El contingut s'ha corregit, però el requisit encara no s'ha verificat.")
    [value] = corrected["values"]
    assert (value["source"], value["source_label"], value["context"]) == (
        "reviewer", "Corregit pel revisor", None)
    # La cita hi es, pero sense valors estructurats: no es diu que el plec no en tingui.
    evidence_label = corrected["evidence"][0]["status_label"]
    assert evidence_label == "Cita sense valors estructurats automàticament"

    confirmed = (await _act(client_a, company, tid, item_id, "confirm", 2)).json()
    assert confirmed["verification_status"] == "verified"
    assert confirmed["review_reasons"] == []  # resolt: cap motiu


async def test_un_manual_diu_afegit_manualment(session, palamos_like, tenant_a, client_a):
    user, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    manual = await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tid, user=user,
        data=ManualRequirementCreate(
            title="Mínim 64 trucades simultànies", category="technical_requirement",
            values=[{"key": "trucades", "operator": ">=", "value": 64, "unit_label": "trucades"}]))
    await session.commit()

    body = await _read(client_a, company, tid, manual.id)

    [value] = body["values"]
    assert (value["source"], value["source_label"], value["status_label"]) == (
        "reviewer", "Afegit manualment", "Afegit manualment")
    assert body["provenance_label"] == "Afegit manualment"
    assert [r["code"] for r in body["review_reasons"]] == ["no_evidence"]


def _snapshot(flagged: bool) -> RequirementSnapshot:
    return RequirementSnapshot(
        title="t", category="other", obligation="mandatory", modality="mandatory", values=[],
        needs_review_from_extraction=flagged)


def test_regles_pures_dels_motius():
    kwargs = {"requirement": None, "evidence": [], "has_citations": True}  # noqa: F841
    for resolved in (ReviewVerification.VERIFIED, ReviewVerification.PARTIAL,
                     ReviewVerification.REJECTED):
        assert review_reasons(verification=resolved, corrected=True, snapshot=_snapshot(True),
                              **kwargs) == []
    flagged = review_reasons(verification=ReviewVerification.NEEDS_REVIEW, corrected=False,
                             snapshot=_snapshot(True), requirement=("v1", {}), evidence=[],
                             has_citations=True)
    assert [r.code for r in flagged] == ["extraction_flagged"]
    none = review_reasons(verification=ReviewVerification.UNREVIEWED, corrected=False,
                          snapshot=_snapshot(False), requirement=("v1", {}), evidence=[],
                          has_citations=True)
    assert none == []
