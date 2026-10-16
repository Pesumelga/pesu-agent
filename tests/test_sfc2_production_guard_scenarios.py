import json
import subprocess
from pathlib import Path
import pytest

from pesu_agent.search.webview_slack_search import JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER

NODE_MOCK_HARNESS = r"""
class MockElement {
    constructor(tag, attrs = {}) {
        this.tag = tag;
        this.attrs = attrs;
        this.isConnected = true;
        this.parentElement = null;
        this.textContent = "";
        this.innerText = "";
        this.children = [];
        this.eventListeners = {};
        this._clicked = false;
    }
    getAttribute(name) { return this.attrs[name] !== undefined ? this.attrs[name] : null; }
    hasAttribute(name) { return this.attrs[name] !== undefined; }
    setAttribute(name, val) { this.attrs[name] = val; }
    closest(selector) {
        if (selector.includes('search_modal') && this.attrs['data-qa'] === 'search_modal') return this;
        if (selector.includes('search_input_box') && this.attrs['data-qa'] === 'search_input_box') return this;
        if (selector.includes('message') && this.attrs['data-qa'] === 'message_input') return this;
        return this.parentElement ? this.parentElement.closest(selector) : null;
    }
    dispatchEvent(ev) { return true; }
    click() { this._clicked = true; }
    querySelectorAll(selector) {
        const found = [];
        const walk = (el) => {
            for (const child of el.children) {
                if (selector.includes('[role="option"]') && child.attrs['role'] === 'option') found.push(child);
                else if (selector.includes('search_input_box') && child.attrs['data-qa'] === 'search_input_box') found.push(child);
                walk(child);
            }
        };
        walk(this);
        return found;
    }
}

global.MouseEvent = class MockMouseEvent {
    constructor(type, opts = {}) {
        this.type = type;
        this.opts = opts;
    }
};

function createSlackSearchEnvironment(opts = {}) {
    const targetQuery = opts.targetQuery || "2026_ANNUAL_SEARCH_QUERY";

    const modal = new MockElement('div', { 'data-qa': 'search_modal' });
    const box = new MockElement('div', { 'data-qa': 'search_input_box' });
    box.parentElement = modal;
    modal.children.push(box);

    const input = new MockElement('div', { 'role': 'combobox', 'class': 'ql-editor' });
    input.parentElement = box;
    box.children.push(input);
    input.textContent = opts.domText !== undefined ? opts.domText : targetQuery;

    if (opts.dataIsComposing) {
        input.setAttribute('data-is-composing', 'true');
    }

    const mockTexty = {
        instance: {
            quill: {
                getText: () => {
                    if (opts.quillThrow) throw new Error("Quill getter exception");
                    return opts.quillText !== undefined ? opts.quillText : (targetQuery + "\n");
                }
            }
        },
        getContents: () => {
            if (opts.textyThrow) throw new Error("Texty getContents exception");
            return { contents: [{ insert: opts.textyText !== undefined ? opts.textyText : targetQuery }] };
        },
        isInComposition: () => Boolean(opts.textyIsInComposition)
    };

    let defaultSuggestions = [
        {
            type: "queryUser",
            suggestion_sub_type: "query-echo",
            is_type_query_completion: true,
            string: targetQuery
        }
    ];

    if (opts.candidateString !== undefined) {
        if (opts.candidateString === null) {
            delete defaultSuggestions[0].string;
        } else {
            defaultSuggestions[0].string = opts.candidateString;
        }
    }

    const inst = {
        getTextyOps: () => {
            if (opts.deltaThrow) throw new Error("getTextyOps exception");
            return [{ insert: opts.deltaText !== undefined ? opts.deltaText : targetQuery }];
        },
        onUserInput: () => {},
        texty: mockTexty,
        props: {
            displayQueryOps: opts.noDisplayOps ? undefined : [{ insert: opts.opsText !== undefined ? opts.opsText : targetQuery }],
            suggestions: opts.suggestions || defaultSuggestions,
            autocompleteSelectedIndex: opts.autocompleteSelectedIndex !== undefined ? opts.autocompleteSelectedIndex : null
        }
    };

    if (opts.noState) {
        inst.state = null;
    } else {
        inst.state = {
            queryHasChanged: opts.queryHasChanged !== undefined ? opts.queryHasChanged : true,
            queryHasBeenSubmitted: Boolean(opts.stateQueryHasBeenSubmitted)
        };
        if (opts.stateIsUpdatingDisplayQuery !== undefined) {
            inst.state._isUpdatingDisplayQuery = opts.stateIsUpdatingDisplayQuery;
        }
        if (opts.stateIsInComposition !== undefined) {
            inst.state.isInComposition = opts.stateIsInComposition;
        }
    }

    // Instance-level composition variants
    if (opts.compositionFnType === 'RETURNS_FALSE') {
        inst.isInComposition = () => false;
    } else if (opts.compositionFnType === 'RETURNS_TRUE') {
        inst.isInComposition = () => true;
    } else if (opts.compositionFnType === 'THROWS') {
        inst.isInComposition = () => { throw new Error("FAULT_INJECTION: composition function threw"); };
    } else if (opts.instanceIsInComposition !== undefined) {
        inst.isInComposition = opts.instanceIsInComposition;
    }

    // Instance-level updating variants
    if (opts.updatingFnType === 'RETURNS_FALSE') {
        inst._isUpdatingDisplayQuery = () => false;
    } else if (opts.updatingFnType === 'RETURNS_TRUE') {
        inst._isUpdatingDisplayQuery = () => true;
    } else if (opts.instanceIsUpdatingDisplayQuery !== undefined) {
        inst._isUpdatingDisplayQuery = opts.instanceIsUpdatingDisplayQuery;
    }

    if (opts.instanceQueryHasBeenSubmitted !== undefined) {
        inst.queryHasBeenSubmitted = opts.instanceQueryHasBeenSubmitted;
    }

    if (opts.throwOnUpdatingGetter) {
        Object.defineProperty(inst, '_isUpdatingDisplayQuery', {
            get() { throw new Error("FAULT_INJECTION: getter threw"); }
        });
    }

    const fiber = {
        stateNode: inst,
        memoizedProps: inst.props,
        return: null
    };

    input['__reactFiber$test'] = fiber;

    // Autocomplete DOM list
    const acList = new MockElement('div', { 'id': 'c-search_autocomplete__suggestion_list', 'role': 'listbox' });
    const suggestions = inst.props.suggestions || [];
    for (let i = 0; i < suggestions.length; i++) {
        const s = suggestions[i];
        let dIdx = String(i);
        if (opts.domOptionIndexMismatch) dIdx = String(i + 99);
        if (opts.domOptionNoIndex) dIdx = null;
        if (opts.domOptionDuplicateIndex) dIdx = '0';

        const optAttrs = {
            'role': 'option',
            'data-type': opts.domOptionTypeMismatch ? 'channel' : (s.type || '')
        };
        if (dIdx !== null) optAttrs['data-index'] = dIdx;
        if (opts.domOptionNavigational) optAttrs['data-is-navigational'] = 'true';

        const opt = new MockElement('div', optAttrs);
        if (s.is_type_slackbot || s.type === 'querySlackbot' || opts.domOptionHasBotId) {
            opt.setAttribute('data-bot-id', 'B001');
        }
        if (opts.domOptionFiberMatch) {
            opt['__reactFiber$test'] = {
                memoizedProps: { suggestion: s },
                return: null
            };
        }
        opt.parentElement = acList;
        acList.children.push(opt);
    }

    if (opts.domOptionDuplicateIndex) {
        const dupOpt = new MockElement('div', {
            'role': 'option',
            'data-index': '0',
            'data-type': 'queryUser'
        });
        dupOpt.parentElement = acList;
        acList.children.push(dupOpt);
    }

    global.window = { location: { href: "https://app.slack.com/client/T001/C001" } };
    global.document = {
        querySelectorAll: (sel) => {
            if (sel.includes('search_input_box') && sel.includes('.ql-editor')) {
                if (opts.duplicateInputs) return [input, input];
                return [input];
            }
            if (sel.includes('role="option"') || sel.includes('suggestion_item')) {
                return acList.children;
            }
            return [];
        },
        querySelector: (sel) => {
            if (sel.includes('c-search_autocomplete') || sel.includes('listbox') || sel.includes('search_autocomplete')) {
                return acList;
            }
            return null;
        }
    };

    return { input, inst, acList };
}
"""

