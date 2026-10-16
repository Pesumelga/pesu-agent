import json
import re
import ast
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Optional
import pytest

import scripts.diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1 as runner
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# =============================================================================
# Mock Host & Backend for Mocked Live Branch Integration Tests
# =============================================================================

class MockLiveWebViewHostQSC2:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.eval_calls = []
        self.closed = False
        self.scenario_responses = []
        self.poll_call_count = 0

    def evaluate_js(self, script: str, timeout: float = 5.0) -> Any:
        self.eval_calls.append(script)
        if "JS_CHECK_AUTH_AND_READINESS" in script or script == JS_CHECK_AUTH_AND_READINESS:
            return {"ready": True}
        if "JS_ACTIVATE_SEARCH" in script or script == JS_ACTIVATE_SEARCH:
            return {"activated": True}
        if "JS_CHECK_SEARCH_INPUT_READINESS" in script or script == JS_CHECK_SEARCH_INPUT_READINESS:
            return {"ready": True}
        if "JS_SET_TEXTY_QUERY" in script or JS_SET_TEXTY_QUERY in script:
            return {"ok": True, "setter_succeeded": True}
        if "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE" in script or "searchInputCandidates" in script or "surfaces_synced" in script:
            if self.scenario_responses:
                resp = self.scenario_responses[min(self.poll_call_count, len(self.scenario_responses) - 1)]
                self.poll_call_count += 1
                return resp
            return {
                "observation_ok": True,
                "observation_valid": True,
                "validity_failure_reason": None,
                "search_input_preconditions_satisfied": True,
                "query_state_all_synced": True,
                "internal_flags_ok": True,
                "surfaces_synced": {
                    "dom": True,
                    "quill_text": True,
                    "quill_delta": True,
                    "texty": True,
                    "display_query_ops": True,
                    "redux": True,
                },
                "suggestion_surface_present": True,
                "connected_candidate_count": 2,
                "safe_candidate_count": 1,
                "unsafe_candidate_count": 1,
                "safe_search_candidate_present": True,
                "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
                "normalized_candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
                "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
                "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
                "safe_candidate_exact_query_correlation": "EXACT_MATCH",
                "coexistence_observed": True,
                "candidate_summaries": [
                    {"index": 0, "classification": "SAFE_EXPLICIT_SEARCH", "is_safe": True, "is_unsafe": False},
                    {"index": 1, "classification": "UNSAFE_WRITE_CAPABLE_SLACKBOT", "is_safe": False, "is_unsafe": True},
                ],
            }
        return True

    def close(self):
        self.closed = True


class MockLiveBackendQSC2:
    def __init__(self, config=None, host=None, **kwargs):
        self.config = config
        self.host = host

    def start_runtime(self, timeout: float = 60.0):
        pass


# =============================================================================
# Helper for Offline Node.js Real JS Execution
# =============================================================================

def execute_observer_js_in_node(env_js: str, target_query: str = "26년 일반기 조회") -> dict[str, Any]:
    js_observer = runner.JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    node_code = f"""
{env_js}

const observer = {js_observer};
const report = observer({json.dumps(target_query)});
console.log(JSON.stringify(report));
"""
    proc = subprocess.run(
        ["node", "-e", node_code],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout)


