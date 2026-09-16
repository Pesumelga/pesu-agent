"""TDD Contract Test for Stage 1A/1B/2/2.5 Configuration in Diagnostic Harness.

Verifies:
1. The bug: Diagnostic harness build_diagnostic_semantic_components passes WorkItemSynthesisConfig
   to Stage 1A/1B/2 constructors, violating the LLMConfig contract.
2. The fix: When fixed, it will create all stages with LLMConfig,
   proper api key accessors, and authoritative model/reasoning parameters.
Zero network calls required.
"""

import pytest
from scripts.diagnose_task11r7_full_production_pipeline import (
    build_diagnostic_semantic_components,
)
from pesu_agent.investigation.workitem_synthesis import (
    WorkItemSynthesisConfig,
    DecomposedWorkItemSynthesisCoordinator,
)
from pesu_agent.llm.config import LLMConfig


def test_diagnostic_stage_config_contract():
    """Asserts that build_diagnostic_semantic_components produces stages satisfying LLMConfig contract.
    
    MUST FAIL in the buggy harness because Stage 1A/1B/2 receive WorkItemSynthesisConfig.
    """
    s_cfg = WorkItemSynthesisConfig()
    coord = build_diagnostic_semantic_components(synthesis_config=s_cfg)

    assert isinstance(coord, DecomposedWorkItemSynthesisCoordinator)

    # Stage 1A
    g1a = coord.stage1a_coordinator.grouper
    assert isinstance(g1a.config, LLMConfig), (
        f"Stage 1A config must be LLMConfig, got {type(g1a.config).__name__}"
    )
    assert hasattr(g1a.config, "get_api_key_value"), (
        "Stage 1A config must have get_api_key_value method"
    )
    assert g1a.model == "openai/gpt-5.6-luna"
    assert g1a.reasoning_effort == "medium"
    assert g1a.temperature == 0.0

    # Stage 1B
    v1b = coord.stage1b_coordinator.verifier
    assert isinstance(v1b.config, LLMConfig), (
        f"Stage 1B config must be LLMConfig, got {type(v1b.config).__name__}"
    )
    assert hasattr(v1b.config, "get_api_key_value"), (
        "Stage 1B config must have get_api_key_value method"
    )
    assert v1b.model == "openai/gpt-5.6-luna"
    assert v1b.reasoning_effort == "medium"
    assert v1b.temperature == 0.0

    # Stage 2
    v2 = coord.stage2_coordinator.verifier
    assert isinstance(v2.config, LLMConfig), (
        f"Stage 2 config must be LLMConfig, got {type(v2.config).__name__}"
    )
    assert hasattr(v2.config, "get_api_key_value"), (
        "Stage 2 config must have get_api_key_value method"
    )
    assert v2.model == "openai/gpt-5.6-luna"
    assert v2.reasoning_effort == "medium"
    assert v2.temperature == 0.0

    # Stage 2.5
    assert coord.stage25_coordinator is not None
    v25 = coord.stage25_coordinator.verifier
    assert isinstance(v25.config, LLMConfig), (
        f"Stage 2.5 config must be LLMConfig, got {type(v25.config).__name__}"
    )
    assert hasattr(v25.config, "get_api_key_value"), (
        "Stage 2.5 config must have get_api_key_value method"
    )
    assert v25.model == "openai/gpt-5.6-luna"
    assert v25.reasoning_effort == "high"
    assert v25.temperature == 0.0
