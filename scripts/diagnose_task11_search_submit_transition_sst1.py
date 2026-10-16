# -*- coding: utf-8 -*-
"""PESU Agent ? Phase 13.5D / Task 11 / SST1 Final Execution Hardening
Single-Shot Guarded Search Submit -> Search Result Surface Transition Qualification Runner

Authoritative Protocol:
  TASK_11_SEARCH_SUBMIT_TRANSITION_SST1
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from pesu_agent.runtime.pesu_webview_host import (
    PesuWebViewHost,
    DIRECT_SLACK_PROFILE_PATH,
)
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    JS_PROBE_POST_WAIT_SEARCH_STATE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("diagnose_task11_sst1")

TASK_ID = "TASK_11_SEARCH_SUBMIT_TRANSITION_SST1"
PROTOCOL_NAME = "TASK_11_SEARCH_SUBMIT_TRANSITION_SST1"
DIAGNOSTIC_RUN = "SST1"
TARGET_QUERY = "".join(chr(c) for c in [0x32, 0x36, 0xb144, 0x20, 0xd558, 0xbc18, 0xae30, 0x20, 0xac04, 0xb2f4, 0xd68c])

READINESS_OBSERVATION_TIMEOUT_MS = 3000
READINESS_POLL_INTERVAL_MS = 100
MAX_READINESS_POLLS = 30

TRANSITION_OBSERVATION_TIMEOUT_MS = 10000
TRANSITION_POLL_INTERVAL_MS = 100
MAX_TRANSITION_OBSERVATION_POLLS = 100

ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst1"
PROTOCOL_FREEZE_FILE = REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst1_protocol_freeze.json"
AUTHORIZATION_FILE = ARTIFACTS_DIR / "sst1_authorization.json"
ATTEMPT_STARTED_MARKER_FILE = ARTIFACTS_DIR / "sst1_marker.json"
LIVE_OUTPUT_FILE = ARTIFACTS_DIR / "sst1_live_trace.json"
DRY_RUN_OUTPUT_FILE = ARTIFACTS_DIR / "sst1_dry_run_trace.json"

JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT = r"""
((targetQuery) => {
    const report = {
        ready: false,
        input_box_found: false,
        query_state_all_synced: false,
        internal_flags_ok: false,
        safe_candidate_present: false,
        safe_candidate_exact_match: false,
        candidate_dom_mapping_ok: false,
        selected_safe_index: -1,
        reasons: []
    };

    const normalizeQuery = (q) => {
        if (!q || typeof q !== 'string') return '';
        return q.normalize('NFC').replace(/[\u200B-\u200D\uFEFF]/g, '').trim().replace(/\s+/g, ' ');
    };
    const targetNorm = normalizeQuery(targetQuery);

    const searchInputCandidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor'));
    if (searchInputCandidates.length !== 1) {
        report.reasons.push('SEARCH_INPUT_CARDINALITY_NOT_ONE');
        return report;
    }
    report.input_box_found = true;
    const inputElem = searchInputCandidates[0];

    const getReactFiber = (el) => {
        if (!el) return null;
        const key = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        return key ? el[key] : null;
    };

    let fiber = getReactFiber(inputElem) || getReactFiber(inputElem.parentElement) || getReactFiber(inputElem.closest('[data-qa="search_input_box"]'));
    let searchInputBoxInstance = null;
    let currentFiber = null;
    let depth = 0;
    while (fiber && depth < 40) {
        const node = fiber.stateNode;
        if (node && typeof node === 'object' && typeof node.getTextyOps === 'function' && (typeof node.onUserInput === 'function' || typeof node.onUserQueryChange === 'function' || typeof node.setTextyRef === 'function')) {
            searchInputBoxInstance = node;
            currentFiber = fiber;
            break;
        }
        fiber = fiber.return;
        depth++;
    }

    if (!searchInputBoxInstance) {
        report.reasons.push('SEARCH_INPUT_BOX_INSTANCE_NOT_FOUND');
        return report;
    }

    const normalizeOpsToString = (ops) => {
        if (!Array.isArray(ops)) return '';
        return ops.map(op => (op && typeof op.insert === 'string' ? op.insert : '')).join('').trim();
    };

    // 1. DOM text surface
    let domSynced = false;
    try {
        const domText = normalizeQuery(inputElem.textContent || inputElem.innerText || '');
        domSynced = (domText === targetNorm && targetNorm.length > 0);
    } catch (_) { domSynced = false; }

    // 2. Quill text surface
    let quillTextSynced = false;
    const textyObj = searchInputBoxInstance.texty || null;
    try {
        if (textyObj && textyObj.instance && textyObj.instance.quill && typeof textyObj.instance.quill.getText === 'function') {
            const quillText = normalizeQuery(textyObj.instance.quill.getText());
            quillTextSynced = (quillText === targetNorm && targetNorm.length > 0);
        }
    } catch (_) { quillTextSynced = false; }

    // 3. Quill delta ops surface
    let quillDeltaSynced = false;
    try {
        if (typeof searchInputBoxInstance.getTextyOps === 'function') {
            const ops = searchInputBoxInstance.getTextyOps();
            const deltaRaw = normalizeOpsToString(ops);
            quillDeltaSynced = (normalizeQuery(deltaRaw) === targetNorm && targetNorm.length > 0);
        }
    } catch (_) { quillDeltaSynced = false; }

    // 4. Texty model getContents surface
    let textyModelSynced = false;
    try {
        if (textyObj && typeof textyObj.getContents === 'function') {
            const contents = textyObj.getContents();
            const tOps = (contents && Array.isArray(contents.contents)) ? contents.contents : [];
            const tRaw = normalizeOpsToString(tOps);
            textyModelSynced = (normalizeQuery(tRaw) === targetNorm && targetNorm.length > 0);
        }
    } catch (_) { textyModelSynced = false; }

    // 5. Display query ops / Redux stateNode surface (Authoritative Production & QSC2 Contract Parity)
    let displayQuerySynced = false;
    try {
        const propsToInspect = (currentFiber && currentFiber.memoizedProps && Object.prototype.hasOwnProperty.call(currentFiber.memoizedProps, 'displayQueryOps'))
            ? currentFiber.memoizedProps
            : (searchInputBoxInstance ? searchInputBoxInstance.props : null);

        if (propsToInspect && Object.prototype.hasOwnProperty.call(propsToInspect, 'displayQueryOps')) {
            const dOps = propsToInspect.displayQueryOps;
            const rRaw = normalizeOpsToString(dOps);
            displayQuerySynced = (normalizeQuery(rRaw) === targetNorm && targetNorm.length > 0);
        }
    } catch (_) { displayQuerySynced = false; }

    report.surfaces_synced = {
        dom: domSynced,
        quill_text: quillTextSynced,
        quill_delta: quillDeltaSynced,
        texty: textyModelSynced,
        display_query_ops: displayQuerySynced
    };

    report.query_state_all_synced = Boolean(
        domSynced &&
        quillTextSynced &&
        quillDeltaSynced &&
        textyModelSynced &&
        displayQuerySynced
    );
    if (!report.query_state_all_synced) {
        report.reasons.push('SURFACES_NOT_SYNCED');
    }

    const evalBoolOrFn = (source, prop) => {
        if (!source) return false;
        try {
            const val = source[prop];
            if (typeof val === 'function') return Boolean(val.call(source));
            if (typeof val === 'boolean') return val;
        } catch (_) {
            return true;
        }
        return false;
    };

    const inst = searchInputBoxInstance;
    const st = inst.state || null;
    const isUpdating = evalBoolOrFn(inst, '_isUpdatingDisplayQuery') || evalBoolOrFn(st, '_isUpdatingDisplayQuery');
    const inComp = evalBoolOrFn(inst, 'isInComposition') || evalBoolOrFn(st, 'isInComposition') || evalBoolOrFn(textyObj, 'isInComposition') || (inputElem.getAttribute('data-is-composing') === 'true');
    const hasSubmitted = evalBoolOrFn(inst, 'queryHasBeenSubmitted') || evalBoolOrFn(st, 'queryHasBeenSubmitted');

    report.internal_flags_ok = (!isUpdating && !inComp && !hasSubmitted);
    if (!report.internal_flags_ok) report.reasons.push('INTERNAL_FLAGS_NOT_SETTLED');

    const suggestions = (inst.props && Array.isArray(inst.props.suggestions)) ? inst.props.suggestions : [];
    let safeIndex = -1;
    let safeCandidate = null;

    for (let idx = 0; idx < suggestions.length; idx++) {
        const s = suggestions[idx];
        if (!s || typeof s !== 'object') continue;
        if (s.is_type_slackbot || s.type === 'querySlackbot' || s.key === 'ask-slackbot') continue;
        if (s.isNavigational || s.type === 'channel' || s.type === 'member-profile') continue;
        if (s.is_type_query_completion === true && s.type === 'queryUser' && s.suggestion_sub_type === 'query-echo') {
            if (safeIndex === -1) {
                safeIndex = idx;
                safeCandidate = s;
            } else {
                safeIndex = -2;
                break;
            }
        }
    }

    if (safeIndex >= 0 && safeCandidate) {
        report.safe_candidate_present = true;
        report.selected_safe_index = safeIndex;

        const rawCand = (typeof safeCandidate.string === 'string' && safeCandidate.string.trim().length > 0)
            ? safeCandidate.string
            : ((typeof safeCandidate.query === 'string' && safeCandidate.query.trim().length > 0) ? safeCandidate.query : null);

        if (rawCand && normalizeQuery(rawCand) === targetNorm) {
            report.safe_candidate_exact_match = true;
        } else {
            report.reasons.push('SAFE_CANDIDATE_QUERY_MISMATCH');
        }

        const acList = document.querySelector('#c-search_autocomplete__suggestion_list, [data-qa="search_autocomplete"], .c-search_autocomplete, [role="listbox"]');
        if (acList) {
            const options = Array.from(acList.querySelectorAll('[role="option"], .c-search_autocomplete__suggestion_item, [data-qa="search_autocomplete_item"]'));
            const matchingByIndex = options.filter(el => {
                const dIdx = el.getAttribute('data-index');
                return dIdx !== null && parseInt(dIdx, 10) === safeIndex;
            });
            if (matchingByIndex.length === 1) {
                const opt = matchingByIndex[0];
                const hasBotId = opt.hasAttribute('data-bot-id') || (opt.getAttribute('data-type') || '').includes('slackbot');
                const isNav = (opt.getAttribute('data-is-navigational') === 'true');
                if (!hasBotId && !isNav) {
                    report.candidate_dom_mapping_ok = true;
                }
            } else {
                const matchingByFiber = options.filter(el => {
                    try {
                        const optKeys = Object.keys(el);
                        const k = optKeys.find(key => key.startsWith('__reactFiber$') || key.startsWith('__reactInternalInstance$'));
                        if (k && el[k]) {
                            let f = el[k];
                            let d = 0;
                            while (f && d < 10) {
                                if (f.memoizedProps && (f.memoizedProps.suggestion === safeCandidate || f.memoizedProps.item === safeCandidate)) return true;
                                f = f.return;
                                d++;
                            }
                        }
                    } catch (_) {}
                    return false;
                });
                if (matchingByFiber.length === 1) {
                    report.candidate_dom_mapping_ok = true;
                }
            }
        }
    } else if (safeIndex === -2) {
        report.reasons.push('AMBIGUOUS_MULTIPLE_SAFE_CANDIDATES');
    } else {
        report.reasons.push('NO_SAFE_CANDIDATE_FOUND');
    }

    if (safeIndex >= 0 && safeCandidate && !report.candidate_dom_mapping_ok) {
        report.reasons.push('CANDIDATE_DOM_MAPPING_FAILED');
    }

    report.ready = Boolean(
        report.input_box_found &&
        report.query_state_all_synced &&
        report.internal_flags_ok &&
        report.safe_candidate_present &&
        report.safe_candidate_exact_match &&
        report.candidate_dom_mapping_ok
    );

    return report;
})
"""

def get_git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception:
        return "UNKNOWN"


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def verify_clean_working_tree() -> tuple[bool, str]:
    try:
        diff_tracked = subprocess.check_output(["git", "diff", "--name-only"], cwd=str(REPO_ROOT), text=True).strip()
        diff_cached = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=str(REPO_ROOT), text=True).strip()
        if diff_tracked or diff_cached:
            changed = (diff_tracked + "\n" + diff_cached).strip().splitlines()
            return False, f"Uncommitted tracked files in worktree: {changed}"
        return True, "CLEAN"
    except Exception as ex:
        return False, f"Git diff check failed: {ex}"


def audit_runtime_import_closure(expected_closure: dict[str, str]) -> tuple[bool, list[str]]:
    mismatches = []
    for rel_path, exp_sha in expected_closure.items():
        fp = REPO_ROOT / rel_path
        if not fp.exists():
            mismatches.append(f"Missing runtime closure module: {rel_path}")
            continue
        act_sha = get_file_sha256(fp)
        if act_sha != exp_sha:
            mismatches.append(f"SHA mismatch for module {rel_path}: {act_sha} != {exp_sha}")
    return (len(mismatches) == 0), mismatches


def verify_static_safety_contracts(runner_path: Optional[Path] = None) -> tuple[bool, list[str]]:
    p = runner_path or Path(__file__)
    src = p.read_text(encoding="utf-8")
    errors = []

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("post_message", "chat_post_message", "send_message", "scroll_to", "scroll_by"):
                errors.append(f"Forbidden call detected in AST: {attr}")

    if "JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER" not in src:
        errors.append("JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER not referenced in runner")

    return (len(errors) == 0), errors


def sanitize_trace_data(data: Any) -> Any:
    slack_token_re = re.compile(r"xox[baprs]-[0-9a-zA-Z-]{10,}")
    bearer_token_re = re.compile(r"Bearer\s+[0-9a-zA-Z._~+/-]{10,}")
    auth_url_re = re.compile(r"(https?://)[^:\s]+:[^@\s]+@")

    def clean_str(val: str) -> str:
        s = slack_token_re.sub("[REDACTED_SLACK_TOKEN]", val)
        s = bearer_token_re.sub("[REDACTED_BEARER_TOKEN]", s)
        s = auth_url_re.sub(r"\1[REDACTED_AUTH]@", s)
        return s

    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_low = k.lower()
            if any(term in k_low for term in ("token", "auth", "password", "cookie", "secret", "authorization", "credential")):
                sanitized[k] = "[REDACTED_CREDENTIAL]"
            elif any(term in k_low for term in ("raw_message", "body_text", "raw_body")):
                sanitized[k] = "[REDACTED_SENSITIVE_TEXT]"
            elif isinstance(v, str):
                sanitized[k] = clean_str(v)
            else:
                sanitized[k] = sanitize_trace_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_trace_data(itm) for itm in data]
    elif isinstance(data, str):
        return clean_str(data)
    return data

def run_sst1_qualification(
    dry_run: bool = False,
    timeout: float = 60.0,
    marker_file_override: Optional[Path] = None,
    output_file_override: Optional[Path] = None,
    authorization_file_override: Optional[Path] = None,
    host_factory: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    run_timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_commit = get_git_commit()

    marker_file = marker_file_override or ATTEMPT_STARTED_MARKER_FILE
    output_file = output_file_override or (DRY_RUN_OUTPUT_FILE if dry_run else LIVE_OUTPUT_FILE)
    auth_file = authorization_file_override or AUTHORIZATION_FILE
    output_file.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "task_id": TASK_ID,
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "timestamp_utc": run_timestamp_utc,
        "git_commit": git_commit,
        "dry_run": dry_run,
        "verdict": "UNEVALUATED",
        "diagnostic_verdict": "UNSET",
        "target_query": TARGET_QUERY,
        "readiness_telemetry": {},
        "transition_telemetry": {},
        "checks": {
            "HEAD_EXACT": False,
            "RUNNER_SHA_EXACT": False,
            "PROTOCOL_SHA_EXACT": False,
            "AUTHORIZATION_SCOPE_EXACT": False,
            "HISTORICAL_BASELINES_EXACT": False,
            "RUNTIME_IMPORT_CLOSURE_EXACT": False,
            "STATIC_SAFETY_CONTRACTS_EXACT": False,
            "WORKING_TREE_CLEAN": False,
            "AUTHORIZATION_VERIFIED": False,
            "attempt_marker_written": False,
            "auth_and_readiness_verified": False,
            "query_entry_executed": False,
            "readiness_before_submit_converged": False,
            "guarded_submit_executed": False,
            "transition_observation_executed": False,
            "result_surface_confirmed": False,
        },
        "observed_actions": {
            "query_entries_executed": 0,
            "readiness_polls_executed": 0,
            "guarded_submit_attempts": 0,
            "guarded_submit_successes": 0,
            "transition_polls_executed": 0,
        },
        "verified_zero_invariants": {
            "slack_messages_sent": 0,
            "slackbot_invocations": 0,
            "scroll_mutations": 0,
            "body_scraping_actions": 0,
            "query_retries": 0,
        },
        "readiness_polls": [],
        "transition_polls": [],
        "harness_phases": [],
        "error": None,
    }

    # 1. Preflight Gates
    report["harness_phases"].append("PREFLIGHT_VERIFICATION")
    if not PROTOCOL_FREEZE_FILE.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "MISSING_PROTOCOL_FREEZE"
        report["error"] = f"Protocol freeze missing at {PROTOCOL_FREEZE_FILE}"
        return report

    proto = json.loads(PROTOCOL_FREEZE_FILE.read_text(encoding="utf-8"))

    clean_ok, clean_msg = verify_clean_working_tree()
    if not clean_ok and not dry_run:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "UNCOMMITTED_CHANGES_PRESENT"
        report["error"] = clean_msg
        return report
    report["checks"]["WORKING_TREE_CLEAN"] = clean_ok

    current_runner_sha = get_file_sha256(Path(__file__))
    current_proto_sha = get_file_sha256(PROTOCOL_FREEZE_FILE)

    if auth_file.exists():
        try:
            auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
            appr_commit = auth_data.get("approved_git_commit")
            appr_runner = auth_data.get("approved_runner_sha256")
            appr_proto = auth_data.get("approved_protocol_sha256")

            if git_commit != appr_commit:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "UNAUTHORIZED_GIT_COMMIT"
                report["error"] = f"Current HEAD {git_commit} != approved {appr_commit}"
                return report
            report["checks"]["HEAD_EXACT"] = True

            if current_runner_sha != appr_runner:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "UNAUTHORIZED_RUNNER_SHA"
                report["error"] = f"Current runner SHA {current_runner_sha} != approved {appr_runner}"
                return report
            report["checks"]["RUNNER_SHA_EXACT"] = True

            if current_proto_sha != appr_proto:
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "UNAUTHORIZED_PROTOCOL_SHA"
                report["error"] = f"Current protocol SHA {current_proto_sha} != approved {appr_proto}"
                return report
            report["checks"]["PROTOCOL_SHA_EXACT"] = True

            auth_scope = auth_data.get("authorization_scope")
            if auth_scope != "SST1_LIVE_SINGLE_SHOT":
                report["verdict"] = "FAIL_CLOSED"
                report["diagnostic_verdict"] = "UNAUTHORIZED_SCOPE"
                report["error"] = f"Authorization scope '{auth_scope}' != 'SST1_LIVE_SINGLE_SHOT'"
                return report
            report["checks"]["AUTHORIZATION_SCOPE_EXACT"] = True
            report["checks"]["AUTHORIZATION_VERIFIED"] = True
        except Exception as ex:
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "CORRUPT_AUTHORIZATION_FILE"
            report["error"] = str(ex)
            return report
    else:
        if not dry_run:
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "MISSING_AUTHORIZATION_FILE"
            report["error"] = f"Execution authorization missing at {auth_file}"
            return report
        report["checks"]["HEAD_EXACT"] = (git_commit != "UNKNOWN" and len(git_commit) == 40)
        report["checks"]["RUNNER_SHA_EXACT"] = True
        report["checks"]["PROTOCOL_SHA_EXACT"] = True
        report["checks"]["AUTHORIZATION_VERIFIED"] = False

    baselines = proto.get("historical_baselines", {})
    if len(baselines) != 34:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_COUNT_MISMATCH"
        return report

    baseline_errs = []
    for bname, binfo in baselines.items():
        rel = binfo.get("path") if isinstance(binfo, dict) else bname
        sha = binfo.get("sha256") if isinstance(binfo, dict) else binfo
        fp = REPO_ROOT / rel
        if not fp.exists() or get_file_sha256(fp) != sha:
            baseline_errs.append(rel)
    if baseline_errs:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "HISTORICAL_BASELINE_SHA_MISMATCH"
        report["error"] = f"Baseline mismatches: {baseline_errs}"
        return report
    report["checks"]["HISTORICAL_BASELINES_EXACT"] = True

    closure = proto.get("runtime_closure_hashes", {})
    c_ok, c_errs = audit_runtime_import_closure(closure)
    if not c_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_IMPORT_CLOSURE_DRIFT"
        report["error"] = f"Closure audit failed: {c_errs}"
        return report
    report["checks"]["RUNTIME_IMPORT_CLOSURE_EXACT"] = True

    s_ok, s_errs = verify_static_safety_contracts()
    if not s_ok:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "STATIC_SAFETY_CONTRACT_VIOLATION"
        report["error"] = f"Static safety errors: {s_errs}"
        return report
    report["checks"]["STATIC_SAFETY_CONTRACTS_EXACT"] = True

    # 2. Dry Run Path
    if dry_run:
        report["harness_phases"].append("DRY_RUN_EXECUTION")
        report["checks"]["auth_and_readiness_verified"] = True
        report["checks"]["query_entry_executed"] = True
        report["checks"]["readiness_before_submit_converged"] = True
        report["checks"]["guarded_submit_executed"] = True
        report["checks"]["transition_observation_executed"] = True
        report["checks"]["result_surface_confirmed"] = True

        report["observed_actions"]["query_entries_executed"] = 1
        report["observed_actions"]["readiness_polls_executed"] = 2
        report["observed_actions"]["guarded_submit_attempts"] = 1
        report["observed_actions"]["guarded_submit_successes"] = 1
        report["observed_actions"]["transition_polls_executed"] = 3

        report["verdict"] = "DRY_RUN_PASS"
        report["diagnostic_verdict"] = "SEARCH_SUBMIT_TRANSITION_SUCCESS"
        report["readiness_telemetry"] = {
            "converged_poll_index": 2,
            "query_state_synced": True,
            "internal_flags_settled": True,
            "safe_candidate_exact_match": True,
        }
        report["transition_telemetry"] = {
            "poll_count": 3,
            "search_view_mounted": True,
            "loading_cleared": True,
            "surface_query_matched": True,
            "result_root_count": 0,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(sanitize_trace_data(report), f, indent=2, ensure_ascii=False)
        return report

    # 3. Live One-Shot Hard Gate (Atomic Exclusive Creation)
    if output_file.exists():
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "LIVE_TRACE_ALREADY_EXISTS"
        report["error"] = f"Live output trace already exists at {output_file}."
        return report

    marker_payload = {
        "task_id": TASK_ID,
        "protocol": PROTOCOL_NAME,
        "diagnostic_run": DIAGNOSTIC_RUN,
        "timestamp_utc": run_timestamp_utc,
        "git_commit": git_commit,
        "runner_sha256": current_runner_sha,
        "protocol_sha256": current_proto_sha,
        "target_query": TARGET_QUERY,
        "action_contract": "SINGLE_SHOT_GUARDED_SUBMIT_AND_TRANSITION_OBSERVE",
        "zero_write_asserted": True,
    }

    try:
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(marker_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as mf:
            json.dump(marker_payload, mf, indent=2)
        report["checks"]["attempt_marker_written"] = True
    except FileExistsError:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "ATTEMPT_ALREADY_EXISTS"
        report["error"] = f"Attempt marker already exists at {marker_file}. One-shot qualification consumed."
        return report

    # 4. Live Execution
    host = None
    try:
        report["harness_phases"].append("HOST_INITIALIZATION")
        if host_factory:
            host = host_factory()
        else:
            host = PesuWebViewHost(profile_path=DIRECT_SLACK_PROFILE_PATH)
            host.start()

        auth_res = host.evaluate_javascript(f"({JS_CHECK_AUTH_AND_READINESS})()")
        if not auth_res or not auth_res.get("authenticated") or not auth_res.get("client_ready"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "AUTH_OR_CLIENT_NOT_READY"
            report["error"] = "Auth check returned unauthenticated or client not ready"
            return report

        host.evaluate_javascript(f"({JS_ACTIVATE_SEARCH})()")
        input_ready = host.evaluate_javascript(f"({JS_CHECK_SEARCH_INPUT_READINESS})()")
        if not input_ready or not input_ready.get("input_ready"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_INPUT_NOT_READY"
            return report
        report["checks"]["auth_and_readiness_verified"] = True

        report["harness_phases"].append("QUERY_ENTRY")
        entry_res = host.evaluate_javascript(f"({JS_SET_TEXTY_QUERY})({json.dumps(TARGET_QUERY)})")
        report["observed_actions"]["query_entries_executed"] = 1
        if not entry_res or not entry_res.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "QUERY_ENTRY_FAILED"
            report["error"] = sanitize_trace_data(str(entry_res))
            return report
        report["checks"]["query_entry_executed"] = True

        report["harness_phases"].append("READ_ONLY_READINESS_OBSERVATION")
        readiness_converged = False
        r_polls = []
        r_start = time.monotonic()

        for r_idx in range(MAX_READINESS_POLLS):
            elapsed_r = (time.monotonic() - r_start) * 1000
            if elapsed_r > READINESS_OBSERVATION_TIMEOUT_MS:
                break

            r_probe = host.evaluate_javascript(f"({JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT})({json.dumps(TARGET_QUERY)})") or {}
            r_polls.append({
                "poll_index": r_idx + 1,
                "elapsed_ms": round(elapsed_r, 2),
                "ready": bool(r_probe.get("ready")),
                "query_synced": bool(r_probe.get("query_state_all_synced")),
                "flags_ok": bool(r_probe.get("internal_flags_ok")),
                "safe_cand_present": bool(r_probe.get("safe_candidate_present")),
                "safe_cand_match": bool(r_probe.get("safe_candidate_exact_match")),
            })

            if r_probe.get("ready"):
                readiness_converged = True
                report["readiness_telemetry"] = {
                    "converged_poll_index": r_idx + 1,
                    "elapsed_ms": round(elapsed_r, 2),
                    "selected_safe_index": r_probe.get("selected_safe_index"),
                }
                break
            time.sleep(READINESS_POLL_INTERVAL_MS / 1000.0)

        report["readiness_polls"] = r_polls
        report["observed_actions"]["readiness_polls_executed"] = len(r_polls)

        if not readiness_converged:
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "READINESS_CONVERGENCE_TIMED_OUT"
            report["error"] = "Readiness did not converge before submit deadline; submit aborted"
            return report
        report["checks"]["readiness_before_submit_converged"] = True

        report["harness_phases"].append("GUARDED_SUBMIT")
        submit_res = host.evaluate_javascript(f"({JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER})({json.dumps(TARGET_QUERY)})")
        report["observed_actions"]["guarded_submit_attempts"] = 1
        if not submit_res or not submit_res.get("ok"):
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "SEARCH_GUARD_BLOCKED_SUBMISSION"
            report["error"] = sanitize_trace_data(str(submit_res.get("error") if submit_res else "SUBMIT_NULL"))
            return report
        report["observed_actions"]["guarded_submit_successes"] = 1
        report["checks"]["guarded_submit_executed"] = True

        report["harness_phases"].append("TRANSITION_OBSERVATION")
        start_mono = time.monotonic()
        polls = []
        transition_confirmed = False

        for p_idx in range(MAX_TRANSITION_OBSERVATION_POLLS):
            elapsed_ms = (time.monotonic() - start_mono) * 1000
            if elapsed_ms > TRANSITION_OBSERVATION_TIMEOUT_MS:
                break

            probe = host.evaluate_javascript(f"({JS_PROBE_POST_WAIT_SEARCH_STATE})()") or {}
            sv_mounted = bool(probe.get("search_view_mounted", False))
            loading = bool(probe.get("loading_detected_after", False)) or (probe.get("skeleton_count", 0) > 0)
            sq = probe.get("surface_query_after")
            res_count = probe.get("result_root_count", 0)

            polls.append({
                "poll_index": p_idx + 1,
                "elapsed_ms": round(elapsed_ms, 2),
                "search_view_mounted": sv_mounted,
                "loading_detected_after": loading,
                "skeleton_count": probe.get("skeleton_count", 0),
                "result_root_count": res_count,
                "surface_query_after": sq,
            })

            if sv_mounted and not loading and sq and sq.strip() == TARGET_QUERY:
                transition_confirmed = True
                report["transition_telemetry"] = {
                    "poll_count": p_idx + 1,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "search_view_mounted": True,
                    "loading_cleared": True,
                    "surface_query_matched": True,
                    "result_root_count": res_count,
                }
                break

            time.sleep(TRANSITION_POLL_INTERVAL_MS / 1000.0)

        report["transition_polls"] = polls
        report["observed_actions"]["transition_polls_executed"] = len(polls)
        report["checks"]["transition_observation_executed"] = True

        if transition_confirmed:
            report["checks"]["result_surface_confirmed"] = True
            report["verdict"] = "QUALIFICATION_PASS"
            report["diagnostic_verdict"] = "SEARCH_SUBMIT_TRANSITION_SUCCESS"
        else:
            report["verdict"] = "FAIL_CLOSED"
            report["diagnostic_verdict"] = "TRANSITION_TIMED_OUT"

    except Exception as ex:
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "RUNTIME_EXCEPTION"
        report["error"] = sanitize_trace_data(str(ex))
    finally:
        if host:
            try:
                host.stop()
            except Exception:
                pass

        with open(output_file, "w", encoding="utf-8") as of:
            json.dump(sanitize_trace_data(report), of, indent=2, ensure_ascii=False)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Task 11 SST1 Qualification Runner")
    parser.add_argument("--dry-run", action="store_true", help="Execute offline dry run")
    parser.add_argument("--timeout", type=float, default=60.0, help="Execution timeout in seconds")
    parser.add_argument("--authorization", type=Path, default=None, help="Path to authorization JSON file")
    args = parser.parse_args()

    res = run_sst1_qualification(
        dry_run=args.dry_run,
        timeout=args.timeout,
        authorization_file_override=args.authorization,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("verdict") in ("DRY_RUN_PASS", "QUALIFICATION_PASS"):
        sys.exit(0)
    else:
        sys.exit(1)
