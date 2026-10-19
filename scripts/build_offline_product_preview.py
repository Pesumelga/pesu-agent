"""Build a self-contained product preview using synthetic evidence and explicit stubs.

No credentials, Slack runtime, or real model providers are created. Semantic stub
outputs are fixtures, not an AI evaluation. Graph resolution, answer projection,
and the shipped product renderer are real production code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pesu_agent.investigation.claim_verification import (
    EvidenceClaimVerdict, StubClaimVerifier, VerificationSemanticRelation,
)
from pesu_agent.investigation.gap_assessment import (
    InvestigationAssessment, ProposedClaim, StubEvidenceAssessor, EvidenceAssessmentProviderError,
)
from pesu_agent.investigation.graph_models import (
    AttributionStrength, ClaimKind, ClaimSemanticType, DecisiveAuthorityDecision,
    WorkItemClaimRelation, WorkItemKind, WorkItemLifecycleSignal,
)
from pesu_agent.investigation.models import SearchQuery
from pesu_agent.investigation.production_trace_coordinator import ProductionRealTraceCoordinator
from pesu_agent.investigation.workitem_synthesis import (
    DecisiveClaimVerificationResult, DecisiveLifecycleAuthorityProposal,
    DecomposedWorkItemSynthesisCoordinator, EpisodeMembershipProposal,
    ProposedClaimLifecycleSignal, ProposedClaimRelation, ProposedEpisodeMembershipWorkItem,
    StubDecisiveLifecycleAuthorityVerifier, StubEpisodeMembershipGrouper,
    StubWorkItemLifecycleVerifier, StubWorkItemRelationVerifier,
    WorkItemLifecycleVerificationProposal, WorkItemRelationProposal,
)
from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
from pesu_agent.ui.grounded_answer_builder import GroundedWorkItemAnswerBuilder
from pesu_agent.ui.result_presenter import present_grounded_investigation_result


class SyntheticSearch(WebViewSlackSearchBackend):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def search_messages(self, query, max_results=5):
        return self.rows[:max_results]


class FailedAssessment:
    async def assess(self, *args, **kwargs):
        raise EvidenceAssessmentProviderError("Synthetic provider failure")


async def build_examples():
    examples = []
    completed = ("샘플 서비스 전환을 완료했고 점검도 통과했습니다.", WorkItemLifecycleSignal.COMPLETION)
    blocker = ("필수 접근 승인이 없어 사용자 전환을 중단했습니다.", WorkItemLifecycleSignal.BLOCKER)
    for key, label, specs in (
        ("completed", "완료와 출처", [completed]),
        ("blocked", "완료 보고와 차단 요인 공존", [completed, blocker]),
        ("insufficient", "근거 부족", []),
        ("provider-failure", "분석 실패", [completed]),
    ):
        question = "샘플 서비스 전환은 어디까지 진행됐고, 남은 문제는 무엇인가요?"
        rows = [SlackSearchResult(
            result_index=i, query=question, text=text, author=f"예시 담당자 {i}",
            timestamp_raw=f"2026-10-01T0{i}:00:00Z",
            result_url=f"https://fixture.slack.com/archives/C0123456789/p1724738400{i:06d}",
            message_fingerprint=f"synthetic-{key}-{i}",
        ) for i, (text, _) in enumerate(specs, 1)]
        candidates = await SyntheticSearch(rows).search(SearchQuery(query=question, purpose="Synthetic preview"), max_results=5)
        claims = [ProposedClaim(
            proposal_id=f"P{i}", statement=text, kind=ClaimKind.FACT,
            semantic_type=ClaimSemanticType.OUTCOME, supporting_evidence_ids=[f"E{i}"],
        ) for i, (text, _) in enumerate(specs, 1)]
        assessor = FailedAssessment() if key == "provider-failure" else StubEvidenceAssessor(
            InvestigationAssessment(proposed_claims=claims))
        verifier = StubClaimVerifier(verdicts=[EvidenceClaimVerdict(
            claim_proposal_id=f"P{i}", evidence_id=f"E{i}",
            semantic_relation=VerificationSemanticRelation.SUPPORTS,
            attribution_strength=AttributionStrength.DIRECT,
        ) for i in range(1, len(specs) + 1)])
        synthesis = DecomposedWorkItemSynthesisCoordinator(
            membership_grouper=StubEpisodeMembershipGrouper(EpisodeMembershipProposal(work_items=[
                ProposedEpisodeMembershipWorkItem(local_proposal_id="sample", title="샘플 서비스 전환",
                    kind=WorkItemKind.TASK, member_claim_ids=[f"C{i}" for i in range(1, len(specs) + 1)], context_claim_ids=[])
            ] if specs else [])),
            relation_verifier=StubWorkItemRelationVerifier(WorkItemRelationProposal(work_item_id="WI-1", relations=[
                ProposedClaimRelation(claim_id=f"C{i}", relation=WorkItemClaimRelation.PRIMARY)
                for i in range(1, len(specs) + 1)])),
            lifecycle_verifier=StubWorkItemLifecycleVerifier(WorkItemLifecycleVerificationProposal(work_item_id="WI-1", claim_signals=[
                ProposedClaimLifecycleSignal(claim_id=f"C{i}", lifecycle_signal=signal)
                for i, (_, signal) in enumerate(specs, 1)])),
            authority_verifier=StubDecisiveLifecycleAuthorityVerifier(DecisiveLifecycleAuthorityProposal(work_item_id="WI-1", verifications=[
                DecisiveClaimVerificationResult(claim_id=f"C{i}", proposed_signal=signal, decision=DecisiveAuthorityDecision.CONFIRMED)
                for i, (_, signal) in enumerate(specs, 1)])),
        )
        result = await ProductionRealTraceCoordinator(evidence_assessor=assessor, claim_verifier=verifier,
            synthesis_coordinator=synthesis).trace_and_synthesize_async(question=question, candidates=candidates)
        answer = GroundedWorkItemAnswerBuilder.build_answer(question=question, graph=result.graph,
            work_items=result.work_items, traceability_chains=result.traceability_chains, stop_reason=result.stop_reason)
        examples.append({"id": key, "label": label, "question": question,
            "payload": present_grounded_investigation_result(key, answer),
            "source_texts": {f"E{i}": row.text for i, row in enumerate(rows, 1)},
            "trace": result.model_dump(mode="json")})
    return examples


def build_preview(output: Path, examples):
    assets = ROOT / "src/pesu_agent/ui/assets"
    page = (assets / "index.html").read_text(encoding="utf-8")
    css = (assets / "app.css").read_text(encoding="utf-8")
    js = (assets / "app.js").read_text(encoding="utf-8")
    data = json.dumps(examples, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    bootstrap = '''
const examples = EXAMPLES;
let receive;
window.chrome = {webview:{addEventListener:(_type, callback)=>{receive=callback;}, postMessage:message=>{
  if(message.type==='citation.open') {
    document.getElementById('preview-source-text').textContent=examples[Number(document.getElementById('preview-select').value)].source_texts[message.payload.evidence_id] || '예시 원문이 없습니다.';
    document.getElementById('preview-source').showModal();
  }
}}};
function runExample() {
  const example=examples[Number(document.getElementById('preview-select').value)];
  document.getElementById('query-input').value=example.question;
  window._pesuUI.setCurrentRequestId('preview');
  receive({data:{version:1,type:'investigation.completed',request_id:'preview',payload:example.payload}});
  document.getElementById('status-text').textContent='합성 예시 · '+example.label;
  document.querySelectorAll('.btn-open-citation').forEach(button=>{button.textContent='예시 원문 확인';});
}
'''.replace("EXAMPLES", data)
    controls = '''<aside style="padding:18px 28px;background:#fff5df;color:#604600;border-bottom:1px solid #e6c98b">
<strong>합성 데이터 · 오프라인 미리보기</strong><p style="margin:6px 0 12px">실제 Slack 조사나 AI 판단 결과가 아닙니다. 예시 근거와 고정 분석 입력으로 실제 업무 상태 해석·답변·화면 연결을 확인합니다.</p>
<label for="preview-select">확인할 상황 </label><select id="preview-select" style="padding:8px;margin-right:12px"></select>
<button type="button" id="preview-run" style="padding:8px 18px">예시 보기</button></aside>
<dialog id="preview-source"><h3>예시 원문 · 합성 데이터</h3><p id="preview-source-text" style="white-space:pre-wrap;max-width:600px"></p><form method="dialog"><button>닫기</button></form></dialog>'''
    finish = '''
for(let i=0;i<examples.length;i++) {const option=document.createElement('option');option.value=i;option.textContent=examples[i].label;document.getElementById('preview-select').appendChild(option);}
document.getElementById('preview-run').onclick=runExample;
document.getElementById('preview-select').onchange=runExample;
document.getElementById('query-input').readOnly=true;
document.getElementById('submit-btn').classList.add('hidden');
for(const id of ['header-settings-btn','header-history-btn']) document.getElementById(id).classList.add('hidden');
document.getElementById('provider-badge').textContent='AI 미연결';
document.getElementById('slack-capability-badge').textContent='Slack 미연결';
document.querySelector('.keyboard-hint').classList.add('hidden');
runExample();
'''
    page = page.replace('<link rel="stylesheet" href="app.css">', '<style>'+css+'</style>')
    page = page.replace('<body>', '<body>'+controls)
    page = page.replace('<script src="app.js"></script>', '<script>'+bootstrap+'</script><script>'+js+'</script><script>'+finish+'</script>')
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(page, encoding="utf-8")
    (output / "examples.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/product-preview")
    args = parser.parse_args()
    build_preview(args.output, asyncio.run(build_examples()))
    print(args.output / "index.html")