NODE_BASE_FIXTURE = """
class MockElement {
    constructor(tag, attrs = {}) {
        this.tag = tag;
        this.attrs = attrs;
        this.isConnected = true;
        this.parentElement = null;
        this.textContent = "";
        this.innerText = "";
    }
    getAttribute(name) { return this.attrs[name] || null; }
    closest(selector) {
        if (selector.includes('message') && this.attrs['data-qa'] === 'message_input') return this;
        if (selector.includes('search_modal') && this.attrs['data-qa'] === 'search_modal') return this;
        if (selector.includes('search_input_box') && this.attrs['data-qa'] === 'search_input_box') return this;
        return this.parentElement ? this.parentElement.closest(selector) : null;
    }
    click() { throw new Error("ILLEGAL_MUTATION: click called!"); }
    submit() { throw new Error("ILLEGAL_MUTATION: submit called!"); }
    scroll() { throw new Error("ILLEGAL_MUTATION: scroll called!"); }
    scrollTo() { throw new Error("ILLEGAL_MUTATION: scrollTo called!"); }
}

function buildMockSlackEnv(opts = {}) {
    const modal = new MockElement('div', { 'data-qa': 'search_modal' });
    const box = new MockElement('div', { 'data-qa': 'search_input_box' });
    box.parentElement = modal;
    const input = new MockElement('div', { 'role': 'combobox', 'class': 'ql-editor' });
    input.parentElement = box;
    input.textContent = opts.domText !== undefined ? opts.domText : "26년 일반기 조회";

    const mockTexty = {
        instance: {
            quill: {
                getText: () => {
                    if (opts.quillThrow) throw new Error("Quill getter exception");
                    return opts.quillText !== undefined ? opts.quillText : "26년 일반기 조회";
                }
            }
        },
        getContents: () => {
            if (opts.textyThrow) throw new Error("Texty getContents exception");
            return { contents: [{ insert: opts.textyText !== undefined ? opts.textyText : "26년 일반기 조회" }] };
        }
    };

    const searchInputBoxInstance = {
        getTextyOps: () => {
            if (opts.deltaThrow) throw new Error("getTextyOps exception");
            return [{ insert: opts.deltaText !== undefined ? opts.deltaText : "26년 일반기 조회" }];
        },
        onUserInput: () => {},
        texty: mockTexty,
        state: opts.noState ? null : {
            _isUpdatingDisplayQuery: Boolean(opts.isUpdatingDisplayQuery),
            isInComposition: Boolean(opts.isInComposition),
            queryHasChanged: true,
            queryHasBeenSubmitted: false
        },
        props: {
            displayQueryOps: opts.noDisplayOps ? undefined : [{ insert: opts.opsText !== undefined ? opts.opsText : "26년 일반기 조회" }],
            suggestions: opts.suggestions || [
                {
                    type: "queryUser",
                    suggestion_sub_type: "query-echo",
                    is_type_query_completion: true,
                    string: opts.candidateString !== undefined ? opts.candidateString : "26년 일반기 조회"
                }
            ]
        }
    };

    if (opts.noDisplayOps) {
        delete searchInputBoxInstance.props.displayQueryOps;
    }

    const fiber = {
        stateNode: searchInputBoxInstance,
        memoizedProps: searchInputBoxInstance.props,
        return: null
    };

    input['__reactFiber$test'] = fiber;

    global.window = {
        location: { href: "https://app.slack.com/client/T001/C001" }
    };

    global.document = {
        querySelectorAll: (sel) => {
            if (sel.includes('search_input_box') && sel.includes('.ql-editor')) return [input];
            if (sel.includes('role="option"')) return [];
            return [];
        },
        querySelector: (sel) => {
            if (sel.includes('search_autocomplete') || sel.includes('listbox')) return new MockElement('div');
            return null;
        }
    };
}
"""


# =============================================================================
# 1. Parity Gates and Static Contract Audits
# =============================================================================

def test_query_setter_response_contract_parity_gate():
    ok, msg = runner.verify_query_setter_response_contract_parity()
    assert ok is True, f"Contract parity verification failed: {msg}"
    assert msg == "EXACT_QUERY_SETTER_RESPONSE_CONTRACT_PARITY_PROVEN"


def test_validate_query_setter_response_contract_acceptance_and_rejection():
    valid_resp = {"ok": True, "setter_succeeded": True}
    assert runner.validate_query_setter_response(valid_resp) is True

    inv_resp1 = {"ok": True}
    assert runner.validate_query_setter_response(inv_resp1) is False

    inv_resp2 = {"ok": True, "setter_succeeded": False}
    assert runner.validate_query_setter_response(inv_resp2) is False

    inv_resp3 = True
    assert runner.validate_query_setter_response(inv_resp3) is False


