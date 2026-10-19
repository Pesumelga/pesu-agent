"""Wire qualified semantic stages to the same production transport."""
from pesu_agent.investigation.claim_verification import OpenRouterClaimVerifier
from pesu_agent.investigation.gap_assessment import OpenRouterEvidenceAssessor
from pesu_agent.investigation.production_trace_coordinator import (
    ProductionRealTraceCoordinator, create_authoritative_synthesis_coordinator,
)
from pesu_agent.investigation.workitem_synthesis import WorkItemSynthesisConfig


def create_grounded_coordinator(provider):
    config = WorkItemSynthesisConfig()
    return ProductionRealTraceCoordinator(
        evidence_assessor=OpenRouterEvidenceAssessor(provider=provider),
        claim_verifier=OpenRouterClaimVerifier(provider=provider),
        synthesis_coordinator=create_authoritative_synthesis_coordinator(
            synthesis_config=config, provider=provider),
        config=config,
    )