def run_js_pre_submit(opts: dict, target_query: str = "2026_ANNUAL_SEARCH_QUERY") -> dict:
    opts_json = json.dumps(opts)
    query_json = json.dumps(target_query)
    runner_code = f"""
{NODE_MOCK_HARNESS}

const opts = {opts_json};
const env = createSlackSearchEnvironment(opts);
const fn = {JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER};
const result = fn({query_json});
result._clickedElementIndex = env.acList.children.findIndex(c => c._clicked);
console.log(JSON.stringify(result));
"""
    proc = subprocess.run(
        ["node", "-e", runner_code],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout)


# =============================================================================
# SFC2 Comprehensive Offline Test Matrix
# =============================================================================

# --- Flag & Composition Matrix (Section 2) ---

def test_sfc2_scenario_1_normal_search_both_flags_false():
    """Scenario 1: Normal search state, both flags false -> Eligible path, submitted."""
    res = run_js_pre_submit({
        "instanceIsUpdatingDisplayQuery": False,
        "instanceIsInComposition": False,
    })
    assert res.get("ok") is True, f"Expected ok: True, got {res}"
    report = res["report"]
    assert report["preconditions_satisfied"] is True
    assert report["query_state_all_synced"] is True
    assert report["internal_flags"]["is_updating_display_query"] is False
    assert report["internal_flags"]["is_in_composition"] is False
    assert report["internal_flags"]["flag_inspection_error"] is None
    assert report["submit_succeeded"] is True
    assert res["_clickedElementIndex"] == 0