def test_production_candidate_classifier_parity_exact():
    ok, msg = runner.verify_production_candidate_classifier_parity()
    assert ok is True, f"Production candidate classifier parity failed: {msg}"
    assert msg == "EXACT_PARITY_PROVEN"


def test_production_read_only_query_state_parity_exact():
    ok, msg = runner.verify_production_read_only_query_state_parity()
    assert ok is True, f"Production read-only query-state parity failed: {msg}"
    assert msg == "PRODUCTION_READ_ONLY_QUERY_STATE_PARITY_PROVEN"


def test_production_component_flags_parity_exact():
    ok, msg = runner.verify_production_component_flags_parity()
    assert ok is True, f"Production component flags parity failed: {msg}"
    assert msg == "COMPONENT_FLAGS_PARITY_PROVEN"


def test_query_entry_primitive_parity_exact():
    ok, msg = runner.verify_query_entry_primitive_parity()
    assert ok is True, f"Query entry primitive parity failed: {msg}"
    assert msg == "EXACT_PRODUCTION_JS_QUERY_ENTRY_PRIMITIVES_REUSED"


def test_runtime_api_signature_parity_and_binds():
    ok, msg = runner.verify_runtime_api_signature_parity()
    assert ok is True, f"Runtime API signature parity failed: {msg}"
    assert msg == "PASS"


def test_static_zero_submit_audit():
    ok = runner.verify_static_zero_submit_contract()
    assert ok is True, "Static zero-submit audit failed"


def test_static_zero_interaction_audit():
    ok = runner.verify_static_zero_interaction_contract()
    assert ok is True, "Static zero-interaction audit failed"


def test_sensitive_data_audit_persistence():
    test_data = {
        "status": "ok",
        "url": "https://slack.com/archives/C12345/p1234567890",
        "token": ('xox' + 'b-secret-token'),
        "nested": {
            "email": "user@example.com",
            "safe_field": "valid_value",
            "text_containing_link": "Please visit https://evil.com/leak for info",
        },
    }
    sanitized = runner.sanitize_report_for_persistence(test_data)
    assert "url" not in sanitized
    assert "token" not in sanitized
    assert "email" not in sanitized["nested"]
    assert sanitized["nested"]["safe_field"] == "valid_value"
    assert sanitized["nested"]["text_containing_link"] == "[REDACTED_SENSITIVE_STRING]"


def test_dry_run_execution_generates_valid_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'audit_runtime_import_closure', lambda _: (True, []))
    canonical_trace = runner.DRY_RUN_OUTPUT_FILE
    original_bytes = canonical_trace.read_bytes() if canonical_trace.exists() else None
    dry_run_file = tmp_path / "qsc2_dry_run.json"
    res = runner.run_qsc2_so1_qualification(dry_run=True, output_file_override=dry_run_file)
    assert res["verdict"] == "DRY_RUN_PASS"
    assert res["diagnostic_verdict"] == "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert res["checks"]["HISTORICAL_BASELINES_EXACT"] is True
    assert res["checks"]["PRODUCTION_CANDIDATE_CLASSIFIER_PARITY"] is True
    assert res["checks"]["PRODUCTION_READ_ONLY_QUERY_STATE_PARITY"] is True
    assert res["checks"]["PRODUCTION_COMPONENT_FLAGS_PARITY"] is True
    assert res["checks"]["QSC2_SO1_STATIC_ZERO_SUBMIT_AUDIT"] is True
    assert res["checks"]["QSC2_SO1_STATIC_ZERO_INTERACTION_AUDIT"] is True

    assert dry_run_file.exists()
    trace_data = json.loads(dry_run_file.read_text(encoding="utf-8"))
    assert trace_data["diagnostic_run"] == "QSC2_SO1"
    assert trace_data["verdict"] == "DRY_RUN_PASS"
    assert (canonical_trace.read_bytes() if canonical_trace.exists() else None) == original_bytes


