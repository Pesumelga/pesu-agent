const fs = require('fs');
const assert = require('assert');

// 1. Extract JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT from python runner
const rawContent = fs.readFileSync('scripts/diagnose_task11_search_submit_transition_sst1.py', 'utf-8');
const runnerContent = rawContent.replace(/\r\n/g, '\n');
const startMarker = 'JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT = r"""';
const endMarker = '"""\n\ndef get_git_commit';

const startIndex = runnerContent.indexOf(startMarker);
if (startIndex === -1) throw new Error('Start marker not found');
const jsStart = startIndex + startMarker.length;
const jsEnd = runnerContent.indexOf(endMarker, jsStart);
if (jsEnd === -1) throw new Error('End marker not found');

const probeCode = runnerContent.slice(jsStart, jsEnd).trim();
console.log('Extracted JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT (length: ' + probeCode.length + ')');

const targetQuery = '26년 하반기 간담회';

// Helper to construct DOM & React Fiber mock environment
function createMockEnvironment(overrides = {}) {
    const defaultSuggestions = [
        {
            type: 'queryUser',
            suggestion_sub_type: 'query-echo',
            is_type_query_completion: true,
            string: targetQuery
        }
    ];

    const suggestions = overrides.suggestions !== undefined ? overrides.suggestions : defaultSuggestions;

    const propsObj = {
        suggestions: suggestions
    };

    if (overrides.propInitialQuery !== undefined) {
        propsObj.initialQuery = overrides.propInitialQuery;
    }

    if (overrides.propDisplayQueryOps !== undefined) {
        if (overrides.propDisplayQueryOpsThrows) {
            Object.defineProperty(propsObj, 'displayQueryOps', {
                get: () => { throw new Error('displayQueryOps getter failed'); },
                enumerable: true,
                configurable: true
            });
        } else {
            propsObj.displayQueryOps = overrides.propDisplayQueryOps;
        }
    }

    const stateObj = {
        _isUpdatingDisplayQuery: overrides.isUpdating !== undefined ? overrides.isUpdating : false,
        isInComposition: overrides.stateInComp !== undefined ? overrides.stateInComp : false,
        queryHasBeenSubmitted: overrides.submitted !== undefined ? overrides.submitted : false
    };

    if (overrides.stateDisplayQuery !== undefined) {
        stateObj.displayQuery = overrides.stateDisplayQuery;
    }

    const searchInputBoxInstance = {
        getTextyOps: () => overrides.textyOps !== undefined ? overrides.textyOps : [{ insert: targetQuery }],
        onUserInput: () => {},
        texty: {
            instance: {
                quill: {
                    getText: () => overrides.quillText !== undefined ? overrides.quillText : targetQuery
                }
            },
            getContents: () => ({
                contents: overrides.textyContents !== undefined ? overrides.textyContents : [{ insert: targetQuery }]
            }),
            isInComposition: overrides.textyInComp !== undefined ? overrides.textyInComp : false
        },
        state: stateObj,
        props: propsObj
    };

    const memoizedPropsObj = Object.assign({}, propsObj);
    if (overrides.fiberDisplayQueryOps !== undefined) {
        if (overrides.fiberDisplayQueryOpsThrows) {
            Object.defineProperty(memoizedPropsObj, 'displayQueryOps', {
                get: () => { throw new Error('fiber displayQueryOps getter failed'); },
                enumerable: true,
                configurable: true
            });
        } else {
            memoizedPropsObj.displayQueryOps = overrides.fiberDisplayQueryOps;
        }
    }

    const currentFiber = {
        stateNode: searchInputBoxInstance,
        memoizedProps: memoizedPropsObj,
        return: null
    };

    const inputElem = {
        textContent: overrides.domText !== undefined ? overrides.domText : targetQuery,
        innerText: overrides.domText !== undefined ? overrides.domText : targetQuery,
        getAttribute: (attr) => (attr === 'data-is-composing' ? (overrides.domComposing ? 'true' : 'false') : null),
        parentElement: null,
        closest: () => null,
        __reactFiber$test: currentFiber
    };

    // Autocomplete DOM list
    let acOptions = [];
    if (!overrides.noAcList) {
        if (overrides.acOptions !== undefined) {
            acOptions = overrides.acOptions;
        } else {
            acOptions = [
                {
                    getAttribute: (attr) => {
                        if (attr === 'data-index') return '0';
                        if (attr === 'data-is-navigational') return 'false';
                        return null;
                    },
                    hasAttribute: (attr) => (attr === 'data-bot-id' ? Boolean(overrides.domHasBot) : false)
                }
            ];
        }
    }

    const acList = overrides.noAcList ? null : {
        querySelectorAll: () => acOptions
    };

    // Global document mock
    const documentMock = {
        querySelectorAll: (sel) => {
            if (sel.includes('[data-qa="search_input_box"] .ql-editor')) {
                return overrides.noInputElem ? [] : [inputElem];
            }
            return [];
        },
        querySelector: (sel) => {
            if (sel.includes('c-search_autocomplete') || sel.includes('suggestion_list')) {
                return acList;
            }
            return null;
        }
    };

    return { document: documentMock };
}

