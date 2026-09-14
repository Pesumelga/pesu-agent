"""Phase 13.5D: Production Real Trace Coordinator.

Thin, authoritative sequential orchestrator unifying read-only Slack retrieval
evidence ingestion with the frozen multi-stage WorkItem reasoning pipeline
(Stages 1A, 1B, 2, 2.5 + Deterministic Decisive Authority Reconciliation)
across a single InvestigationStateGraph.

Architectural Invariants:
- ONE_GRAPH_PER_INVESTIGATION = True (Zero graph duplication or lossy DTO recreation)
- SLACK_WRITE_ACTIONS = 0 (Strict read-only safety)
- LIVE_SLACK_ACTIONS = 0 (Zero live Slack network calls during orchestration)
- OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS = 0 (Zero legacy analyzer fallbacks)
- EVERY_SUCCESSFUL_FINAL_STATE_HAS_COMPLETE_TRACE = True (Traceability gate enforced)
- THIN_ORCHESTRATION_ONLY = True (Zero domain semantic re-judging or status override)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from pesu_agent.investigation.claim_verification import (
    ClaimVerificationCoordinator,
    ClaimVerificationResult,
    ClaimVerificationSchemaError,
    InvestigationClaimVerifier,
    OpenRouterClaimVerifier,
)
from pesu_agent.investigation.context_expansion import InvestigationSeedPoolBuilder
from pesu_agent.investigation.gap_assessment import (
    AssessmentPayloadBuilder,
    EvidenceAssessmentProviderError,
    EvidenceAssessmentSchemaError,
    InvestigationEvidenceAssessor,
    OpenRouterEvidenceAssessor,
)
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemNode,
    WorkItemStatus,
)
from pesu_agent.investigation.models import (
    EvidenceItem,
    ResolvedActorScope,
    ResolvedTemporalScope,
)
from pesu_agent.investigation.workitem_synthesis import (
    DecomposedWorkItemSynthesisCoordinator,
    OpenRouterDecisiveLifecycleAuthorityVerifier,
    OpenRouterEpisodeMembershipGrouper,
    OpenRouterWorkItemLifecycleVerifier,
    OpenRouterWorkItemRelationVerifier,
    WorkItemSynthesisConfig,
    WorkItemSynthesisResult,
    WorkItemSynthesisSchemaError,
    WorkItemTraceabilityChain,
    WorkItemValidationError,
    build_work_item_traceability_chain,
)

logger = logging.getLogger(__name__)

# Architectural Invariants
ONE_GRAPH_PER_INVESTIGATION: bool = True
SLACK_WRITE_ACTIONS: int = 0
LIVE_SLACK_ACTIONS: int = 0
OLD_ANALYZER_SEMANTIC_FALLBACK_CALLS: int = 0
EVERY_SUCCESSFUL_FINAL_STATE_HAS_COMPLETE_TRACE: bool = True
THIN_ORCHESTRATION_ONLY: bool = True


class ProductionTraceResult(BaseModel):
    """Result of production trace orchestration across a single InvestigationStateGraph."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    success: bool
    question: str
    graph: InvestigationStateGraph
    work_items: dict[str, WorkItemNode] = Field(default_factory=dict)
    traceability_chains: dict[str, WorkItemTraceabilityChain] = Field(default_factory=dict)
    error_message: Optional[str] = None
    stop_reason: str = "COMPLETED"


