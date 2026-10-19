"""The real default product assembly, replacing only external transport boundaries."""
import json
import pytest
from unittest.mock import MagicMock

from pesu_agent.investigation.models import InvestigationPlan, SearchQuery
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.ui import investigation_service as service_module


class FixtureBackend(WebViewSlackSearchBackend):
    def search_messages(self, query, max_results=5):
        return [SlackSearchResult(result_index=1, query=query, message_fingerprint="fixture", text="서비스 전환 완료 및 점검 통과",
            author="담당자", timestamp_raw="2026-10-01T12:00:00Z",
            result_url="https://fixture.slack.com/archives/C0123456789/p1724738400123456")]


class ScriptedTransport:
    provider_name = "offline-transport"

    def __init__(self, *args, **kwargs):
        self.schemas = []
        self.options = {}

    async def generate_structured(self, *, response_schema, **kwargs):
        name = response_schema.__name__
        self.schemas.append(name)
        self.options[name] = kwargs
        data = {
            "InvestigationPlan": dict(objective="전환 상태 확인", search_queries=[dict(query="서비스 전환", purpose="상태 확인")], questions_to_resolve=["상태는?"], anchor_terms=["전환"]),
            "InvestigationAssessment": dict(proposed_claims=[dict(proposal_id="P1", statement="서비스 전환 완료 및 점검 통과", kind="FACT", semantic_type="OUTCOME", supporting_evidence_ids=["E1"])]),
            "ClaimVerificationAssessment": dict(verdicts=[dict(claim_proposal_id="P1", evidence_id="E1", semantic_relation="SUPPORTS", attribution_strength="DIRECT")]),
            "EpisodeMembershipProposal": dict(work_items=[dict(local_proposal_id="item", title="서비스 전환", kind="TASK", member_claim_ids=["C1"], context_claim_ids=[])]),
            "WorkItemRelationProposal": dict(work_item_id="WI-1", relations=[dict(claim_id="C1", relation="PRIMARY")]),
            "WorkItemLifecycleVerificationProposal": dict(work_item_id="WI-1", claim_signals=[dict(claim_id="C1", lifecycle_signal="COMPLETION")]),
            "DecisiveLifecycleAuthorityProposal": dict(work_item_id="WI-1", verifications=[dict(claim_id="C1", proposed_signal="COMPLETION", decision="CONFIRMED")]),
        }
        assert name in data, f"Unexpected provider schema: {name}"
        return response_schema.model_validate(data[name]), LLMUsageStats(provider=self.provider_name, model="fixture", input_tokens=2, output_tokens=3, total_tokens=5)


def test_default_product_worker_returns_grounded_answer_and_preserves_citation(monkeypatch, tmp_path):
    provider = ScriptedTransport()
    backend = FixtureBackend()
    monkeypatch.setattr(service_module, "OpenRouterProvider", lambda *a, **kw: provider)
    monkeypatch.setattr(service_module, "create_slack_search_backend", lambda *a, **kw: backend)
    events = []
    history = MagicMock()
    service = service_module.InvestigationService(post_to_ui=events.append,
        allow_real_slack_external_analysis=True, history_store=history)
    try:
        orchestrator = service._get_or_create_orchestrator()
        orchestrator.config.base_output_dir = tmp_path
        service._run_investigation_worker("서비스 전환 상태를 알려줘", "real-product-path")
        completed = [m for m in events if m.type == "investigation.completed"]
        assert len(completed) == 1, [(m.type, m.payload) for m in events]
        payload = completed[0].payload
        assert payload["work_items"][0]["status"] == "COMPLETED"
        assert payload["work_items"][0]["findings"][0]["label"] == "확인된 사실"
        assert payload["evidence"][0]["permalink"].endswith("p1724738400123456")
        assert "AnalysisResult" not in provider.schemas
        assert "DecisiveLifecycleAuthorityProposal" in provider.schemas
        assert provider.options["DecisiveLifecycleAuthorityProposal"]["reasoning_effort"] == "high"
        assert service._current_citations["E1"] == payload["evidence"][0]["permalink"]
        assert history.save_investigation.call_count == 1
        audit = json.loads(next(tmp_path.rglob("audit.json")).read_text(encoding="utf-8"))
        assert audit["llm_calls"] == len(provider.schemas) == 7
        assert audit["input_tokens"] == 14
        service._run_investigation_worker("서비스 전환 상태를 알려줘", "next-product-request")
        audits = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.rglob("audit.json")]
        assert len(audits) == 2 and all(a["llm_calls"] == 7 for a in audits)
        assert len([m for m in events if m.type == "investigation.completed"]) == 2
    finally:
        service.shutdown()