// Scenario 1: Actual displayQueryOps present on fiber/props, state.displayQuery absent -> ALLOW
{
    const env = createMockEnvironment({
        fiberDisplayQueryOps: [{ insert: targetQuery }]
        // note: stateDisplayQuery is undefined!
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 1: Actual displayQueryOps present, state.displayQuery absent]');
    console.log('ready:', res.ready);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, true, 'Must be ready: true when displayQueryOps is valid');
    assert.strictEqual(res.surfaces_synced.display_query_ops, true, 'display_query_ops must be true');
    assert.strictEqual(res.reasons.length, 0, 'No reasons on success');
    console.log('PASS: Scenario 1 verified (ALLOW)');
}

// Scenario 2: state.displayQuery present only, displayQueryOps absent -> BLOCK
{
    const env = createMockEnvironment({
        stateDisplayQuery: targetQuery
        // note: fiberDisplayQueryOps and propDisplayQueryOps are undefined!
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 2: state.displayQuery present only, displayQueryOps absent]');
    console.log('ready:', res.ready);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, false, 'Must NOT be ready when displayQueryOps is missing');
    assert.strictEqual(res.surfaces_synced.display_query_ops, false, 'display_query_ops must be false');
    assert.ok(res.reasons.includes('SURFACES_NOT_SYNCED'), 'Must report SURFACES_NOT_SYNCED');
    console.log('PASS: Scenario 2 verified (BLOCK)');
}

// Scenario 3: initialQuery present only, displayQueryOps mismatched -> BLOCK
{
    const env = createMockEnvironment({
        propInitialQuery: targetQuery,
        fiberDisplayQueryOps: [{ insert: '다른 쿼리' }]
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 3: initialQuery present only, displayQueryOps mismatched]');
    console.log('ready:', res.ready);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, false, 'Must NOT be ready when displayQueryOps is mismatched');
    assert.strictEqual(res.surfaces_synced.display_query_ops, false, 'display_query_ops must be false');
    assert.ok(res.reasons.includes('SURFACES_NOT_SYNCED'), 'Must report SURFACES_NOT_SYNCED');
    console.log('PASS: Scenario 3 verified (BLOCK)');
}

// Scenario 4: displayQueryOps getter throws exception -> BLOCK
{
    const env = createMockEnvironment({
        fiberDisplayQueryOpsThrows: true
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 4: displayQueryOps getter throws exception]');
    console.log('ready:', res.ready);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, false, 'Must NOT be ready on getter exception');
    assert.strictEqual(res.surfaces_synced.display_query_ops, false, 'display_query_ops must be false on exception');
    assert.ok(res.reasons.includes('SURFACES_NOT_SYNCED'), 'Must report SURFACES_NOT_SYNCED');
    console.log('PASS: Scenario 4 verified (BLOCK)');
}

// Scenario 5: displayQueryOps actual Delta ops array -> ALLOW
{
    const env = createMockEnvironment({
        propDisplayQueryOps: [
            { insert: '26' },
            { insert: '년 ' },
            { insert: '하반기 ' },
            { insert: '간담회' }
        ]
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 5: displayQueryOps actual segmented Delta ops array]');
    console.log('ready:', res.ready);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, true, 'Segmented delta ops must normalize and ALLOW');
    assert.strictEqual(res.surfaces_synced.display_query_ops, true, 'display_query_ops must be true');
    console.log('PASS: Scenario 5 verified (ALLOW)');
}

// Scenario 6: displayQueryOps is valid, but other surfaces or candidate mismatch -> fail closed
{
    const env = createMockEnvironment({
        fiberDisplayQueryOps: [{ insert: targetQuery }],
        quillText: 'mismatched quill text'
    });
    global.document = env.document;
    const res = eval('(' + probeCode + ')') (targetQuery);

    console.log('\n[Scenario 6: displayQueryOps valid, but quillText mismatched]');
    console.log('ready:', res.ready);
    console.log('quill_text:', res.surfaces_synced.quill_text);
    console.log('display_query_ops:', res.surfaces_synced.display_query_ops);
    console.log('reasons:', res.reasons);

    assert.strictEqual(res.ready, false, 'Must fail closed when any surface fails');
    assert.strictEqual(res.surfaces_synced.display_query_ops, true, 'display_query_ops was true');
    assert.strictEqual(res.surfaces_synced.quill_text, false, 'quill_text must be false');
    assert.ok(res.reasons.includes('SURFACES_NOT_SYNCED'), 'Must report SURFACES_NOT_SYNCED');
    console.log('PASS: Scenario 6 verified (BLOCK)');
}

console.log('\n=============================================================');
console.log('ALL 6 PRODUCTION DISPLAYQUERYOPS SCENARIOS PASSED 100%!');
console.log('=============================================================');
