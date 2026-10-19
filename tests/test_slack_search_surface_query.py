"""Execute the production observer against search-header DOM fixtures; no browser/network."""

import json
import subprocess

import pytest

from pesu_agent.search.webview_slack_search import JS_PROBE_POST_WAIT_SEARCH_STATE


QUERY = "26년 하반기 간담회"
# Only the permitted header field captured in SST3 is replayed. Canonical
# artifacts stay out of test execution/output and are hash-audited separately.
SST3_HEADER = '다음에 대한 결과: 26년 하반기 간담회'


def observe(*, headers=(), query_fields=(), url_query=None, input_text=QUERY,
            views=1, loading=False, outside_header=None, script=JS_PROBE_POST_WAIT_SEARCH_STATE, body=None):
    setup = {"headers": headers, "queryFields": query_fields, "urlQuery": url_query,
             "input": input_text, "views": views, "loading": loading, "outsideHeader": outside_header, "body": body}
    source = r'''
const o = SETUP;
const element = text => ({isConnected:true, textContent:text, getBoundingClientRect:()=>({width:100,height:30})});
const selectHeaders = selector => {
  let found = [];
  if (selector.includes('search_header_query') || selector.includes('search_query')) found = found.concat(o.queryFields.map(element));
  if (selector.includes('titleContent') || selector.includes('search_header__title')) found = found.concat(o.headers.map(element));
  return found;
};
const root = element('');
if (o.body !== null) {
  const fragment = {...element(o.body), closest:()=>null};
  const container = {...element(''), querySelector:()=>null,
    querySelectorAll:selector=>selector === '.p-rich_text_section' ? [fragment] : []};
  root.querySelectorAll = selector=>selector === '.c-search_message__content' ? [container] : [];
}
const view = {isConnected:true, querySelectorAll(selector) {
  if (selector.includes('skeleton') || selector.includes('aria-busy')) return o.loading ? [element('')] : [];
  if (selector.includes('search_result')) return [root];
  return selectHeaders(selector);
}};
global.window = {location:{href:'https://app.slack.com/client/T001/C001' + (o.urlQuery === null ? '' : '?q='+encodeURIComponent(o.urlQuery))}};
global.document = {
  querySelectorAll(selector) {
    if (selector === '[data-qa="search_view"]') return Array(o.views).fill(view);
    return selectHeaders(selector);
  },
  querySelector(selector) {
    if (selector.includes('loading_indicator')) return o.loading ? element('') : null;
    if (selector.includes('search_input_box')) return element(o.input);
    return selectHeaders(selector)[0] || (o.outsideHeader === null ? null : element(o.outsideHeader));
  }
};
console.log(JSON.stringify(eval(SCRIPT)));
'''.replace('SETUP', json.dumps(setup)).replace('SCRIPT', json.dumps(script))
    result = subprocess.run(['node', '-'], input=source, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_reproduces_consumed_sst3_header_without_rewriting_evidence():
    assert observe(headers=[SST3_HEADER])['surface_query_after'] == QUERY


@pytest.mark.parametrize('options', [
    {'headers': ['다음에 대한 결과: 다른 검색어']},
    {'headers': ['다음에 대한 결과: ' + QUERY + ' 추가']},
    {'headers': ['다음에 대한 결과: 앞 ' + QUERY]},
    {'headers': ['다음에 대한 결과:']},
    {'headers': ['다음에 대한 결과: ' + QUERY, '다음에 대한 결과: 다른 검색어']},
    {'headers': ['다음에 대한 결과: ' + QUERY], 'url_query': '다른 검색어'},
    {'headers': ['다음에 대한 결과: 다른 검색어'], 'url_query': QUERY},
    {'query_fields': [QUERY, '다른 검색어']},
    {'headers': [], 'input_text': QUERY},
    {'headers': [], 'outside_header': '다음에 대한 결과: ' + QUERY},
    {'headers': ['다음에 대한 결과: ' + QUERY], 'views': 2},
])
def test_mismatch_ambiguity_and_input_echo_cannot_establish_query(options):
    assert observe(**options)['surface_query_after'] != QUERY


@pytest.mark.parametrize('options', [
    {'headers': ['다음에 대한 결과: ' + QUERY]},
    {'query_fields': [QUERY]},
    {'url_query': QUERY},
    {'url_query': QUERY, 'headers': ['다음에 대한 결과: ' + QUERY]},
])
def test_exact_query_sources_remain_supported(options):
    assert observe(**options)['surface_query_after'] == QUERY


def test_query_only_field_preserves_literal_label_in_user_query():
    literal_query = '다음에 대한 결과: ' + QUERY
    assert observe(query_fields=[literal_query])['surface_query_after'] == literal_query
    assert observe(headers=['다음에 대한 결과: ' + literal_query])['surface_query_after'] == literal_query


def test_loading_remains_nonterminal_after_header_is_parsed():
    result = observe(headers=['다음에 대한 결과: ' + QUERY], loading=True)
    assert result['loading_detected_after'] is True
    assert result['skeleton_count'] > 0


@pytest.mark.parametrize("unconfirmed", [
    {"headers": []}, {"headers": ["26년 하반기"]},
    {"headers": ["다음에 대한 결과: " + QUERY + " 추가"]},
    {"views": 0}, {"views": 2}, {"loading": True},
])
def test_real_observer_controls_production_body_extraction(unconfirmed):
    from unittest.mock import patch
    from pesu_agent.search.webview_slack_search import WebViewSlackSearchBackend
    from tests.test_slack_webview_search_transition_forensics import ExtractionBarrierHost, _make_sample_item

    first = observe(**unconfirmed)
    confirmed = observe(headers=["다음에 대한 결과: " + QUERY])
    host = ExtractionBarrierHost([first, confirmed])
    host.input_value = QUERY
    host.current_results = [_make_sample_item(1, "C123", "1724738400.123456", "Fixture", "Synthetic body")]
    backend = WebViewSlackSearchBackend(host=host, search_transition_timeout_sec=.03,
                                        search_transition_poll_interval_sec=.01)
    with patch("time.sleep", return_value=None):
        results = backend.search_messages(QUERY, max_results=5)
    assert len(results) == 1
    assert host.extraction_states == [confirmed]


@pytest.mark.parametrize("options", [
    {"headers": ["another query"]}, {"loading": True}, {"views": 2},
])
def test_extractor_rechecks_current_surface_before_touching_body(options):
    from pesu_agent.search.webview_slack_search import JS_EXTRACT_SEARCH_RESULTS
    args = [5, ".c-search_message__content", ".p-rich_text_section",
            '[data-qa="message_sender_name"]', ".c-timestamp", QUERY]
    script = "(" + JS_EXTRACT_SEARCH_RESULTS + ")(" + ", ".join(json.dumps(arg) for arg in args) + ")"
    # The fixture result roots have no body APIs: touching them would throw.
    state = observe(script=script, **options)
    assert state.get("error") == "SEARCH_SURFACE_NOT_CONFIRMED"
    assert state.get("items", []) == []


def test_extractor_accepts_confirmed_surface_and_reads_scoped_body():
    from pesu_agent.search.webview_slack_search import JS_EXTRACT_SEARCH_RESULTS
    args = [5, ".c-search_message__content", ".p-rich_text_section",
            '[data-qa="message_sender_name"]', ".c-timestamp", QUERY]
    script = "(" + JS_EXTRACT_SEARCH_RESULTS + ")(" + ", ".join(json.dumps(arg) for arg in args) + ")"
    result = observe(headers=["다음에 대한 결과: " + QUERY], body="Synthetic source body.", script=script)
    assert result["ok"] is True
    assert result["items"][0]["body_valid"] is True
    assert result["items"][0]["normalized_body"] == "Synthetic source body."