@pytest.mark.parametrize("mode", ["no-evidence", "provider-failure", "budget", "auth"])
def test_default_product_distinguishes_missing_evidence_and_execution_failure(monkeypatch, tmp_path, mode):
    provider = ScriptedTransport()
    backend = FixtureBackend()
    if mode == "no-evidence":
        backend.search_messages = lambda *a, **kw: []
    if mode == "auth":
        from pesu_agent.search.webview_slack_search import WebViewSearchError, WebViewSearchFailureCode
        def auth_failure(*args, **kwargs):
            raise WebViewSearchError(WebViewSearchFailureCode.AUTH_REQUIRED, "Opaque runtime failure")
        backend.search_messages = auth_failure
    if mode == "provider-failure":
        original = provider.generate_structured
        async def fail_at_assessment(**kwargs):
            if kwargs["response_schema"].__name__ == "InvestigationAssessment":
                raise RuntimeError("Synthetic transport failure")
            return await original(**kwargs)
        provider.generate_structured = fail_at_assessment
    monkeypatch.setattr(service_module, "OpenRouterProvider", lambda *a, **kw: provider)
    monkeypatch.setattr(service_module, "create_slack_search_backend", lambda *a, **kw: backend)
    events = []
    history = MagicMock()
    service = service_module.InvestigationService(post_to_ui=events.append,
        allow_real_slack_external_analysis=True, history_store=history)
    try:
        orchestrator = service._get_or_create_orchestrator()
        orchestrator.config.base_output_dir = tmp_path
        if mode == "budget":
            orchestrator.provider.max_calls = 1
        service._run_investigation_worker("서비스 전환 상태를 알려줘", mode)
        completed = [m for m in events if m.type == "investigation.completed"]
        failed = [m for m in events if m.type == "investigation.failed"]
        if mode == "no-evidence":
            assert len(completed) == 1 and not failed
            assert completed[0].payload["stop_reason"] == "NO_EVIDENCE"
            assert completed[0].payload["work_items"] == []
            assert "InvestigationAssessment" not in provider.schemas
        else:
            assert not completed and len(failed) == 1
            assert history.save_investigation.call_count == 0
            assert service._current_citations == {}
            if mode == "auth":
                assert failed[0].payload["auth_required"] is True
                assert any(m.type == "app.status" and m.payload.get("status") == "slack_login_required" for m in events)
            if mode == "budget":
                assert "한도" in failed[0].payload["error"]
                assert provider.schemas == ["InvestigationPlan"]
    finally:
        service.shutdown()


def test_product_requires_consent_before_creating_runtime_or_sending_data(monkeypatch):
    runtime = MagicMock()
    monkeypatch.setattr(service_module, "create_slack_search_backend", runtime)
    credentials = MagicMock()
    credentials.get_openrouter_api_key.return_value = ("synthetic-key", "test")
    events = []
    service = service_module.InvestigationService(post_to_ui=events.append,
        credential_store=credentials, history_store=MagicMock())
    try:
        assert service.submit_investigation("업무 상태를 알려줘", "consent-gate") is False
        assert any(m.type == "consent.external_analysis.required" for m in events)
        runtime.assert_not_called()
        assert service.backend_creation_count == 0
    finally:
        service.shutdown()


@pytest.mark.anyio
async def test_provider_cost_and_failure_limits_block_before_next_transport_call():
    from pesu_agent.llm.budgeted import BudgetedProvider
    class CostTransport:
        provider_name = "fixture"
        calls = 0
        async def generate_structured(self, **kwargs):
            self.calls += 1
            return None, LLMUsageStats(provider="fixture", model="fixture", actual_cost_usd=.06)
    transport = CostTransport()
    provider = BudgetedProvider(transport, max_calls=2, max_cost_usd=.05)
    await provider.generate_structured()
    with pytest.raises(RuntimeError, match="LLM_BUDGET_REACHED"):
        await provider.generate_structured()
    assert transport.calls == 1
    provider.reset()
    await provider.generate_structured()
    assert transport.calls == 2
    stopping = BudgetedProvider(transport, should_stop=lambda: True)
    with pytest.raises(RuntimeError, match="INVESTIGATION_CANCELLED"):
        await stopping.generate_structured()
    assert transport.calls == 2

    class FailingTransport:
        provider_name = "fixture"
        calls = 0
        async def generate_structured(self, **kwargs):
            self.calls += 1
            raise RuntimeError("Synthetic failure")
    failing = FailingTransport()
    limited = BudgetedProvider(failing, max_calls=1)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        await limited.generate_structured()
    with pytest.raises(RuntimeError, match="LLM_BUDGET_REACHED"):
        await limited.generate_structured()
    assert failing.calls == 1