def test_sfc2_scenario_2_is_in_composition_boolean_true_blocks():
    """Scenario 2: isInComposition boolean true -> Submission blocked."""
    res = run_js_pre_submit({
        "instanceIsInComposition": True,
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_in_composition"] is True
    assert res["report"]["preconditions_satisfied"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_2b_is_in_composition_function_returning_false_allows():
    """Scenario 2b: isInComposition is a function returning false -> NOT in composition, submission allowed."""
    res = run_js_pre_submit({
        "compositionFnType": "RETURNS_FALSE",
    })
    assert res.get("ok") is True, f"Expected ok: True, got {res}"
    assert res["report"]["internal_flags"]["is_in_composition"] is False
    assert res["report"]["preconditions_satisfied"] is True
    assert res["report"]["submit_succeeded"] is True


def test_sfc2_scenario_2c_is_in_composition_function_returning_true_blocks():
    """Scenario 2c: isInComposition is a function returning true -> Active composition, submission blocked."""
    res = run_js_pre_submit({
        "compositionFnType": "RETURNS_TRUE",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_in_composition"] is True
    assert res["report"]["preconditions_satisfied"] is False


def test_sfc2_scenario_2d_is_in_composition_function_throwing_fails_closed():
    """Scenario 2d: isInComposition is a function that throws -> Fails closed immediately."""
    res = run_js_pre_submit({
        "compositionFnType": "THROWS",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["flag_inspection_error"] is not None
    assert "composition function threw" in res["report"]["internal_flags"]["flag_inspection_error"]
    assert res["report"]["preconditions_satisfied"] is False


def test_sfc2_scenario_3_is_updating_display_query_true_blocks_submission():
    """Scenario 3: _isUpdatingDisplayQuery boolean true -> Submission blocked."""
    res = run_js_pre_submit({
        "instanceIsUpdatingDisplayQuery": True,
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_updating_display_query"] is True
    assert res["report"]["preconditions_satisfied"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_3b_is_updating_display_query_function_returning_true_blocks():
    """Scenario 3b: _isUpdatingDisplayQuery is a function returning true -> Submission blocked."""
    res = run_js_pre_submit({
        "updatingFnType": "RETURNS_TRUE",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_updating_display_query"] is True


def test_sfc2_scenario_3c_is_updating_display_query_function_returning_false_allows():
    """Scenario 3c: _isUpdatingDisplayQuery is a function returning false -> Submission allowed."""
    res = run_js_pre_submit({
        "updatingFnType": "RETURNS_FALSE",
    })
    assert res.get("ok") is True
    assert res["report"]["internal_flags"]["is_updating_display_query"] is False
    assert res["report"]["submit_succeeded"] is True


def test_sfc2_scenario_4_state_missing_safely_permitted_under_settled_contract():
    """Scenario 4: Component state completely missing -> Permitted under settled contract if clean."""
    res = run_js_pre_submit({
        "noState": True,
        "instanceIsUpdatingDisplayQuery": False,
        "instanceIsInComposition": False,
    })
    assert res.get("ok") is True, f"Expected ok: True under settled contract, got {res}"
    assert res["report"]["preconditions_satisfied"] is True
    assert res["report"]["submit_succeeded"] is True
    assert res["_clickedElementIndex"] == 0


def test_sfc2_scenario_5_flag_missing_from_state_resolved_authoritatively():
    """Scenario 5: Flag missing from state object -> Resolved from instance authoritatively."""
    res = run_js_pre_submit({
        "stateIsUpdatingDisplayQuery": None,
        "stateIsInComposition": None,
        "instanceIsUpdatingDisplayQuery": False,
        "instanceIsInComposition": False,
    })
    assert res.get("ok") is True
    assert res["report"]["internal_flags"]["is_updating_display_query"] is False
    assert res["report"]["internal_flags"]["is_in_composition"] is False
    assert res["report"]["submit_succeeded"] is True


def test_sfc2_scenario_6_getter_exception_fails_closed():
    """Scenario 6: Flag getter throws exception -> Fails closed immediately."""
    res = run_js_pre_submit({
        "throwOnUpdatingGetter": True,
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["flag_inspection_error"] is not None
    assert "FAULT_INJECTION" in res["report"]["internal_flags"]["flag_inspection_error"]
    assert res["report"]["preconditions_satisfied"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_7_surface_mismatch_blocks_submission():
    """Scenario 7: One of 5 query-state surfaces mismatched -> Submission blocked."""
    res = run_js_pre_submit({
        "quillText": "DIVERGENT_TEXT",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["surfaces_synced"]["quill_text"] is False
    assert res["report"]["query_state_all_synced"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_8_partial_query_substring_fails_closed():
    """Scenario 8: Surface contains query as prefix/substring only -> Fails closed under strict normalized equality."""
    res = run_js_pre_submit({
        "domText": "2026_ANNUAL_SEARCH_QUERY_EXTRA_SUFFIX",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["surfaces_synced"]["dom"] is False
    assert res["report"]["query_state_all_synced"] is False


# --- Candidate Query Correlation Matrix (Section 1) ---

def test_sfc2_scenario_9_safe_search_echo_exact_match_identified():
    """Scenario 9: Safe search echo exact match -> Eligible candidate identified and activated."""
    res = run_js_pre_submit({
        "candidateString": "2026_ANNUAL_SEARCH_QUERY",
    })
    assert res.get("ok") is True
    assert res["report"]["search_submission_target_safe"] is True
    assert res["report"]["safe_candidates_count"] == 1
    assert res["report"]["selected_safe_candidate_index"] == 0
    assert res["_clickedElementIndex"] == 0


def test_sfc2_scenario_9b_missing_candidate_query_fails_closed():
    """Scenario 9b: Candidate query property completely missing -> Fails closed."""
    res = run_js_pre_submit({
        "candidateString": None,  # Will delete string property
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_CANDIDATE_QUERY_MISSING_OR_INVALID"
    assert res["report"]["search_submission_target_safe"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_9c_empty_candidate_query_fails_closed():
    """Scenario 9c: Candidate query is empty or whitespace string -> Fails closed."""
    res = run_js_pre_submit({
        "candidateString": "   ",
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_CANDIDATE_QUERY_MISSING_OR_INVALID"
    assert res["report"]["search_submission_target_safe"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_9d_invalid_type_candidate_query_fails_closed():
    """Scenario 9d: Candidate query has non-string type (number) -> Fails closed."""
    res = run_js_pre_submit({
        "candidateString": 12345,
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_CANDIDATE_QUERY_MISSING_OR_INVALID"
    assert res["report"]["search_submission_target_safe"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_10_unsafe_slackbot_default_fails_closed():
    """Scenario 10: Unsafe Slackbot is default candidate and only candidate -> Fails closed."""
    res = run_js_pre_submit({
        "suggestions": [
            {
                "type": "querySlackbot",
                "is_type_slackbot": True,
                "suggestion_sub_type": "query-slackbot",
                "string": "Ask Slackbot"
            }
        ]
    })
    assert res.get("ok") is not True
    assert res.get("error") == "NO_SAFE_SEARCH_SUGGESTION_FOUND"
    assert res["report"]["default_suggestion_type"] == "WRITE_CAPABLE_SLACKBOT"
    assert res["report"]["search_submission_target_safe"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_11_slackbot_first_activates_safe_search_explicitly():
    """Scenario 11: suggestions[0]=Slackbot, suggestions[1]=Search -> index 1 explicitly clicked, never default."""
    res = run_js_pre_submit({
        "suggestions": [
            {
                "type": "querySlackbot",
                "is_type_slackbot": True,
                "suggestion_sub_type": "query-slackbot",
                "string": "Ask Slackbot"
            },
            {
                "type": "queryUser",
                "is_type_query_completion": True,
                "suggestion_sub_type": "query-echo",
                "string": "2026_ANNUAL_SEARCH_QUERY"
            }
        ]
    })
    assert res.get("ok") is True
    assert res["report"]["selected_safe_candidate_index"] == 1
    assert res["report"]["generic_default_onenter_used"] is False
    assert res["_clickedElementIndex"] == 1


def test_sfc2_scenario_12_duplicate_search_input_box_fails_closed():
    """Scenario 12: Duplicate search input box / Fiber -> Fails closed."""
    res = run_js_pre_submit({
        "duplicateInputs": True
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SEARCH_INPUT_CARDINALITY_NOT_ONE"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_13_candidate_query_mismatch_fails_closed():
    """Scenario 13: Safe candidate string does not match target query -> Fails closed."""
    res = run_js_pre_submit({
        "candidateString": "DIVERGENT_SUGGESTION_STRING"
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_CANDIDATE_QUERY_MISMATCH"
    assert res["report"]["search_submission_target_safe"] is False
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_14_dom_data_is_composing_fails_closed():
    """Scenario 14: Active DOM IME composition attribute -> Fails closed."""
    res = run_js_pre_submit({
        "dataIsComposing": True
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_in_composition"] is True
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_15_query_has_been_submitted_fails_closed():
    """Scenario 15: queryHasBeenSubmitted is true -> Fails closed."""
    res = run_js_pre_submit({
        "instanceQueryHasBeenSubmitted": True
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["query_has_been_submitted"] is True
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_16_texty_is_in_composition_method_fails_closed():
    """Scenario 16: textyObj.isInComposition() returns true -> Fails closed."""
    res = run_js_pre_submit({
        "textyIsInComposition": True
    })
    assert res.get("ok") is not True
    assert res.get("error") == "PRECONDITIONS_NOT_SATISFIED"
    assert res["report"]["internal_flags"]["is_in_composition"] is True
    assert res["_clickedElementIndex"] == -1


# --- Candidate-to-DOM Mapping Matrix (Section 4) ---

def test_sfc2_scenario_17_dom_option_index_mismatch_fails_closed():
    """Scenario 17: DOM option data-index does not match candidate safeIndex -> Fails closed."""
    res = run_js_pre_submit({
        "domOptionIndexMismatch": True,
        "autocompleteSelectedIndex": 999  # Does not match safeIndex 0
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_18_dom_option_bot_id_blocks_click():
    """Scenario 18: DOM option has data-bot-id attribute -> DOM click is blocked, and unselected fallback blocked."""
    res = run_js_pre_submit({
        "domOptionHasBotId": True,
        "autocompleteSelectedIndex": None  # Unselected, so generic onAccept is not safe
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_19_dom_option_missing_index_fails_closed():
    res = run_js_pre_submit({
        "domOptionNoIndex": True,
        "autocompleteSelectedIndex": None
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["report"]["candidate_dom_mapping_error"] == "NO_VERIFIED_DOM_OR_FIBER_MATCH"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_20_dom_option_duplicate_index_fails_closed():
    res = run_js_pre_submit({
        "domOptionDuplicateIndex": True,
        "autocompleteSelectedIndex": None
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["report"]["candidate_dom_mapping_error"] == "AMBIGUOUS_DUPLICATE_DOM_INDEX"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_21_dom_option_navigational_marker_fails_closed():
    res = run_js_pre_submit({
        "domOptionNavigational": True,
        "autocompleteSelectedIndex": None
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["report"]["candidate_dom_mapping_error"] == "NAVIGATIONAL_DOM_MARKER_DETECTED"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_22_dom_option_type_mismatch_fails_closed():
    res = run_js_pre_submit({
        "domOptionTypeMismatch": True,
        "autocompleteSelectedIndex": None
    })
    assert res.get("ok") is not True
    assert res.get("error") == "SAFE_SEARCH_ACTIVATION_FAILED"
    assert res["report"]["candidate_dom_mapping_error"] == "DOM_OPTION_TYPE_MISMATCH"
    assert res["_clickedElementIndex"] == -1


def test_sfc2_scenario_23_dom_option_fiber_match_when_index_missing_succeeds():
    res = run_js_pre_submit({
        "domOptionNoIndex": True,
        "domOptionFiberMatch": True
    })
    assert res.get("ok") is True
    assert res.get("error") is None
    assert res["_clickedElementIndex"] == 0