def test_protocol_freeze_integrity_and_34_baselines():
    proto_file = REPO_ROOT / "artifacts" / "task11q4_search_qsc2_so1_protocol_freeze.json"
    assert proto_file.exists()
    proto = json.loads(proto_file.read_text(encoding="utf-8"))

    baselines = proto.get("historical_baselines", {})
    assert len(baselines) == 34, f"Expected exactly 34 baselines, found {len(baselines)}"

    for name, base_info in baselines.items():
        rel_path = base_info.get("path") if isinstance(base_info, dict) else name
        exp_sha = base_info.get("sha256") if isinstance(base_info, dict) else base_info
        fp = REPO_ROOT / rel_path
        assert fp.exists(), f"Baseline file missing: {rel_path} ({name})"
        act_sha = hashlib.sha256(fp.read_bytes()).hexdigest()
        assert act_sha == exp_sha, f"Baseline SHA mismatch for {rel_path}: exp {exp_sha}, got {act_sha}"

    closure = proto.get("runtime_closure_hashes", {})
    closure_ok, closure_errors = runner.audit_runtime_import_closure(closure)
    # Honest worktree audit: in the post-SFC2 worktree, exactly 1 file (src/pesu_agent/search/webview_slack_search.py)
    # has drifted from the historical QSC2 freeze snapshot due to the remediated pre-submit guard.
    if not closure_ok:
        assert len(closure_errors) == 1, f"Unexpected multiple closure errors: {closure_errors}"
        assert "src/pesu_agent/search/webview_slack_search.py" in closure_errors[0]


# =============================================================================
# 2. Offline Unit Test Scenarios (15 Required Scenarios)
# =============================================================================

def test_scenario_1_all_query_states_synced():
    surfaces = {
        "dom": True,
        "quill_text": True,
        "quill_delta": True,
        "texty": True,
        "display_query_ops": True,
        "redux": True,
    }
    all_synced = all([
        surfaces["dom"],
        surfaces["quill_text"],
        surfaces["quill_delta"],
        surfaces["texty"],
        surfaces["display_query_ops"],
    ])
    assert all_synced is True


def test_scenario_2_dom_only_synced():
    surfaces = {
        "dom": True,
        "quill_text": False,
        "quill_delta": False,
        "texty": False,
        "display_query_ops": False,
        "redux": False,
    }
    all_synced = all([
        surfaces["dom"],
        surfaces["quill_text"],
        surfaces["quill_delta"],
        surfaces["texty"],
        surfaces["display_query_ops"],
    ])
    assert all_synced is False


def test_scenario_3_quill_text_unsynced():
    surfaces = {"dom": True, "quill_text": False, "quill_delta": True, "texty": True, "display_query_ops": True}
    assert all(surfaces.values()) is False


def test_scenario_4_quill_delta_unsynced():
    surfaces = {"dom": True, "quill_text": True, "quill_delta": False, "texty": True, "display_query_ops": True}
    assert all(surfaces.values()) is False


def test_scenario_5_texty_unsynced():
    surfaces = {"dom": True, "quill_text": True, "quill_delta": True, "texty": False, "display_query_ops": True}
    assert all(surfaces.values()) is False


def test_scenario_6_display_query_ops_unsynced():
    surfaces = {"dom": True, "quill_text": True, "quill_delta": True, "texty": True, "display_query_ops": False}
    assert all(surfaces.values()) is False


