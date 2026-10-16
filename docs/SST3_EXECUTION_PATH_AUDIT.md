# SST3 execution-path audit — 2026-10-16

Scope: offline Phase 13.5D / Task 11 recovery. Initial HEAD:
`95ba4cf0f70d3e06a34f1e71c45bfa602311fbaa`.
No real Slack host, search, message/thread access, or LLM provider was invoked.
Final commit identity is reported in the delivery message (a commit cannot contain its own hash).

## JavaScript contracts

All constants below were executed by the non-dry runner against a Node DOM/React fixture reused from the existing production guard tests. This exercises actual expressions and return schemas, rather than returning runner-selected field names. It is offline evidence, not live Slack qualification.

| Constant | Expression | Actual successful return / runner gate |
| --- | --- | --- |
| `JS_CHECK_AUTH_AND_READINESS` | Already invoked IIFE; evaluate directly | `ready: true`, `auth_required: false`, `search_button_count: 1`, strict types |
| `JS_ACTIVATE_SEARCH` | Already invoked IIFE; evaluate directly once | `activated: true`, no error |
| `JS_CHECK_SEARCH_INPUT_READINESS` | Already invoked IIFE; evaluate directly | `ready: true`, `ambiguous: false`, no error; bounded observation permits delayed mount |
| `JS_SET_TEXTY_QUERY` | Function expression; invoke once with JSON-encoded query | `ok: true`, `setter_succeeded: true`; runner requires strict `ok` and no error |
| `JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT` | Function expression; invoke with query | `ready` is derived from five synced surfaces, settled flags, exact safe candidate and DOM mapping |
| `JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER` | Function expression; invoke once with query | `ok: true`, `report.submit_succeeded: true`; existing production guard revalidates atomically |
| `JS_PROBE_POST_WAIT_SEARCH_STATE` | Already invoked IIFE; evaluate directly | Mounted search view, explicit no-loading, integer zero skeletons and exact query; result count may be zero |

Production query-state surfaces are DOM text, Quill text, Quill delta, Texty contents and authoritative `displayQueryOps`. Safe query-echo classification, exact query correlation, DOM mapping and the production atomic guard remain unchanged. The SST3 readiness expression is byte-for-byte equal to SST1's; its six existing Node scenarios also passed.

## Defects and changes

- Input readiness checked nonexistent `input_ready`; corrected to production `ready`. Executing original HEAD with real production JS reproduced `SEARCH_INPUT_NOT_READY`; the corrected complete path passes.
- Legacy `authenticated`/`client_ready` fallbacks could override explicit authentication failure or accept missing/malformed production fields; removed in favor of a strict production schema.
- Activation result was ignored and input readiness was checked only once; activation now gates entry, followed by bounded read-only polling without reactivation.
- Preflight evaluation exceptions were misreported as authentication/client failure; final evaluation exceptions now retain a sanitized error and use `AUTH_PREFLIGHT_JS_ERROR`.
- The `finally` block overwrote traces even for a runner losing marker acquisition. Only the marker owner now writes, with exclusive creation. Pre-marker failures return their report without reserving the live trace path, so later readiness checks remain possible.
- Entry/submit counters were incremented after evaluation returned; now record dispatch before evaluation, retaining an attempted action if the response times out. No retry is introduced.
- Missing transition loading/skeleton fields previously defaulted to a successful settled state; missing/malformed responses now cannot establish success. Entry, submit and readiness responses also reject malformed values safely.

## Host and one-shot lifecycle

Constructor arguments bind to real `PesuWebViewHost(initial_url=..., user_data_folder=..., title=...)`. The default construction path passes `DIRECT_SLACK_PROFILE_PATH`, calls `start()`, evaluates through `evaluate_js()` and cleans up through `destroy()`. Legacy adapter fallbacks `evaluate_javascript()` / `stop()` are exercised without recursive calls. Native Windows startup/login persistence was not executed or requalified.

Read-only auth preflight precedes atomic `O_CREAT | O_EXCL` marker acquisition. Activation, query entry and submit follow acquisition. Entry and guarded submit have one call site each outside polling loops. A consumed marker remains after failure; reruns refuse before host/action execution. Concurrent marker losers and existing trace files cannot overwrite the owner's evidence. Offline test markers, authorization fixtures and traces use temporary directories only.

## Verification and repository accounting

- Final focused Python run: **82 passed** across SST3, production SFC2 guard scenarios and search safety guard tests.
- Expanded Python run before the final error-diagnostic adjustment: **143 passed, 1 failed** across SST1–SST3, SFC2, safety guard and QSC2 observer tests. The unchanged SST2 dry-run test reads its frozen approval for `e35931b807940e8dad7d8e70a6a0d2ae73183e33` and correctly refuses current HEAD as `UNAUTHORIZED_GIT_COMMIT`. Its runner/authorization/evidence were not changed to make that historical test pass.
- `node tests/test_sst1_readiness_display_query_ops.js`: **6 scenarios passed**; identical SST3 readiness expression confirmed.
- `git diff --check`: passed. Independent review found no actionable issues in the main change; final error-diagnostic adjustment was separately tested.
- All **34 historical baseline hashes**, **60 runtime closure hashes**, SST1/SST2 markers/traces and frozen qualification artifacts remain unchanged. A before/after inventory covered 781 existing artifact/output files.
- One existing QSC2 test unexpectedly regenerated its **nonfrozen, ignored dry-run** file: `output/qualification_13_5d/live_trace/task11q4_search_qsc2_so1_query_state_convergence_dry_run.json`. Its before hash was `3b6b9ccb8f55d987d74302e52c418bd971cddcfcaf4c27f6f70446e0e8b188f0`; after hash is `01de5a435002cff00d40ba8ff9c59154bd4b95810b9e3e23ab318279548feef1`. This ancillary test side effect is not committed; original bytes were not captured for restoration. No other preexisting artifact/output file changed.
- The initial 5,551 untracked files remain untouched. Commit scope: SST3 runner, SST3 tests and this audit only.
- Real `sst3_marker.json`, `sst3_authorization.json` and `sst3_live_trace.json` remain absent. Existing SST3 dry-run trace remains unchanged.

Runner SHA-256: `c3df20e5df80d2ed297e9a9d4d6ba5e792f648ae2c9a11f7ab9b2baf17664a00`.

Protocol SHA-256 (unchanged): `0d3e8c4979e7fdac486358dd4d09d271b099b238b2d207027c26e555b37c46c1`.

Recommendation: offline execution-path readiness supports one controlled SST3 attempt **after new explicit authorization bound to the final HEAD and hashes**. Current live execution is **NO-GO: authorization absent**. Do not reuse old authorization. Next milestone is the separately authorized single safe search and verified result-surface transition; Evidence → Claim → WorkItem E2E follows successful qualification and separate scope approval. No Task 12/cutover is started here.
