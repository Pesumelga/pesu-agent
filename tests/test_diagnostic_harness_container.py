"""Regression test for Task 11R8 Diagnostic Harness container assumption defect."""

import pytest
from pesu_agent.investigation.graph_models import (
    InvestigationStateGraph,
    WorkItemClaimLink,
    WorkItemEpisodeMembership,
    WorkItemClaimRelation,
    WorkItemNode,
    WorkItemKind,
    WorkItemStatus,
    WorkItemCategory,
    ClaimNode,
    ClaimKind,
    ClaimStatus,
    ConfidenceLevel,
    ClaimSemanticType,
)


def test_work_item_claim_links_is_list_not_mapping():
    """Verify that InvestigationStateGraph.work_item_claim_links is a list,
    and calling .values() on it fails with AttributeError.
    """
    graph = InvestigationStateGraph()
    wi = WorkItemNode(
        work_item_id="wi-1",
        title="Test WI",
        kind=WorkItemKind.TASK,
        status=WorkItemStatus.IN_PROGRESS,
        category=WorkItemCategory.WORK_ITEM,
    )
    graph.add_work_item(wi)

    claim = ClaimNode(
        claim_id="clm-1",
        statement="Test statement",
        kind=ClaimKind.FACT,
        status=ClaimStatus.SUPPORTED,
        confidence=ConfidenceLevel.HIGH,
        semantic_type=ClaimSemanticType.EVENT,
    )
    graph.add_claim(claim)

    link = WorkItemClaimLink(
        link_id="lnk-1",
        work_item_id="wi-1",
        claim_id="clm-1",
        relation=WorkItemClaimRelation.PRIMARY,
        episode_membership=WorkItemEpisodeMembership.MEMBER,
    )
    graph.add_work_item_claim_link(link)

    assert isinstance(graph.work_item_claim_links, list)

    # Harness previously called .values(), which raises AttributeError on list
    with pytest.raises(AttributeError, match="'list' object has no attribute 'values'"):
        _ = graph.work_item_claim_links.values()

    # The correct harness iteration is over the list directly
    links = list(graph.work_item_claim_links)
    assert len(links) == 1
    assert links[0].link_id == "lnk-1"
