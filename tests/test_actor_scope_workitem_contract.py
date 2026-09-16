"""Regression test for Task 11R8: ResolvedActorScope <-> WorkItem synthesis contract mismatch."""

import pytest
from pesu_agent.search.actor import ActorScopeKind, ActorResolutionStatus
from pesu_agent.investigation.models import ResolvedActorScope
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    ClaimNode,
    ClaimKind,
    ClaimStatus,
    ConfidenceLevel,
    ClaimSemanticType,
    WorkItemNode,
    WorkItemKind,
    WorkItemStatus,
    WorkItemCategory,
    WorkItemClaimLink,
    WorkItemEpisodeMembership,
    WorkItemClaimRelation,
)
from pesu_agent.investigation.workitem_synthesis import (
    EpisodeMembershipCoordinator,
    StubEpisodeMembershipGrouper,
    WorkItemSynthesisConfig,
    WorkItemSynthesisCoordinator,
    StubWorkItemSynthesizer,
    WorkItemGroupingCoordinator,
    WorkItemLifecycleVerificationCoordinator,
    StubWorkItemLifecycleVerifier,
    WorkItemRelationCoordinator,
    StubWorkItemRelationVerifier,
    DecisiveLifecycleAuthorityCoordinator,
    StubDecisiveLifecycleAuthorityVerifier,
)


def test_resolved_actor_scope_contract_with_workitem_synthesis():
    """Reproduce exact Task 11R7 failure:
    When authoritative_actor_scope is a ResolvedActorScope (as constructed by production runtime),
    WorkItem synthesis prepare_payload must not raise AttributeError: 'ResolvedActorScope' object has no attribute 'matched_author_name'.
    """
    # 1. Real R7 actor scope shape
    actor_scope = ResolvedActorScope(
        kind=ActorScopeKind.UNSPECIFIED,
        status=ActorResolutionStatus.RESOLVED,
        is_implicit=False,
    )

    # 2. Graph with authoritative actor scope, a verified claim, and a workitem
    graph = InvestigationStateGraph(authoritative_actor_scope=actor_scope)
    claim = ClaimNode(
        claim_id="clm-001",
        statement="Test claim",
        kind=ClaimKind.FACT,
        status=ClaimStatus.SUPPORTED,
        confidence=ConfidenceLevel.HIGH,
        semantic_type=ClaimSemanticType.EVENT,
    )
    graph.add_claim(claim)

    wi = WorkItemNode(
        work_item_id="wi-001",
        title="Test Work Item",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.IN_PROGRESS,
        category=WorkItemCategory.WORK_ITEM,
    )
    graph.add_work_item(wi)

    link = WorkItemClaimLink(
        link_id="lnk-001",
        work_item_id="wi-001",
        claim_id="clm-001",
        relation=WorkItemClaimRelation.PRIMARY,
        episode_membership=WorkItemEpisodeMembership.MEMBER,
    )
    graph.add_work_item_claim_link(link)

    # Site 1: line 1631 (WorkItemSynthesisCoordinator)
    coord1 = WorkItemSynthesisCoordinator(
        synthesizer=StubWorkItemSynthesizer([]),
        config=WorkItemSynthesisConfig(),
    )
    p1 = coord1.prepare_payload(graph, "objective", "question")
    assert p1.actor_scope_summary is None

    # Site 2: line 2405 (WorkItemGroupingCoordinator)
    coord2 = WorkItemGroupingCoordinator(
        synthesizer=None,  # prepare_payload does not call synthesizer
        config=WorkItemSynthesisConfig(),
    )
    p2 = coord2.prepare_payload(graph, "objective", "question")
    assert p2.actor_scope_summary is None

    # Site 3: line 2795 (WorkItemLifecycleVerificationCoordinator)
    coord3 = WorkItemLifecycleVerificationCoordinator(
        verifier=StubWorkItemLifecycleVerifier([]),
    )
    p3 = coord3.prepare_payload_for_work_item(graph, "wi-001", "objective", "question")
    assert p3.actor_scope_summary is None

    # Site 4: line 3641 (EpisodeMembershipCoordinator)
    coord4 = EpisodeMembershipCoordinator(
        grouper=StubEpisodeMembershipGrouper([]),
        config=WorkItemSynthesisConfig(),
    )
    p4 = coord4.prepare_payload(graph, "objective", "question")
    assert p4.actor_scope_summary is None

    # Site 5: line 4246 (WorkItemRelationCoordinator)
    coord5 = WorkItemRelationCoordinator(
        verifier=StubWorkItemRelationVerifier([]),
    )
    p5 = coord5.prepare_payload_for_work_item(graph, "wi-001", "objective", "question")
    assert p5.actor_scope_summary is None

    # Site 6: line 4923 (DecisiveLifecycleAuthorityCoordinator)
    coord6 = DecisiveLifecycleAuthorityCoordinator(
        verifier=StubDecisiveLifecycleAuthorityVerifier([]),
    )
    p6 = coord6.prepare_payload_for_work_item(graph, "wi-001", "objective", "question")
    assert p6.actor_scope_summary is None


def test_resolved_actor_scope_matched_author_name_propagation():
    """When matched_author_name is explicitly set, it propagates to all payloads."""
    actor_scope = ResolvedActorScope(
        kind=ActorScopeKind.NAMED_ACTOR,
        status=ActorResolutionStatus.RESOLVED,
        source_expression="홍길동",
        matched_author_name="홍길동",
    )
    graph = InvestigationStateGraph(authoritative_actor_scope=actor_scope)
    claim = ClaimNode(
        claim_id="clm-001",
        statement="Test claim",
        kind=ClaimKind.FACT,
        status=ClaimStatus.SUPPORTED,
        confidence=ConfidenceLevel.HIGH,
        semantic_type=ClaimSemanticType.EVENT,
    )
    graph.add_claim(claim)

    coord1 = WorkItemSynthesisCoordinator(
        synthesizer=StubWorkItemSynthesizer([]),
        config=WorkItemSynthesisConfig(),
    )
    p1 = coord1.prepare_payload(graph, "objective", "question")
    assert p1.actor_scope_summary == "홍길동"
