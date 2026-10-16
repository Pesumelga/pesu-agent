"""Current single-shot path; real production JS and isolated authorization fixtures."""

import hashlib
import json
from pathlib import Path

import pytest

import scripts.diagnose_task11_search_submit_transition_sst4 as runner
from tests.test_task11_search_submit_transition_sst3 import ProductionJSHost, MockHostForSST3


class HeaderProductionJSHost(ProductionJSHost):
    def __init__(self, title):
        super().__init__()
        self.title = title

    def evaluate_js(self, script):
        if script == runner.JS_PROBE_POST_WAIT_SEARCH_STATE:
            assert self.submitted
            # Exercise the same no-query-in-URL, localized title route observed
            # in SST3. Reading result bodies is an error even in this fixture.
            setup = r'''
window.location.href = 'https://app.slack.com/client/T001/C001';
const header = {isConnected:true, textContent:TITLE};
const root = {isConnected:true, getBoundingClientRect:()=>({width:100,height:30}), get textContent() {throw new Error('Body read forbidden');}};
const view = {isConnected:true, querySelectorAll(selector) {
    if (selector.includes('titleContent')) return [header];
    if (selector.includes('skeleton') || selector.includes('aria-busy')) return [];
    if (selector.includes('search_result')) return [root];
    return [];
}};
const previousAll = document.querySelectorAll;
document.querySelectorAll = selector => selector === '[data-qa="search_view"]' ? [view] : previousAll(selector);
'''.replace('TITLE', json.dumps(self.title))
            script = setup + script
        return super().evaluate_js(script)


def auth_file(tmp_path, **changes):
    data = {
        'approved_git_commit': runner.get_git_commit(),
        'approved_runner_sha256': runner.get_file_sha256(Path(runner.__file__)),
        'approved_protocol_sha256': runner.get_file_sha256(runner.PROTOCOL_FREEZE_FILE),
        'authorization_scope': 'SST4_LIVE_SINGLE_SHOT',
    }
    data.update(changes)
    path = tmp_path / 'offline_auth.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


@pytest.fixture
def execute(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'verify_clean_working_tree', lambda: (True, 'CLEAN'))
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    monkeypatch.setattr(runner, 'MAX_AUTH_PREFLIGHT_POLLS', 2)
    monkeypatch.setattr(runner, 'MAX_READINESS_POLLS', 3)
    monkeypatch.setattr(runner, 'MAX_TRANSITION_OBSERVATION_POLLS', 3)
    def run(host, authorization=None):
        return runner.run_sst4_qualification(
            host_factory=lambda: host, marker_file_override=tmp_path / 'marker.json',
            output_file_override=tmp_path / 'trace.json',
            authorization_file_override=authorization or auth_file(tmp_path),
        )
    return run


def test_actual_production_js_full_chain_and_no_replay(execute, tmp_path):
    host = ProductionJSHost()
    result = execute(host)
    assert result['diagnostic_verdict'] == 'SEARCH_SUBMIT_TRANSITION_SUCCESS', result
    assert result['observed_actions']['query_entries_executed'] == 1
    assert result['observed_actions']['guarded_submit_attempts'] == 1
    assert result['transition_telemetry']['result_root_count'] == 0
    assert host.destroyed
    trace = (tmp_path / 'trace.json').read_bytes()
    calls = len(host.calls)
    assert execute(host)['diagnostic_verdict'] == 'LIVE_TRACE_ALREADY_EXISTS'
    assert (tmp_path / 'trace.json').read_bytes() == trace
    assert len(host.calls) == calls


@pytest.mark.parametrize('title, verdict', [
    ('다음에 대한 결과: 26년 하반기 간담회', 'SEARCH_SUBMIT_TRANSITION_SUCCESS'),
    ('다음에 대한 결과: 26년 하반기 간담회 추가', 'TRANSITION_TIMED_OUT'),
    ('다음에 대한 결과: 다른 검색어', 'TRANSITION_TIMED_OUT'),
])
def test_actual_production_js_localized_header_full_chain(execute, title, verdict):
    result = execute(HeaderProductionJSHost(title))
    assert result['diagnostic_verdict'] == verdict, result
    assert result['observed_actions']['query_entries_executed'] == 1
    assert result['observed_actions']['guarded_submit_attempts'] == 1


@pytest.mark.parametrize('changes, verdict', [
    ({'approved_git_commit': '0' * 40}, 'UNAUTHORIZED_GIT_COMMIT'),
    ({'approved_runner_sha256': '0' * 64}, 'UNAUTHORIZED_RUNNER_SHA'),
    ({'approved_protocol_sha256': '0' * 64}, 'UNAUTHORIZED_PROTOCOL_SHA'),
    ({'authorization_scope': 'SST3_LIVE_SINGLE_SHOT'}, 'UNAUTHORIZED_SCOPE'),
])
def test_exact_new_identity_required(execute, tmp_path, changes, verdict):
    host = ProductionJSHost()
    result = execute(host, auth_file(tmp_path, **changes))
    assert result['diagnostic_verdict'] == verdict
    assert not host.calls
    assert not (tmp_path / 'marker.json').exists()


def test_preflight_failure_never_consumes_attempt(execute, tmp_path):
    result = execute(MockHostForSST3(auth_ok=False))
    assert result['diagnostic_verdict'] == 'AUTH_OR_CLIENT_NOT_READY'
    assert not (tmp_path / 'marker.json').exists()
    assert not (tmp_path / 'trace.json').exists()


def test_guard_failure_is_consumed_without_retry(execute, tmp_path):
    host = MockHostForSST3(submit_ok=False)
    result = execute(host)
    assert result['diagnostic_verdict'] == 'SEARCH_GUARD_BLOCKED_SUBMISSION'
    assert result['observed_actions']['guarded_submit_attempts'] == 1
    assert (tmp_path / 'marker.json').exists()
    assert execute(host)['diagnostic_verdict'] == 'LIVE_TRACE_ALREADY_EXISTS'


def test_old_attempt_and_runtime_identities_are_enforced(execute, tmp_path, monkeypatch):
    proto = json.loads(runner.PROTOCOL_FREEZE_FILE.read_text(encoding='utf-8'))
    assert len(proto['historical_baselines']) == 34
    assert len(proto['runtime_closure_hashes']) == 60
    for item in proto['previous_attempt_evidence'].values():
        assert hashlib.sha256((runner.REPO_ROOT / item['path']).read_bytes()).hexdigest() == item['sha256']
    assert runner.audit_runtime_import_closure(proto['runtime_closure_hashes']) == (True, [])
    assert runner.verify_static_safety_contracts()[0]
    proto['previous_attempt_evidence']['sst3_live_trace.json']['sha256'] = '0' * 64
    altered = tmp_path / 'altered_protocol.json'
    altered.write_text(json.dumps(proto), encoding='utf-8')
    monkeypatch.setattr(runner, 'PROTOCOL_FREEZE_FILE', altered)
    host = ProductionJSHost()
    result = execute(host)
    assert result['diagnostic_verdict'] == 'HISTORICAL_BASELINE_SHA_MISMATCH'
    assert not host.calls