class ProductionRealTraceCoordinator:
    """Thin sequential coordinator connecting retrieval evidence, real claim formation,

    claim verification, and decomposed WorkItem synthesis across a single InvestigationStateGraph.
    """

    def __init__(
        self,
        seed_pool_builder: Optional[InvestigationSeedPoolBuilder] = None,
        evidence_assessor: Optional[InvestigationEvidenceAssessor] = None,
        claim_verifier: Optional[InvestigationClaimVerifier] = None,
        claim_verification_coordinator: Optional[ClaimVerificationCoordinator] = None,
        synthesis_coordinator: Optional[DecomposedWorkItemSynthesisCoordinator] = None,
        config: Optional[WorkItemSynthesisConfig] = None,
    ) -> None:
        self.config = config or WorkItemSynthesisConfig()
        self.seed_pool_builder = seed_pool_builder or InvestigationSeedPoolBuilder()
        self.evidence_assessor = evidence_assessor or OpenRouterEvidenceAssessor()
        self.claim_verifier = claim_verifier or OpenRouterClaimVerifier()
        self.claim_verification_coordinator = (
            claim_verification_coordinator or ClaimVerificationCoordinator()
        )

        if synthesis_coordinator is not None:
            self.synthesis_coordinator = synthesis_coordinator
        else:
            self.synthesis_coordinator = DecomposedWorkItemSynthesisCoordinator(
                membership_grouper=OpenRouterEpisodeMembershipGrouper(config=self.config),
                relation_verifier=OpenRouterWorkItemRelationVerifier(config=self.config),
                lifecycle_verifier=OpenRouterWorkItemLifecycleVerifier(config=self.config),
                authority_verifier=OpenRouterDecisiveLifecycleAuthorityVerifier(config=self.config),
                config=self.config,
            )

    async def trace_and_synthesize_async(
        self,
        question: str,
        candidates: list[EvidenceItem],
        investigation_objective: Optional[str] = None,
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
    ) -> ProductionTraceResult:
        """Executes full production trace pipeline across a single InvestigationStateGraph."""
        # Step 1: Instantiate single state graph (ONE_GRAPH_PER_INVESTIGATION = True)
        graph = InvestigationStateGraph(
            question=question,
            authoritative_actor_scope=actor_scope,
            authoritative_temporal_scope=temporal_scope,
        )

        # Step 2: Empty candidates check
        if not candidates:
            return ProductionTraceResult(
                success=True,
                question=question,
                graph=graph,
                work_items={},
                traceability_chains={},
                stop_reason="NO_EVIDENCE",
            )

        # Step 3: Populate seed pool into graph
        self.seed_pool_builder.populate_seed_graph(
            graph=graph,
            candidates=candidates,
            temporal_scope=temporal_scope,
            actor_scope=actor_scope,
            candidate_query_provenance=candidate_query_provenance,
        )

        if not graph.evidence_nodes:
            return ProductionTraceResult(
                success=True,
                question=question,
                graph=graph,
                work_items={},
                traceability_chains={},
                stop_reason="NO_EVIDENCE",
            )

        # Step 4: Build AssessmentPayload from graph
        payload = AssessmentPayloadBuilder.build_payload(
            question=question,
            graph=graph,
        )

        # Step 5: Evidence Assessment (ProposedClaim Formation)
        try:
            assessment = await self.evidence_assessor.assess(payload)
        except (EvidenceAssessmentProviderError, RuntimeError) as e:
            logger.warning("Evidence assessor provider failure: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="PROVIDER_EXECUTION_FAILURE",
            )
        except (EvidenceAssessmentSchemaError, ValueError) as e:
            logger.warning("Evidence assessor invalid structured output: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="INVALID_PROVIDER_OUTPUT",
            )
        except Exception as e:
            logger.warning("Evidence assessor unexpected failure: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="PROVIDER_EXECUTION_FAILURE",
            )

        # Check preserved last_error in case assessor was configured with fail_closed_on_provider_error=True
        if getattr(self.evidence_assessor, "last_error", None) is not None:
            last_err = self.evidence_assessor.last_error
            err_reason = (
                "INVALID_PROVIDER_OUTPUT"
                if isinstance(last_err, (EvidenceAssessmentSchemaError, ValueError))
                else "PROVIDER_EXECUTION_FAILURE"
            )
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(last_err),
                stop_reason=err_reason,
            )

        if not assessment.proposed_claims:
            return ProductionTraceResult(
                success=True,
                question=question,
                graph=graph,
                work_items={},
                traceability_chains={},
                stop_reason="NO_SUPPORTED_CLAIMS",
            )

        # Step 6: Real Claim Verification & Status Resolution
        try:
            verification_res = await self.claim_verification_coordinator.coordinate_verification_async(
                graph=graph,
                proposed_claims=assessment.proposed_claims,
                verifier=self.claim_verifier,
            )
        except (ClaimVerificationSchemaError, ValueError) as e:
            logger.warning("Claim verification schema error: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="INVALID_PROVIDER_OUTPUT",
            )
        except Exception as e:
            logger.warning("Claim verification provider failure: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="PROVIDER_EXECUTION_FAILURE",
            )

        if not graph.claim_nodes:
            return ProductionTraceResult(
                success=True,
                question=question,
                graph=graph,
                work_items={},
                traceability_chains={},
                stop_reason="NO_SUPPORTED_CLAIMS",
            )

        # Step 7: Decomposed WorkItem Synthesis (Stages 1A, 1B, 2, 2.5 + Decisive Authority Filter)
        try:
            synthesis_res: WorkItemSynthesisResult = (
                await self.synthesis_coordinator.synthesize_and_verify_async(
                    graph=graph,
                    investigation_objective=investigation_objective,
                    investigation_question=question,
                    raise_on_error=False,
                )
            )
        except (WorkItemSynthesisSchemaError, WorkItemValidationError, ValueError) as e:
            logger.warning("WorkItem synthesis validation/schema error: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="INVALID_PROVIDER_OUTPUT",
            )
        except Exception as e:
            logger.warning("WorkItem synthesis provider failure: %s", e)
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=str(e),
                stop_reason="PROVIDER_EXECUTION_FAILURE",
            )

        if not synthesis_res.success:
            err_msg = synthesis_res.error_message or "WorkItem synthesis failed"
            return ProductionTraceResult(
                success=False,
                question=question,
                graph=graph,
                error_message=err_msg,
                stop_reason="INVALID_PROVIDER_OUTPUT",
            )

        # Step 8: Build and enforce complete traceability chain
        work_items: dict[str, WorkItemNode] = {
            wid: graph.work_items[wid]
            for wid in synthesis_res.work_item_ids
            if wid in graph.work_items
        }

        traceability_chains: dict[str, WorkItemTraceabilityChain] = {}
        for wid in synthesis_res.work_item_ids:
            chain = build_work_item_traceability_chain(graph, wid)
            traceability_chains[wid] = chain
            if not chain.is_traceable:
                # Invariant: EVERY_SUCCESSFUL_FINAL_STATE_HAS_COMPLETE_TRACE = True
                err_msg = chain.traceability_error or f"Incomplete traceability for WorkItem '{wid}'"
                logger.warning("Traceability gate violation: %s", err_msg)
                return ProductionTraceResult(
                    success=False,
                    question=question,
                    graph=graph,
                    work_items=work_items,
                    traceability_chains=traceability_chains,
                    error_message=err_msg,
                    stop_reason="TRACEABILITY_FAILURE",
                )

        if not work_items:
            return ProductionTraceResult(
                success=True,
                question=question,
                graph=graph,
                work_items={},
                traceability_chains={},
                stop_reason="NO_SUPPORTED_CLAIMS",
            )

        # Step 9: Determine stop reason (SAFE_UNKNOWN vs COMPLETED)
        has_unknown = any(wi.status == WorkItemStatus.UNKNOWN for wi in work_items.values())
        stop_reason = "SAFE_UNKNOWN" if has_unknown else "COMPLETED"

        return ProductionTraceResult(
            success=True,
            question=question,
            graph=graph,
            work_items=work_items,
            traceability_chains=traceability_chains,
            stop_reason=stop_reason,
        )

    def trace_and_synthesize(
        self,
        question: str,
        candidates: list[EvidenceItem],
        investigation_objective: Optional[str] = None,
        temporal_scope: Optional[ResolvedTemporalScope] = None,
        actor_scope: Optional[ResolvedActorScope] = None,
        candidate_query_provenance: Optional[dict[str, list[Any]]] = None,
    ) -> ProductionTraceResult:
        """Synchronous wrapper executing trace_and_synthesize_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(
                    asyncio.run,
                    self.trace_and_synthesize_async(
                        question=question,
                        candidates=candidates,
                        investigation_objective=investigation_objective,
                        temporal_scope=temporal_scope,
                        actor_scope=actor_scope,
                        candidate_query_provenance=candidate_query_provenance,
                    ),
                ).result()

        return asyncio.run(
            self.trace_and_synthesize_async(
                question=question,
                candidates=candidates,
                investigation_objective=investigation_objective,
                temporal_scope=temporal_scope,
                actor_scope=actor_scope,
                candidate_query_provenance=candidate_query_provenance,
            )
        )