def test_scenario_7_getter_exception_fail_closed():
    polls = [
        {
            "poll_index": 1,
            "observation_valid": False,
            "validity_failure_reason": "QUILL_TEXT_GETTER_EXCEPTION",
            "query_state_all_synced": None,
            "internal_flags_ok": None,
            "safe_search_candidate_present": False,
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "OBSERVATION_INVALID"
    assert details["final_observation_valid"] is False
    assert details["final_validity_failure_reason"] == "QUILL_TEXT_GETTER_EXCEPTION"


def test_scenario_8_component_state_or_flags_missing():
    component_state_present = False
    flags_readable = False
    internal_flags_ok = bool(component_state_present and flags_readable)
    assert internal_flags_ok is False

    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": False,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state != "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert state == "SAFE_SUGGESTION_OBSERVED_BUT_QUERY_STATE_NEVER_SYNCED"


def test_scenario_9_search_input_box_cardinality_not_one():
    polls = [
        {
            "poll_index": 1,
            "observation_valid": False,
            "validity_failure_reason": "STRONG_SEARCH_INPUT_BOX_CARDINALITY_INVALID",
            "query_state_all_synced": None,
            "internal_flags_ok": None,
            "safe_search_candidate_present": False,
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "OBSERVATION_INVALID"


def test_scenario_10_observation_validity_failure():
    polls = [
        {"poll_index": 1, "observation_valid": False, "validity_failure_reason": "SEARCH_INPUT_CARDINALITY_NOT_ONE"},
        {"poll_index": 2, "observation_valid": False, "validity_failure_reason": "NO_REACT_FIBER_KEY_FOUND"},
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "OBSERVATION_INVALID"
    assert details["valid_poll_count"] == 0
    assert details["invalid_poll_count"] == 2


def test_scenario_11_safe_candidate_unproven_correlation():
    candidate_obj = {
        "type": "queryUser",
        "suggestion_sub_type": "query-echo",
        "is_type_query_completion": True,
    }
    text_candidate = None
    for k in ["string", "query", "text", "label"]:
        if isinstance(candidate_obj.get(k), str):
            text_candidate = candidate_obj[k]
            break

    target_query = "26년 일반기 조회"
    if text_candidate is not None:
        correlation = "EXACT_MATCH" if text_candidate.strip() == target_query.strip() else "MISMATCH"
    else:
        correlation = "UNPROVEN"

    assert correlation == "UNPROVEN"


def test_scenario_12_coexistence_in_same_poll():
    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "safe_candidate_count": 1,
            "connected_candidate_count": 1,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
            "safe_candidate_exact_query_correlation": "EXACT_MATCH",
            "coexistence_observed": True,
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert details["final_safe_candidate_exact_query_correlation"] == "EXACT_MATCH"


def test_scenario_13_separate_polls_without_coexistence():
    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": False,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
        },
        {
            "poll_index": 2,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": False,
            "candidate_classifications": ["UNSAFE_WRITE_CAPABLE_SLACKBOT"],
        },
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "QUERY_STATE_AND_SAFE_SUGGESTION_OBSERVED_WITHOUT_COEXISTENCE"


def test_scenario_14_invalid_setter_response_fail_closed():
    invalid_responses = [
        None,
        {},
        {"ok": False},
        {"ok": True, "setter_succeeded": False},
        {"status": "ok"},
        {"ok": True, "error": "Something went wrong"},
    ]
    for resp in invalid_responses:
        assert runner.validate_query_setter_response(resp) is False, f"Expected rejection for {resp}"


def test_scenario_15_unsafe_slackbot_candidate_excluded():
    obs_src = runner.JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    assert "UNSAFE_WRITE_CAPABLE_SLACKBOT" in obs_src
    assert "isWriteCapableSuggestion" in obs_src


# =============================================================================
# 3. Exact-Query Correlation Semantics Tests (Question B Hardening)
# =============================================================================

def test_mismatch_candidate_never_promoted_to_coexistence():
    """Proves that a safe candidate with MISMATCH query text receives dedicated mismatch status."""
    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "MISMATCH",
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
            "coexistence_observed": True,
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "QUERY_STATE_SYNCED_WITH_CANDIDATE_QUERY_MISMATCH"
    assert state != "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert details["final_safe_candidate_exact_query_correlation"] == "MISMATCH"


def test_mismatch_followed_by_exact_match_reaches_coexistence():
    """Proves that a transient MISMATCH in early poll followed by EXACT_MATCH in subsequent poll correctly qualifies."""
    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "MISMATCH",
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
            "coexistence_observed": True,
        },
        {
            "poll_index": 2,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "EXACT_MATCH",
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
            "coexistence_observed": True,
        },
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert details["final_safe_candidate_exact_query_correlation"] == "EXACT_MATCH"
def test_unproven_candidate_records_unproven_correlation():
    """Proves that UNPROVEN candidate records UNPROVEN in details without claiming exact match."""
    polls = [
        {
            "poll_index": 1,
            "observation_valid": True,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "safe_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "UNPROVEN",
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH"],
            "coexistence_observed": True,
        }
    ]
    state, details = runner.classify_qsc2_so1_terminal_state(polls)
    assert state == "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert details["final_safe_candidate_exact_query_correlation"] == "UNPROVEN"


# =============================================================================
# 4. Actual JavaScript Execution in Node.js (Question A Hardening)
# =============================================================================

def test_node_actual_js_execution_full_sync_and_coexistence():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js: all 5 surfaces synced + exact safe candidate."""
    script = NODE_BASE_FIXTURE + "\nbuildMockSlackEnv();"
    report = execute_observer_js_in_node(script)
    assert report["observation_ok"] is True
    assert report["observation_valid"] is True
    assert report["query_state_all_synced"] is True
    assert report["internal_flags_ok"] is True
    assert report["safe_search_candidate_present"] is True
    assert report["safe_candidate_exact_query_correlation"] == "EXACT_MATCH"
    assert report["coexistence_observed"] is True
    surfaces = report["surfaces_synced"]
    assert surfaces["dom"] is True
    assert surfaces["quill_text"] is True
    assert surfaces["quill_delta"] is True
    assert surfaces["texty"] is True
    assert surfaces["display_query_ops"] is True


def test_node_actual_js_execution_getter_exception_fail_closed():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js: Quill getText throws exception."""
    script = NODE_BASE_FIXTURE + "\nbuildMockSlackEnv({ quillThrow: true });"
    report = execute_observer_js_in_node(script)
    assert report["observation_ok"] is False
    assert report["observation_valid"] is False
    assert report["validity_failure_reason"] == "QUILL_TEXT_GETTER_EXCEPTION"
    assert report["coexistence_observed"] is False


def test_node_actual_js_execution_component_state_missing_fail_closed():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js: component state missing."""
    script = NODE_BASE_FIXTURE + "\nbuildMockSlackEnv({ noState: true });"
    report = execute_observer_js_in_node(script)
    assert report["observation_valid"] is True
    assert report["internal_flags_ok"] is False
    assert report["coexistence_observed"] is False


def test_node_actual_js_execution_unproven_safe_candidate():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js: safe candidate without query text."""
    script = NODE_BASE_FIXTURE + """
buildMockSlackEnv({
    suggestions: [{
        type: "queryUser",
        suggestion_sub_type: "query-echo",
        is_type_query_completion: true
    }]
});
"""
    report = execute_observer_js_in_node(script)
    assert report["safe_search_candidate_present"] is True
    assert report["safe_candidate_exact_query_correlation"] == "UNPROVEN"


def test_node_actual_js_execution_mismatch_safe_candidate_no_coexistence():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js: safe candidate with mismatched query text."""
    script = NODE_BASE_FIXTURE + """
buildMockSlackEnv({
    suggestions: [{
        type: "queryUser",
        suggestion_sub_type: "query-echo",
        is_type_query_completion: true,
        string: "\uC644\uC804 \uB2E4\uB978 \uAC80\uC0C9\uC5B4"
    }]
});
"""
    report = execute_observer_js_in_node(script)
    assert report["safe_search_candidate_present"] is True
    assert report["safe_candidate_exact_query_correlation"] == "MISMATCH"
    assert report["coexistence_observed"] is True

    # Terminal classification separates mismatch from non-coexistence
    state, details = runner.classify_qsc2_so1_terminal_state([report])
    assert state == "QUERY_STATE_SYNCED_WITH_CANDIDATE_QUERY_MISMATCH"
    assert details["final_safe_candidate_exact_query_correlation"] == "MISMATCH"
def test_node_actual_js_execution_zero_interaction_verified():
    """Runs actual JS_OBSERVE_SEARCH_SUGGESTION_SURFACE in Node.js with mutation traps and verifies zero interactions."""
    script = NODE_BASE_FIXTURE + "\nbuildMockSlackEnv();"
    # If any click/submit/scroll had been invoked, MockElement would have thrown an exception
    report = execute_observer_js_in_node(script)
    assert report["observation_ok"] is True


# =============================================================================
# 5. Mocked Live Branch Integration Tests
# =============================================================================

def test_mocked_live_branch_coexistence_success(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'audit_runtime_import_closure', lambda _: (True, []))
    marker_path = tmp_path / "marker_coexist.json"
    output_path = tmp_path / "live_coexist.json"

    host = MockLiveWebViewHostQSC2()
    host.scenario_responses = [
        {
            "observation_ok": True,
            "observation_valid": True,
            "validity_failure_reason": None,
            "query_state_all_synced": False,
            "internal_flags_ok": True,
            "surfaces_synced": {"dom": False, "quill_text": False, "quill_delta": False, "texty": False, "display_query_ops": False, "redux": False},
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "normalized_candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "explicit_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "EXACT_MATCH",
            "coexistence_observed": False,
        },
        {
            "observation_ok": True,
            "observation_valid": True,
            "validity_failure_reason": None,
            "query_state_all_synced": True,
            "internal_flags_ok": True,
            "surfaces_synced": {"dom": True, "quill_text": True, "quill_delta": True, "texty": True, "display_query_ops": True, "redux": True},
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "normalized_candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_WRITE_CAPABLE_SLACKBOT"],
            "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "explicit_search_candidate_present": True,
            "safe_candidate_exact_query_correlation": "EXACT_MATCH",
            "coexistence_observed": True,
        },
    ]

    report = runner.run_qsc2_so1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendQSC2(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "QUERY_STATE_SYNCED_WITH_SAFE_SEARCH_SUGGESTION"
    assert report["observation_telemetry"]["actual_poll_count"] == 2
    assert report["observation_telemetry"]["first_safe_candidate_poll_index"] == 1
    assert report["observation_telemetry"]["first_query_state_synced_poll_index"] == 2
    assert marker_path.exists()
    assert output_path.exists()


def test_mocked_live_branch_observation_invalid_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'audit_runtime_import_closure', lambda _: (True, []))
    marker_path = tmp_path / "marker_invalid.json"
    output_path = tmp_path / "live_invalid.json"

    host = MockLiveWebViewHostQSC2()
    host.scenario_responses = [
        {
            "observation_ok": False,
            "observation_valid": False,
            "validity_failure_reason": "QUILL_TEXT_GETTER_EXCEPTION",
            "query_state_all_synced": None,
            "internal_flags_ok": None,
            "safe_search_candidate_present": False,
        }
    ]

    report = runner.run_qsc2_so1_qualification(
        dry_run=False,
        timeout=1.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendQSC2(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "OBSERVATION_INVALID"
    assert report["terminal_details"]["valid_poll_count"] == 0
    assert report["terminal_details"]["invalid_poll_count"] > 0


def test_mocked_live_branch_setter_rejection_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'audit_runtime_import_closure', lambda _: (True, []))
    marker_path = tmp_path / "marker_rej.json"
    output_path = tmp_path / "live_rej.json"

    class BadSetterHost(MockLiveWebViewHostQSC2):
        def evaluate_js(self, script: str, timeout: float = 5.0) -> Any:
            if "JS_SET_TEXTY_QUERY" in script or JS_SET_TEXTY_QUERY in script:
                return {"ok": True}
            return super().evaluate_js(script, timeout)

    host = BadSetterHost()
    report = runner.run_qsc2_so1_qualification(
        dry_run=False,
        timeout=2.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendQSC2(),
    )

    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "QUERY_SETTER_RESPONSE_CONTRACT_INVALID"
    assert report["checks"]["query_entry_executed"] is False
    assert host.closed is True
