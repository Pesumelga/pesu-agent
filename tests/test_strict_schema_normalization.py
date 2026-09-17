"""Tests for strict Structured Outputs schema normalization and provider request construction.

Validates that schemas passed to OpenRouter in native json_schema mode strictly comply
with OpenAI/Azure Structured Outputs requirements, preserving semantic contracts
without changing local Pydantic validation authority.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from pydantic import BaseModel, Field

from pesu_agent.investigation.gap_assessment import (
    InvestigationAssessment,
    ProposedClaim,
    ProposedInformationGap,
    ProposedRetrievalRequest,
    ClaimKind,
    ClaimSemanticType,
    AssessmentAnswerability,
    GapOriginType,
)
from pesu_agent.investigation.claim_verification import (
    ClaimVerificationAssessment,
    EvidenceClaimVerdict,
    VerificationSemanticRelation,
    AttributionStrength,
)
from pesu_agent.investigation.workitem_synthesis import (
    EpisodeMembershipProposal,
    ProposedEpisodeMembershipWorkItem,
    WorkItemRelationProposal,
    ProposedClaimRelation,
    WorkItemClaimRelation,
    WorkItemLifecycleVerificationProposal,
    ProposedClaimLifecycleSignal,
    WorkItemLifecycleSignal,
)
from pesu_agent.llm.models import LLMMessage
from pesu_agent.llm.openrouter import OpenRouterProvider, OpenRouterAPIError
from pesu_agent.llm.schema import normalize_strict_json_schema


AFFECTED_SCHEMAS: list[type[BaseModel]] = [
    InvestigationAssessment,
    ClaimVerificationAssessment,
    EpisodeMembershipProposal,
    WorkItemRelationProposal,
    WorkItemLifecycleVerificationProposal,
]


# ============================================================================
# Strict Schema Contract Validator Helper (Offline Oracle)
# ============================================================================

def assert_is_strict_json_schema(schema: dict[str, Any], path: str = "root") -> None:
    """Recursively validates that a JSON schema satisfies strict Structured Outputs rules."""
    assert isinstance(schema, dict), f"{path}: schema must be a dictionary, got {type(schema)}"

    # If it is an object schema (has 'properties' or type == 'object')
    is_object = schema.get("type") == "object" or "properties" in schema
    if is_object:
        assert schema.get("type") == "object", f"{path}: object schema must explicitly have type='object'"
        assert schema.get("additionalProperties") is False, (
            f"{path}: object schema must have additionalProperties=False, got {schema.get('additionalProperties')}"
        )
        properties = schema.get("properties", {})
        assert isinstance(properties, dict), f"{path}: properties must be a dict"
        required = schema.get("required")
        assert isinstance(required, list), f"{path}: required must be a list"

        # Every property MUST appear in required
        missing_props = [p for p in properties if p not in required]
        assert not missing_props, f"{path}: properties {missing_props} missing from required"

        # Required order must match property declaration order (no arbitrary re-sorting)
        prop_keys = list(properties.keys())
        assert required == prop_keys, (
            f"{path}: required order must match property declaration order. "
            f"Expected {prop_keys}, got {required}"
        )

        # Recursively validate each property
        for p, p_schema in properties.items():
            if isinstance(p_schema, dict):
                assert_is_strict_json_schema(p_schema, path=f"{path}.properties[{p}]")

    # If it is an array schema
    if schema.get("type") == "array" or "items" in schema:
        assert schema.get("type") == "array", f"{path}: array schema must have type='array'"
        items = schema.get("items")
        assert items is not None, f"{path}: array schema must define items"
        if isinstance(items, dict):
            assert_is_strict_json_schema(items, path=f"{path}.items")

    # If it has anyOf
    if "anyOf" in schema:
        assert isinstance(schema["anyOf"], list), f"{path}: anyOf must be a list"
        for i, branch in enumerate(schema["anyOf"]):
            if isinstance(branch, dict):
                assert_is_strict_json_schema(branch, path=f"{path}.anyOf[{i}]")

    # If it has allOf
    if "allOf" in schema:
        assert isinstance(schema["allOf"], list), f"{path}: allOf must be a list"
        for i, branch in enumerate(schema["allOf"]):
            if isinstance(branch, dict):
                assert_is_strict_json_schema(branch, path=f"{path}.allOf[{i}]")

    # If it has $defs / definitions
    for defs_key in ("$defs", "definitions"):
        if defs_key in schema:
            defs = schema[defs_key]
            assert isinstance(defs, dict), f"{path}.{defs_key} must be a dict"
            for d_name, d_schema in defs.items():
                if isinstance(d_schema, dict):
                    assert_is_strict_json_schema(d_schema, path=f"{path}.{defs_key}[{d_name}]")

    # Check $ref rules: strict schema disallows sibling keys alongside $ref
    if "$ref" in schema:
        sibling_keys = [k for k in schema if k != "$ref" and not k.startswith("$")]
        assert not sibling_keys, f"{path}: $ref must not have sibling keys {sibling_keys}"


# ============================================================================
# 1. Characterization Test (Raw Pydantic Incompatibility)
# ============================================================================

def test_characterization_raw_pydantic_schemas_not_strict_compatible():
    """Characterization test documenting that all 5 raw Pydantic v2 schemas violate strict rules.

    Raw Pydantic schemas omit fields with defaults from 'required' and omit
    additionalProperties=False on default BaseModel schemas.
    """
    incompatible_schemas = 0
    for model_cls in AFFECTED_SCHEMAS:
        raw_schema = model_cls.model_json_schema()
        try:
            assert_is_strict_json_schema(raw_schema, path=model_cls.__name__)
        except AssertionError:
            incompatible_schemas += 1

    assert incompatible_schemas == 5, (
        f"Expected all 5 raw schemas to be strict-incompatible, found {incompatible_schemas}"
    )


# ============================================================================
# 2. Production Provider Strict Construction Verification (GREEN)
# ============================================================================

@pytest.mark.parametrize("model_cls", AFFECTED_SCHEMAS)
def test_production_provider_native_schema_construction_satisfies_strict(model_cls: type[BaseModel]):
    """Verifies that OpenRouterProvider.build_native_response_format produces
    strictly compliant schemas for all 5 affected models.
    """
    provider = OpenRouterProvider()
    rf = provider.build_native_response_format(model_cls)

    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == model_cls.__name__

    schema = rf["json_schema"]["schema"]
    assert_is_strict_json_schema(schema, path=model_cls.__name__)


# ============================================================================
# 3. Offline Differential Characterization vs OpenAI SDK Reference Oracle
# ============================================================================

@pytest.mark.parametrize("model_cls", AFFECTED_SCHEMAS)
def test_offline_differential_vs_openai_sdk_oracle(model_cls: type[BaseModel]):
    """Compares normalized schema from pesu_agent.llm.schema with the output
    of OpenAI Python SDK's reference helper (used as test oracle only).
    """
    from openai.lib._pydantic import to_strict_json_schema as sdk_oracle

    our_schema = normalize_strict_json_schema(model_cls)
    oracle_schema = sdk_oracle(model_cls)

    our_json = json.dumps(our_schema, sort_keys=True)
    oracle_json = json.dumps(oracle_schema, sort_keys=True)

    assert our_json == oracle_json, (
        f"Mismatch between our normalizer and SDK oracle for {model_cls.__name__}"
    )


# ============================================================================
# 4. Normalization Determinism
# ============================================================================

@pytest.mark.parametrize("model_cls", AFFECTED_SCHEMAS)
def test_normalization_deterministic(model_cls: type[BaseModel]):
    """Verifies that normalizing a schema multiple times produces identical results."""
    run1 = normalize_strict_json_schema(model_cls)
    run2 = normalize_strict_json_schema(model_cls)

    assert run1 == run2
    assert json.dumps(run1) == json.dumps(run2)


# ============================================================================
# 5. Semantic Validation (User Item 4 Criteria)
# ============================================================================

def test_semantic_validation_investigation_assessment():
    """Verifies semantic properties on InvestigationAssessment:
    - Normalized schema allows canonical model_dump.
    - Local Pydantic accepts the payload.
    - Optional/nullable value semantics are preserved.
    - Explicit default fields do not modify local values.
    """
    instance = InvestigationAssessment(
        proposed_claims=[
            ProposedClaim(
                proposal_id="claim-1",
                statement="Production issue resolved",
                kind=ClaimKind.FACT,
                semantic_type=ClaimSemanticType.EVENT,
                supporting_evidence_ids=["ev-1"],
                contradicting_evidence_ids=[],
            ),
            ProposedClaim(
                proposal_id=None,  # Nullable optional field
                statement="Second statement",
                semantic_type=ClaimSemanticType.ACTION,
            ),
        ],
        information_gaps=[
            ProposedInformationGap(
                proposal_id=None,
                description="Root cause details",
                origin_type=GapOriginType.QUESTION,
                target_actor_policy=None,
                target_temporal_policy=None,
            ),
        ],
        retrieval_requests=[],
        answerability=AssessmentAnswerability.ANSWERABLE,
        stop_recommendation=True,
    )

    # 1. Canonical dump
    payload = instance.model_dump(mode="json")

    # 2. Local Pydantic validation authority
    reconstructed = InvestigationAssessment.model_validate(payload)
    assert reconstructed == instance
    assert reconstructed.proposed_claims[0].proposal_id == "claim-1"
    assert reconstructed.proposed_claims[1].proposal_id is None
    assert reconstructed.stop_recommendation is True


def test_semantic_validation_claim_verification():
    """Verifies semantic properties on ClaimVerificationAssessment."""
    instance = ClaimVerificationAssessment(
        verdicts=[
            EvidenceClaimVerdict(
                claim_proposal_id="c-1",
                evidence_id="e-1",
                semantic_relation=VerificationSemanticRelation.SUPPORTS,
                attribution_strength=AttributionStrength.DIRECT,
                basis=None,  # Nullable optional field
            ),
            EvidenceClaimVerdict(
                claim_proposal_id="c-2",
                evidence_id="e-2",
                semantic_relation=VerificationSemanticRelation.CONTRADICTS,
                basis="Evidence timestamp precedes claim",
            ),
        ]
    )

    payload = instance.model_dump(mode="json")
    reconstructed = ClaimVerificationAssessment.model_validate(payload)
    assert reconstructed == instance
    assert reconstructed.verdicts[0].basis is None
    assert reconstructed.verdicts[1].basis == "Evidence timestamp precedes claim"


def test_semantic_validation_episode_membership():
    """Verifies semantic properties on EpisodeMembershipProposal (Stage 1A)."""
    instance = EpisodeMembershipProposal(
        work_items=[
            ProposedEpisodeMembershipWorkItem(
                local_proposal_id="EWI-1",
                title="Service restart sequence",
                member_claim_ids=["c-1", "c-2"],
                context_claim_ids=[],
            )
        ],
        unassigned_claim_ids=["c-3"],
    )

    payload = instance.model_dump(mode="json")
    reconstructed = EpisodeMembershipProposal.model_validate(payload)
    assert reconstructed == instance
    assert reconstructed.work_items[0].local_proposal_id == "EWI-1"
    assert reconstructed.unassigned_claim_ids == ["c-3"]


def test_semantic_validation_work_item_relation():
    """Verifies semantic properties on WorkItemRelationProposal (Stage 1B)."""
    instance = WorkItemRelationProposal(
        work_item_id="WI-100",
        relations=[
            ProposedClaimRelation(
                claim_id="c-1",
                relation=WorkItemClaimRelation.PRIMARY,
                rationale=None,  # Nullable optional field
            ),
            ProposedClaimRelation(
                claim_id="c-2",
                relation=WorkItemClaimRelation.OUTCOME,
                rationale="Follow-up outcome",
            ),
        ],
    )

    payload = instance.model_dump(mode="json")
    reconstructed = WorkItemRelationProposal.model_validate(payload)
    assert reconstructed == instance
    assert reconstructed.relations[0].rationale is None
    assert reconstructed.relations[1].rationale == "Follow-up outcome"


def test_semantic_validation_lifecycle_verification():
    """Verifies semantic properties on WorkItemLifecycleVerificationProposal (Stage 2)."""
    instance = WorkItemLifecycleVerificationProposal(
        work_item_id="WI-200",
        claim_signals=[
            ProposedClaimLifecycleSignal(
                claim_id="c-1",
                lifecycle_signal=WorkItemLifecycleSignal.COMPLETION,
                rationale=None,
            )
        ],
    )

    payload = instance.model_dump(mode="json")
    reconstructed = WorkItemLifecycleVerificationProposal.model_validate(payload)
    assert reconstructed == instance
    assert reconstructed.claim_signals[0].lifecycle_signal == WorkItemLifecycleSignal.COMPLETION
    assert reconstructed.claim_signals[0].rationale is None


# ============================================================================
# 6. Offline Integration Test: generate_structured uses build_native_response_format
# ============================================================================

@pytest.mark.anyio
async def test_generate_structured_uses_normalized_native_schema():
    """Verifies that generate_structured uses the exact normalized response_format
    from build_native_response_format in primary native mode.
    """
    provider = OpenRouterProvider()
    expected_rf = provider.build_native_response_format(InvestigationAssessment)

    mock_res = MagicMock(spec=httpx.Response)
    mock_res.status_code = 200
    mock_res.headers = httpx.Headers()
    mock_res.json.return_value = {
        "id": "gen_test_1",
        "model": "google/gemini-2.0-flash-001",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        InvestigationAssessment().model_dump(mode="json")
                    ),
                }
            }
        ],
        "usage": {"prompt_tokens": 80, "completion_tokens": 40},
    }

    captured_payload: dict[str, Any] = {}

    async def mock_post(endpoint: str, json: dict[str, Any], headers: dict[str, str]):
        captured_payload.update(json)
        return mock_res

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        obj, usage = await provider.generate_structured(
            messages=[LLMMessage(role="user", content="Assess evidence")],
            response_schema=InvestigationAssessment,
        )

    assert isinstance(obj, InvestigationAssessment)
    assert "response_format" in captured_payload
    assert captured_payload["response_format"] == expected_rf


# ============================================================================
# 7. Error Observability & Secret Masking
# ============================================================================

def test_openrouter_api_error_structured_metadata_and_secret_masking():
    """Verifies that OpenRouterAPIError parses structured error fields while
    strictly masking any API keys or Bearer tokens.
    """
    raw_error_json = json.dumps({
        "error": {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "provider_name": "Azure",
                "provider_error_code": "invalid_json_schema",
                "raw": json.dumps({
                    "error": {
                        "message": "Invalid schema for response_format 'InvestigationAssessment': missing 'proposal_id'",
                        "code": "invalid_json_schema",
                    }
                }),
            },
        }
    })

    err = OpenRouterAPIError.from_response(400, raw_error_json)

    assert err.status_code == 400
    assert err.error_code == "invalid_json_schema"
    assert err.upstream_provider == "Azure"
    assert "missing 'proposal_id'" in err.provider_message

    # Test secret masking in error message
    sensitive_raw = "Failed with token sk-or-v1-secret-key-99999 and Bearer abc.def.ghi in header"
    err_sensitive = OpenRouterAPIError.from_response(401, sensitive_raw)
    assert "sk-or-v1-secret-key-99999" not in str(err_sensitive)
    assert "Bearer abc.def.ghi" not in str(err_sensitive)
    assert "[REDACTED_API_KEY]" in str(err_sensitive)
