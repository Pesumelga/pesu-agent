/**
 * PESU Agent Product UI Client Logic (Phase 10.4).
 *
 * Implements strict Untrusted Content Hard Gate:
 * - MODEL_TEXT_EXECUTABLE_AS_HTML = false
 * - SLACK_TEXT_EXECUTABLE_AS_HTML = false
 * Zero innerHTML or insertAdjacentHTML used for dynamic text.
 * Uses textContent, createElement, and appendChild throughout.
 *
 * Request Isolation:
 * - STALE_RESULT_CAN_OVERWRITE_CURRENT_QUERY = false
 * Drops any incoming messages whose request_id does not match the active query.
 */

(function () {
  'use strict';

  const PROTOCOL_VERSION = 1;
  let isBridgeReady = false;
  let currentRequestId = null;

  // DOM Elements
  const queryInput = document.getElementById('query-input');
  const submitBtn = document.getElementById('submit-btn');
  const btnText = document.getElementById('btn-text');
  const statusDot = document.getElementById('status-dot');
  const statusText = document.getElementById('status-text');
  const resultCard = document.getElementById('result-card');
  const evidenceCard = document.getElementById('evidence-card');
  const evidenceSection = document.getElementById('evidence-section');
  const consentModal = document.getElementById('consent-modal');
  const consentGrantBtn = document.getElementById('consent-grant-btn');
  const consentCancelBtn = document.getElementById('consent-cancel-btn');
  const providerBadge = document.getElementById('provider-badge');
  const apiKeyModal = document.getElementById('api-key-modal');
  const apiKeyInput = document.getElementById('api-key-input');
  const apiKeyError = document.getElementById('api-key-error');
  const apiKeySaveBtn = document.getElementById('api-key-save-btn');
  const apiKeyCancelBtn = document.getElementById('api-key-cancel-btn');
  const apiKeyRemoveBtn = document.getElementById('api-key-remove-btn');
  const slackCapabilityBadge = document.getElementById('slack-capability-badge');
  const headerSettingsBtn = document.getElementById('header-settings-btn');
  const settingsModal = document.getElementById('settings-modal');
  const settingsCloseBtn = document.getElementById('settings-close-btn');
  const settingsCloseXBtn = document.getElementById('settings-close-x-btn');
  const settingsSlackStatus = document.getElementById('settings-slack-status');
  const settingsSlackLoginBtn = document.getElementById('settings-slack-login-btn');
  const settingsAiStatus = document.getElementById('settings-ai-status');
  const settingsAiSetupBtn = document.getElementById('settings-ai-setup-btn');
  const settingsAiRemoveBtn = document.getElementById('settings-ai-remove-btn');
  const settingsAiDesc = document.getElementById('settings-ai-desc');
  const settingsConsentStatus = document.getElementById('settings-consent-status');
  const settingsReopenOnboardingBtn = document.getElementById('settings-reopen-onboarding-btn');
  const settingsAutostartStatus = document.getElementById('settings-autostart-status');
  const settingsAutostartToggleBtn = document.getElementById('settings-autostart-toggle-btn');
  const settingsAutostartDesc = document.getElementById('settings-autostart-desc');
  let currentAutostartStatus = 'DISABLED';
  const keyConfirmModal = document.getElementById('key-confirm-modal');
  const keyConfirmCancelBtn = document.getElementById('key-confirm-cancel-btn');
  const keyConfirmOkBtn = document.getElementById('key-confirm-ok-btn');

  // History DOM elements
  const headerHistoryBtn = document.getElementById('header-history-btn');
  const historyDrawer = document.getElementById('history-drawer');
  const historyDrawerCloseBtn = document.getElementById('history-drawer-close-btn');
  const historyDrawerBackdrop = document.getElementById('history-drawer-backdrop');
  const historyCountBadge = document.getElementById('history-count-badge');
  const historyList = document.getElementById('history-list');
  const historyEmpty = document.getElementById('history-empty');
  const historyLoadMore = document.getElementById('history-load-more');
  const historyLoadMoreBtn = document.getElementById('history-load-more-btn');
  const pastResultBanner = document.getElementById('past-result-banner');
  const pastResultDate = document.getElementById('past-result-date');
  const btnReturnComposer = document.getElementById('btn-return-composer');
  const settingsHistoryStatus = document.getElementById('settings-history-status');
  const settingsHistoryClearBtn = document.getElementById('settings-history-clear-btn');
  const historyConfirmModal = document.getElementById('history-confirm-modal');
  const historyConfirmCancelBtn = document.getElementById('history-confirm-cancel-btn');
  const historyConfirmOkBtn = document.getElementById('history-confirm-ok-btn');

  let historyOffset = 0;
  const historyLimit = 20;
  let historyTotalCount = 0;
  let isViewingHistoryRecord = false;

  // Onboarding DOM elements
  const onboardingModal = document.getElementById('onboarding-modal');
  const stepDot1 = document.getElementById('step-dot-1');
  const stepDot2 = document.getElementById('step-dot-2');
  const stepDot3 = document.getElementById('step-dot-3');
  const stepLine1 = document.getElementById('step-line-1');
  const stepLine2 = document.getElementById('step-line-2');
  const onboardingStep1 = document.getElementById('onboarding-step-1');
  const onboardingStep2 = document.getElementById('onboarding-step-2');
  const onboardingStep3 = document.getElementById('onboarding-step-3');
  const onboardingSkipBtn = document.getElementById('onboarding-skip-btn');
  const onboardingNext1Btn = document.getElementById('onboarding-next-1-btn');
  const onboardingPrev2Btn = document.getElementById('onboarding-prev-2-btn');
  const onboardingNext2Btn = document.getElementById('onboarding-next-2-btn');
  const onboardingPrev3Btn = document.getElementById('onboarding-prev-3-btn');
  const onboardingFinishBtn = document.getElementById('onboarding-finish-btn');
  const onboardingAiConfigured = document.getElementById('onboarding-ai-configured');
  const onboardingAiMissing = document.getElementById('onboarding-ai-missing');
  const onboardingAiSetupBtn = document.getElementById('onboarding-ai-setup-btn');
  const onboardingSlackLoginBtn = document.getElementById('onboarding-slack-login-btn');

  let isProviderConfigured = false;
  let currentOnboardingStep = 1;
  let currentSlackAuthStatus = 'unknown';

  /**
   * Single authoritative Slack Auth UI Renderer (Finding 2).
   * Responsible for:
   * - settings status text (settingsSlackStatus)
   * - login button text (settingsSlackLoginBtn)
   * - disabled state (settingsSlackLoginBtn.disabled)
   * - title tooltip (settingsSlackLoginBtn.title)
   * - Slack capability badge (slackCapabilityBadge)
   * - Onboarding login button sync (onboardingSlackLoginBtn)
   */
  function renderSlackAuthState(authStatus, isAuthoritative = false) {
    // Guard against stale non-authoritative events overwriting authoritative READY
    if (!isAuthoritative && currentSlackAuthStatus === 'ready') {
      console.warn(`[SLACK_AUTH] Ignoring non-authoritative transition to '${authStatus}' because app is already in authoritative READY state.`);
      return;
    }

    currentSlackAuthStatus = authStatus;

    if (authStatus === 'ready') {
      if (settingsSlackStatus) settingsSlackStatus.textContent = '준비됨 (읽기 전용)';
      if (slackCapabilityBadge) slackCapabilityBadge.textContent = 'Slack · 읽기 전용';
      if (settingsSlackLoginBtn) {
        settingsSlackLoginBtn.textContent = '로그인됨';
        settingsSlackLoginBtn.disabled = true;
        settingsSlackLoginBtn.title = '이미 로그인되어 있습니다';
      }
      if (onboardingSlackLoginBtn) {
        onboardingSlackLoginBtn.textContent = '로그인됨';
        onboardingSlackLoginBtn.disabled = true;
        onboardingSlackLoginBtn.title = '이미 로그인되어 있습니다';
      }
    } else if (authStatus === 'slack_login_in_progress') {
      if (settingsSlackStatus) settingsSlackStatus.textContent = '로그인 진행 중';
      if (slackCapabilityBadge) slackCapabilityBadge.textContent = 'Slack · 로그인 중';
      if (settingsSlackLoginBtn) {
        settingsSlackLoginBtn.textContent = '로그인 진행 중...';
        settingsSlackLoginBtn.disabled = true;
        settingsSlackLoginBtn.title = 'Slack 로그인 진행 중입니다';
      }
      if (onboardingSlackLoginBtn) {
        onboardingSlackLoginBtn.textContent = '로그인 진행 중...';
        onboardingSlackLoginBtn.disabled = true;
        onboardingSlackLoginBtn.title = 'Slack 로그인 진행 중입니다';
      }
    } else if (authStatus === 'slack_login_failed') {
      if (settingsSlackStatus) settingsSlackStatus.textContent = '로그인 필요 (다시 시도 가능)';
      if (slackCapabilityBadge) slackCapabilityBadge.textContent = 'Slack · 로그인 필요';
      if (settingsSlackLoginBtn) {
        settingsSlackLoginBtn.textContent = 'Slack 로그인';
        settingsSlackLoginBtn.disabled = false;
        settingsSlackLoginBtn.title = 'Slack 로그인을 다시 시도합니다';
      }
      if (onboardingSlackLoginBtn) {
        onboardingSlackLoginBtn.textContent = 'Slack 로그인';
        onboardingSlackLoginBtn.disabled = false;
        onboardingSlackLoginBtn.title = 'Slack 로그인을 다시 시도합니다';
      }
    } else if (authStatus === 'slack_login_required') {
      if (settingsSlackStatus) settingsSlackStatus.textContent = '로그인 필요';
      if (slackCapabilityBadge) slackCapabilityBadge.textContent = 'Slack · 로그인 필요';
      if (settingsSlackLoginBtn) {
        settingsSlackLoginBtn.textContent = 'Slack 로그인';
        settingsSlackLoginBtn.disabled = false;
        settingsSlackLoginBtn.title = 'Slack에 로그인합니다';
      }
      if (onboardingSlackLoginBtn) {
        onboardingSlackLoginBtn.textContent = 'Slack 로그인';
        onboardingSlackLoginBtn.disabled = false;
        onboardingSlackLoginBtn.title = 'Slack에 로그인합니다';
      }
    }
  }

  function showOnboardingStep(step) {
    currentOnboardingStep = step;
    if (onboardingStep1) onboardingStep1.classList.toggle('hidden', step !== 1);
    if (onboardingStep2) onboardingStep2.classList.toggle('hidden', step !== 2);
    if (onboardingStep3) onboardingStep3.classList.toggle('hidden', step !== 3);

    if (stepDot1) stepDot1.classList.toggle('active', step >= 1);
    if (stepLine1) stepLine1.classList.toggle('active', step >= 2);
    if (stepDot2) stepDot2.classList.toggle('active', step >= 2);
    if (stepLine2) stepLine2.classList.toggle('active', step >= 3);
    if (stepDot3) stepDot3.classList.toggle('active', step >= 3);

    if (step === 2) {
      updateOnboardingAiView();
    }
  }

  function updateOnboardingAiView() {
    if (onboardingAiConfigured && onboardingAiMissing) {
      if (isProviderConfigured) {
        onboardingAiConfigured.classList.remove('hidden');
        onboardingAiMissing.classList.add('hidden');
      } else {
        onboardingAiConfigured.classList.add('hidden');
        onboardingAiMissing.classList.remove('hidden');
      }
    }
  }

  function openOnboardingModal() {
    if (onboardingModal) {
      showOnboardingStep(1);
      onboardingModal.classList.remove('hidden');
    }
  }

  function closeOnboardingModal(completed = false) {
    if (onboardingModal) {
      onboardingModal.classList.add('hidden');
    }
    if (completed) {
      sendMessage('onboarding.complete', currentRequestId, {});
    }
  }

  function openSettingsModal() {
    if (settingsModal) {
      settingsModal.classList.remove('hidden');
      sendMessage('app.status.request', currentRequestId, {});
      sendMessage('startup.auto.status.request', currentRequestId, {});
      sendMessage('history.status.get', currentRequestId, {});
    }
  }

  function closeSettingsModal() {
    if (settingsModal) {
      settingsModal.classList.add('hidden');
    }
  }

  function openHistoryDrawer() {
    if (historyDrawer) {
      historyDrawer.classList.remove('hidden');
      if (historyDrawerBackdrop) historyDrawerBackdrop.classList.remove('hidden');
      historyOffset = 0;
      if (historyList) clearElement(historyList);
      if (historyEmpty) historyEmpty.classList.add('hidden');
      if (historyLoadMore) historyLoadMore.classList.add('hidden');
      sendMessage('history.list', generateRequestId(), { limit: historyLimit, offset: 0 });
    }
  }

  function closeHistoryDrawer() {
    if (historyDrawer) {
      historyDrawer.classList.add('hidden');
      if (historyDrawerBackdrop) historyDrawerBackdrop.classList.add('hidden');
    }
  }

  function formatDateTime(isoStr) {
    if (!isoStr) return '';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      const pad = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch (e) {
      return isoStr;
    }
  }

  function renderReadyPlaceholder(container) {
    clearElement(container);
    const box = document.createElement('div');
    box.id = 'result-placeholder';
    box.className = 'ready-placeholder';

    const icon = document.createElement('div');
    icon.className = 'ready-icon';
    icon.textContent = '💬';

    const title = document.createElement('h3');
    title.className = 'ready-title';
    title.textContent = '무엇을 찾아볼까요?';

    const desc = document.createElement('p');
    desc.className = 'ready-desc';
    desc.textContent = 'Slack의 업무 논의와 현황을 검색하여 근거와 함께 정리해 드립니다.';

    const suggestions = document.createElement('div');
    suggestions.className = 'ready-suggestions';

    const chip1 = document.createElement('span');
    chip1.className = 'suggestion-chip';
    chip1.textContent = '예: “지난주 프로젝트 릴리즈 논의 정리해줘”';

    const chip2 = document.createElement('span');
    chip2.className = 'suggestion-chip';
    chip2.textContent = '예: “배포 일정 관련 최근 결정 사항”';

    suggestions.appendChild(chip1);
    suggestions.appendChild(chip2);

    box.appendChild(icon);
    box.appendChild(title);
    box.appendChild(desc);
    box.appendChild(suggestions);
    container.appendChild(box);
  }

  function returnToComposer() {
    isViewingHistoryRecord = false;
    if (pastResultBanner) pastResultBanner.classList.add('hidden');
    if (queryInput) {
      queryInput.value = '';
    }
    if (resultCard) {
      renderReadyPlaceholder(resultCard);
    }
    if (evidenceSection) {
      evidenceSection.classList.add('hidden');
    }
    if (evidenceCard) {
      clearElement(evidenceCard);
    }
    statusText.textContent = '상태: 준비됨';
    statusDot.className = 'status-dot ready';
    btnText.textContent = '실행';
    updateSubmitButtonState();
    sendMessage('history.exit', generateRequestId(), {});
  }

  function renderHistoryItems(items, totalCount, offset) {
    if (!historyList) return;
    if (offset === 0) {
      clearElement(historyList);
    }

    if (!items || items.length === 0) {
      if (offset === 0 && historyEmpty) {
        historyEmpty.classList.remove('hidden');
      }
      if (historyLoadMore) historyLoadMore.classList.add('hidden');
      return;
    }

    if (historyEmpty) historyEmpty.classList.add('hidden');

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const card = document.createElement('div');
      card.className = 'history-item-card';
      card.setAttribute('data-history-id', item.id);

      // Header: Date & Status
      const header = document.createElement('div');
      header.className = 'history-item-header';

      const dateSpan = document.createElement('span');
      dateSpan.className = 'history-item-date';
      dateSpan.textContent = formatDateTime(item.created_at || item.completed_at);

      const statusBadge = document.createElement('span');
      statusBadge.className = 'history-item-status';
      statusBadge.textContent = '완료';

      header.appendChild(dateSpan);
      header.appendChild(statusBadge);
      card.appendChild(header);

      // Question preview
      const questionEl = document.createElement('div');
      questionEl.className = 'history-item-question';
      questionEl.textContent = item.question || '(질문 없음)';
      card.appendChild(questionEl);

      // Footer: Delete button
      const footer = document.createElement('div');
      footer.className = 'history-item-footer';

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'btn-history-delete';
      delBtn.textContent = '삭제';
      delBtn.onclick = function (e) {
        e.stopPropagation();
        sendMessage('history.delete', generateRequestId(), { id: item.id });
      };

      footer.appendChild(delBtn);
      card.appendChild(footer);

      // Click card to restore record
      card.onclick = function () {
        sendMessage('history.open', generateRequestId(), { id: item.id });
      };

      historyList.appendChild(card);
    }

    const currentLoadedCount = (offset || 0) + items.length;
    if (historyLoadMore) {
      if (currentLoadedCount < totalCount) {
        historyLoadMore.classList.remove('hidden');
      } else {
        historyLoadMore.classList.add('hidden');
      }
    }
  }

  function generateRequestId() {
    return 'req-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6);
  }

  function clearElement(el) {
    if (!el) return;
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  /**
   * Sends a typed JSON envelope to Python via window.chrome.webview.postMessage.
   */
  function sendMessage(type, requestId, payload) {
    if (!window.chrome || !window.chrome.webview) {
      console.warn('WebView2 environment not available.');
      return;
    }

    const envelope = {
      version: PROTOCOL_VERSION,
      type: type,
      request_id: requestId || null,
      payload: payload || {}
    };

    try {
      window.chrome.webview.postMessage(envelope);
    } catch (err) {
      console.error('Failed to postMessage to Python bridge:', err);
    }
  }

  /**
   * Safe Answer Renderer:
   * Translates plain paragraphs into <p> and <br> nodes using createTextNode.
   * Guarantees MODEL_TEXT_EXECUTABLE_AS_HTML = false.
   */
  function renderGroundedAnswer(container, payload) {
    if (!Array.isArray(payload.work_items)) {
      renderAnswer(container, payload.answer);
      return;
    }
    clearElement(container);
    const headline = document.createElement('h3');
    headline.className = 'grounded-headline';
    headline.textContent = payload.summary_headline || '근거에 따른 업무 현황';
    container.appendChild(headline);
    if (payload.content_truncated) {
      const notice = document.createElement('p');
      notice.className = 'grounded-uncertainty';
      notice.textContent = '표시 범위 제한으로 일부 업무 항목이나 근거 내용이 생략되었습니다.';
      container.appendChild(notice);
    }
    if (payload.is_conclusive === false) {
      const notice = document.createElement('p');
      notice.className = 'grounded-uncertainty';
      notice.textContent = payload.uncertainty_explanation || '현재 근거만으로는 상태를 확정할 수 없습니다.';
      container.appendChild(notice);
    }
    const statuses = {COMPLETED: '완료', IN_PROGRESS: '진행 중', BLOCKED: '차단됨', PLANNED: '계획됨', UNKNOWN: '확인 불가'};
    for (const item of payload.work_items) {
      const card = document.createElement('article');
      card.className = 'grounded-workitem';
      const status = document.createElement('span');
      status.className = 'workitem-status';
      status.textContent = statuses[item.status] || '확인 불가';
      card.appendChild(status);
      const title = document.createElement('h4');
      title.textContent = item.title || '업무 항목';
      card.appendChild(title);
      for (const [label, values] of [['판단 근거', item.decisive_claims], ['차단 요인', item.blocker_details]]) {
        if (!Array.isArray(values) || !values.length) continue;
        const heading = document.createElement('p');
        heading.className = 'workitem-section-label';
        heading.textContent = label;
        card.appendChild(heading);
        const list = document.createElement('ul');
        for (const text of values) {
          const row = document.createElement('li');
          row.textContent = text;
          list.appendChild(row);
        }
        card.appendChild(list);
      }
      if (Array.isArray(item.evidence_ids) && item.evidence_ids.length) {
        const sources = document.createElement('p');
        sources.className = 'workitem-sources';
        sources.textContent = '연결된 출처: ' + item.evidence_ids.join(', ') + ' · 아래 근거에서 확인';
        card.appendChild(sources);
      }
      if (item.omitted_evidence_count > 0) {
        const omitted = document.createElement('p');
        omitted.className = 'grounded-uncertainty';
        omitted.textContent = `이 업무의 출처 ${item.omitted_evidence_count}개는 표시 범위에서 생략됐습니다.`;
        card.appendChild(omitted);
      }
      container.appendChild(card);
    }
  }

  function renderAnswer(container, answerText) {
    clearElement(container);

    if (!answerText || typeof answerText !== 'string' || !answerText.trim()) {
      const p = document.createElement('p');
      p.className = 'placeholder-text';
      p.textContent = '조사 결과가 없습니다.';
      container.appendChild(p);
      return;
    }

    const paragraphs = answerText.split(/\n\n+/);
    for (const para of paragraphs) {
      const trimmed = para.trim();
      if (!trimmed) continue;

      const p = document.createElement('p');
      p.className = 'answer-paragraph';

      const lines = trimmed.split('\n');
      for (let i = 0; i < lines.length; i++) {
        p.appendChild(document.createTextNode(lines[i]));
        if (i < lines.length - 1) {
          p.appendChild(document.createElement('br'));
        }
      }
      container.appendChild(p);
    }
  }

  /**
   * Safe Evidence / Citation Renderer:
   * Translates evidence cards into structured DOM nodes using textContent.
   * Guarantees SLACK_TEXT_EXECUTABLE_AS_HTML = false.
   */
  function renderEvidence(container, evidenceList, noEvidenceMsg) {
    clearElement(container);

    if (!evidenceList || !Array.isArray(evidenceList) || evidenceList.length === 0) {
      const emptyBox = document.createElement('div');
      emptyBox.className = 'no-evidence-box';

      const title = document.createElement('div');
      title.className = 'no-evidence-title';
      title.textContent = noEvidenceMsg || '관련 근거를 찾지 못했습니다.';
      emptyBox.appendChild(title);

      const hint = document.createElement('div');
      hint.className = 'no-evidence-hint';
      hint.textContent = '질문을 조금 더 구체적으로 입력해 보세요.';
      emptyBox.appendChild(hint);

      container.appendChild(emptyBox);
      return;
    }

    const listContainer = document.createElement('div');
    listContainer.className = 'evidence-list';

    for (let i = 0; i < evidenceList.length; i++) {
      const item = evidenceList[i];
      const card = document.createElement('div');
      card.className = 'evidence-card-item';
      card.setAttribute('data-evidence-id', item.id || `E${i+1}`);

      // Header: [E1] Source & Author · Time
      const header = document.createElement('div');
      header.className = 'evidence-card-header';

      const badge = document.createElement('span');
      badge.className = 'evidence-badge';
      badge.textContent = `[${item.id || 'E' + (i + 1)}] ${item.source || 'Slack'}`;

      const meta = document.createElement('span');
      meta.className = 'evidence-meta';
      const authorText = item.author || '알 수 없음';
      const timeText = item.timestamp ? ` · ${item.timestamp}` : '';
      meta.textContent = `${authorText}${timeText}`;

      header.appendChild(badge);
      header.appendChild(meta);
      card.appendChild(header);

      // Body: Snippet text
      const body = document.createElement('div');
      body.className = 'evidence-snippet';
      body.textContent = item.snippet || '';
      card.appendChild(body);

      // Footer: Slack Permalink Button (if valid)
      if (item.has_valid_permalink) {
        const footer = document.createElement('div');
        footer.className = 'evidence-card-footer';

        const openBtn = document.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'btn-open-citation';
        openBtn.textContent = 'Slack에서 원문 보기';
        openBtn.onclick = function () {
          sendMessage('citation.open', generateRequestId(), {
            evidence_id: item.id
          });
        };

        footer.appendChild(openBtn);
        card.appendChild(footer);
      }

      listContainer.appendChild(card);
    }

    container.appendChild(listContainer);
  }

  /**
   * Dispatches incoming typed messages from Python backend.
   */
  function handleIncomingMessage(msg) {
    if (!msg || typeof msg !== 'object') {
      console.warn('Received non-object message:', msg);
      return;
    }

    const { type, request_id, payload } = msg;

    // Request Isolation Guard: Drop stale events from earlier requests
    const isolateTypes = [
      'investigation.progress',
      'investigation.completed',
      'investigation.failed',
      'investigation.busy'
    ];
    if (isolateTypes.includes(type) && request_id && currentRequestId && request_id !== currentRequestId) {
      console.warn(`[REQUEST_ISOLATION] Dropping stale ${type} (req=${request_id}, current=${currentRequestId})`);
      return;
    }

    switch (type) {
      case 'app.ready':
        isBridgeReady = true;
        statusDot.className = 'status-dot ready';
        statusText.textContent = '상태: 준비됨';
        updateSubmitButtonState();
        break;

      case 'app.status':
        if (payload) {
          if (payload.message) {
            statusText.textContent = `상태: ${payload.message}`;
          }
          const st = payload.status || '';
          if (st === 'ready') {
            statusDot.className = 'status-dot ready';
            renderSlackAuthState('ready', true);
          } else if (st === 'investigating') {
            statusDot.className = 'status-dot busy';
          } else if (st === 'slack_login_required') {
            statusDot.className = 'status-dot error';
            renderSlackAuthState('slack_login_required', true);
          } else if (st === 'slack_login_in_progress') {
            statusDot.className = 'status-dot busy';
            renderSlackAuthState('slack_login_in_progress', true);
          } else if (st === 'provider_setup_required') {
            statusDot.className = 'status-dot';
            if (settingsAiStatus) settingsAiStatus.textContent = '설정 필요 (미설정)';
          } else if (st === 'error') {
            statusDot.className = 'status-dot error';
          }
        }
        break;

      case 'investigation.accepted':
        statusDot.className = 'status-dot busy';
        statusText.textContent = '상태: 조사 시작됨...';
        btnText.textContent = '조사 중...';
        if (submitBtn) submitBtn.disabled = true;
        if (pastResultBanner) pastResultBanner.classList.add('hidden');
        isViewingHistoryRecord = false;

        clearElement(resultCard);
        const loadingBox = document.createElement('div');
        loadingBox.className = 'loading-state-box';

        const spinner = document.createElement('span');
        spinner.className = 'spinner-indicator';

        const loadingText = document.createElement('span');
        loadingText.textContent = '조사를 진행하고 있습니다...';

        loadingBox.appendChild(spinner);
        loadingBox.appendChild(loadingText);
        resultCard.appendChild(loadingBox);

        if (evidenceSection) evidenceSection.classList.add('hidden');
        clearElement(evidenceCard);
        break;

      case 'investigation.progress':
        if (payload && payload.message) {
          statusText.textContent = `상태: ${payload.message}`;
        }
        break;

      case 'investigation.completed':
        statusDot.className = 'status-dot ready';
        statusText.textContent = '상태: 조사 완료';
        btnText.textContent = '실행';
        updateSubmitButtonState();

        if (resultCard && payload) {
          renderGroundedAnswer(resultCard, payload);
        }
        if (payload && Array.isArray(payload.evidence) && payload.evidence.length > 0) {
          if (evidenceSection) evidenceSection.classList.remove('hidden');
          if (evidenceCard) renderEvidence(evidenceCard, payload.evidence, payload.no_evidence_message);
        } else {
          if (evidenceSection) evidenceSection.classList.add('hidden');
        }
        break;

      case 'investigation.failed':
        statusDot.className = 'status-dot error';
        if (evidenceSection) evidenceSection.classList.add('hidden');
        const errText = (payload && payload.error) ? payload.error : '조사 중 오류가 발생했습니다.';
        btnText.textContent = '실행';
        updateSubmitButtonState();

        clearElement(resultCard);
        if (payload && payload.auth_required) {
          statusDot.className = 'status-dot';
          statusText.textContent = '상태: Slack 로그인 필요';
          renderSlackAuthState('slack_login_required', true);

          const authBox = document.createElement('div');
          authBox.className = 'auth-required-box';

          const authTitle = document.createElement('div');
          authTitle.className = 'auth-required-title';
          authTitle.textContent = 'Slack 로그인이 필요합니다';

          const authDesc = document.createElement('div');
          authDesc.className = 'auth-required-desc';
          authDesc.textContent = 'PESU가 Slack 대화를 검색하려면 PESU 전용 Slack 세션에 로그인해야 합니다. 아래 버튼을 눌러 로그인을 진행해 주세요.';

          const authAction = document.createElement('div');
          authAction.className = 'auth-required-action';

          const loginBtn = document.createElement('button');
          loginBtn.type = 'button';
          loginBtn.id = 'start-login-btn';
          loginBtn.className = 'btn btn-primary';
          loginBtn.textContent = 'Slack 로그인';
          loginBtn.addEventListener('click', () => {
            loginBtn.disabled = true;
            statusText.textContent = '상태: Slack 로그인 창 여는 중...';
            sendMessage('slack.login.request', 'login-req-' + Date.now(), {});
          });

          authAction.appendChild(loginBtn);
          authBox.appendChild(authTitle);
          authBox.appendChild(authDesc);
          authBox.appendChild(authAction);
          resultCard.appendChild(authBox);
        } else {
          statusText.textContent = `오류: ${errText}`;
          const errBox = document.createElement('div');
          errBox.className = 'error-box';

          const errTitle = document.createElement('div');
          errTitle.className = 'error-title';
          errTitle.textContent = errText;

          errBox.appendChild(errTitle);
          resultCard.appendChild(errBox);
        }
        break;

      case 'investigation.busy':
        const busyText = (payload && payload.message) ? payload.message : '현재 다른 조사가 진행 중입니다.';
        statusText.textContent = `알림: ${busyText}`;
        btnText.textContent = '실행';
        updateSubmitButtonState();
        break;

      case 'citation.open_ack':
        if (payload && payload.success) {
          statusText.textContent = `상태: 근거 [${payload.evidence_id}] 브라우저에서 열림`;
        } else {
          statusText.textContent = `알림: 링크 열기 실패 (${(payload && payload.reason) || '오류'})`;
        }
        break;

      case 'bridge.pong':
        console.log('bridge.pong received:', payload);
        break;

      case 'consent.external_analysis.required':
        if (consentModal) {
          consentModal.classList.remove('hidden');
        }
        statusDot.className = 'status-dot';
        statusText.textContent = '상태: 외부 AI 분석 허용 확인 대기 중';
        btnText.textContent = '실행';
        updateSubmitButtonState();
        break;

      case 'consent.external_analysis.state':
        if (payload && payload.state === 'GRANTED_FOR_SESSION') {
          if (consentModal) {
            consentModal.classList.add('hidden');
          }
          statusText.textContent = '상태: 외부 AI 분석 허용됨 (현재 세션)';
          if (settingsConsentStatus) settingsConsentStatus.textContent = '이번 실행 중 허용됨';
          updateSubmitButtonState();
        } else if (payload && payload.state === 'NOT_GRANTED') {
          if (consentModal) consentModal.classList.add('hidden');
          if (settingsConsentStatus) settingsConsentStatus.textContent = '필요할 때 확인';
          btnText.textContent = '실행';
          updateSubmitButtonState();
        }
        break;

      case 'slack.login.started':
        statusDot.className = 'status-dot';
        statusText.textContent = '상태: Slack 로그인 창 열림 (로그인 완료 후 창 닫기)';
        if (submitBtn) submitBtn.disabled = true;
        renderSlackAuthState('slack_login_in_progress', false);
        break;

      case 'slack.login.busy':
        statusText.textContent = `알림: ${(payload && payload.message) || '현재 로그인을 진행할 수 없습니다.'}`;
        break;

      case 'slack.login.completed':
        statusDot.className = 'status-dot ready';
        statusText.textContent = '상태: Slack 로그인 창이 닫혔습니다.';
        clearElement(resultCard);
        const successBox = document.createElement('div');
        successBox.className = 'login-success-box';
        const st = document.createElement('div');
        st.className = 'login-success-title';
        st.textContent = 'Slack 로그인 창이 닫혔습니다.';
        const sd = document.createElement('div');
        sd.className = 'login-success-desc';
        sd.textContent = '이제 원하는 질문을 입력하여 조사를 시작할 수 있습니다.';
        successBox.appendChild(st);
        successBox.appendChild(sd);
        resultCard.appendChild(successBox);
        updateSubmitButtonState();
        // Critical: Do NOT directly invent READY here.
        // LOGIN_COMPLETED_DIRECTLY_ASSUMES_READY = false.
        // Authoritative subsequent app.status establishes READY.
        break;

      case 'slack.login.failed':
        statusDot.className = 'status-dot error';
        statusText.textContent = `오류: ${(payload && payload.error) || 'Slack 로그인 실패'}`;
        renderSlackAuthState('slack_login_failed', false);
        updateSubmitButtonState();
        break;

      case 'provider.openrouter.setup.required':
        if (apiKeyModal) {
          apiKeyModal.classList.remove('hidden');
          if (apiKeyInput) apiKeyInput.value = '';
          if (apiKeyError) apiKeyError.classList.add('hidden');
        }
        statusDot.className = 'status-dot';
        statusText.textContent = '상태: OpenRouter API 키 설정 필요';
        btnText.textContent = '실행';
        updateSubmitButtonState();
        break;

      case 'provider.openrouter.status':
        if (payload) {
          isProviderConfigured = !!payload.configured;
          updateOnboardingAiView();
          if (payload.configured) {
            if (providerBadge) {
              providerBadge.classList.add('configured');
              providerBadge.textContent = 'AI · 설정됨';
            }
            if (settingsAiStatus) {
              const srcText = (payload.source === 'environment') ? '설정됨 (환경 설정에서 사용 중)' : '설정됨 (PESU 보안 저장소에서 사용 중)';
              settingsAiStatus.textContent = srcText;
            }
            if (settingsAiDesc) {
              if (payload.source === 'environment') {
                settingsAiDesc.textContent = '• API 키가 시스템 환경 설정(OPENROUTER_API_KEY)에서 제공되고 있습니다.';
              } else {
                settingsAiDesc.textContent = '• API 키는 Windows DPAPI로 암호화되어 안전하게 보관됩니다.';
              }
            }
            if (settingsAiSetupBtn) settingsAiSetupBtn.textContent = '키 변경';
            if (settingsAiRemoveBtn) {
              if (payload.can_remove) {
                settingsAiRemoveBtn.classList.remove('hidden');
              } else {
                settingsAiRemoveBtn.classList.add('hidden');
              }
            }
            if (apiKeyRemoveBtn) {
              if (payload.can_remove) {
                apiKeyRemoveBtn.classList.remove('hidden');
              } else {
                apiKeyRemoveBtn.classList.add('hidden');
              }
            }
          } else {
            if (providerBadge) {
              providerBadge.classList.remove('configured');
              providerBadge.textContent = 'AI · 설정 필요';
            }
            if (settingsAiStatus) settingsAiStatus.textContent = '설정 필요 (미설정)';
            if (settingsAiDesc) {
              settingsAiDesc.textContent = '• AI 분석 기능을 사용하려면 OpenRouter API 키를 설정해야 합니다.';
            }
            if (settingsAiSetupBtn) settingsAiSetupBtn.textContent = '키 설정';
            if (settingsAiRemoveBtn) settingsAiRemoveBtn.classList.add('hidden');
            if (apiKeyRemoveBtn) apiKeyRemoveBtn.classList.add('hidden');
          }
        }
        break;

      case 'provider.openrouter.key.saved':
        if (apiKeyModal) apiKeyModal.classList.add('hidden');
        if (apiKeyInput) apiKeyInput.value = '';
        statusText.textContent = '상태: API 키 저장 완료';
        break;

      case 'provider.openrouter.key.removed':
        if (apiKeyModal) apiKeyModal.classList.add('hidden');
        if (apiKeyInput) apiKeyInput.value = '';
        statusText.textContent = '상태: 저장된 API 키 삭제 완료';
        break;

      case 'provider.openrouter.error':
        if (apiKeyError) {
          apiKeyError.textContent = (payload && payload.error) || '오류가 발생했습니다.';
          apiKeyError.classList.remove('hidden');
        }
        statusText.textContent = `오류: ${(payload && payload.error) || 'API 키 처리 실패'}`;
        break;

      case 'settings.open':
        openSettingsModal();
        break;

      case 'settings.close':
        closeSettingsModal();
        break;

      case 'onboarding.show':
        openOnboardingModal();
        break;

      case 'onboarding.dismiss':
        closeOnboardingModal(false);
        break;

      case 'startup.auto.status':
        if (payload) {
          updateAutostartUI(payload);
        }
        break;

      case 'startup.auto.error':
        if (payload && payload.error) {
          if (settingsAutostartDesc) {
            settingsAutostartDesc.textContent = `• ${payload.error}`;
          }
        }
        break;

      case 'history.list.result':
        if (payload) {
          historyTotalCount = payload.total_count || 0;
          if (historyCountBadge) historyCountBadge.textContent = String(historyTotalCount);
          if (settingsHistoryStatus) settingsHistoryStatus.textContent = `${historyTotalCount}건 저장됨`;
          renderHistoryItems(payload.items || [], historyTotalCount, payload.offset || 0);
        }
        break;

      case 'history.record':
        if (payload && payload.record) {
          const rec = payload.record;
          isViewingHistoryRecord = true;
          closeHistoryDrawer();

          if (queryInput) queryInput.value = rec.question || '';
          if (pastResultDate) pastResultDate.textContent = formatDateTime(rec.created_at || rec.completed_at);
          if (pastResultBanner) pastResultBanner.classList.remove('hidden');

          if (resultCard) {
            renderAnswer(resultCard, rec.answer);
          }
          if (Array.isArray(rec.evidence) && rec.evidence.length > 0) {
            if (evidenceSection) evidenceSection.classList.remove('hidden');
            if (evidenceCard) renderEvidence(evidenceCard, rec.evidence, null);
          } else {
            if (evidenceSection) evidenceSection.classList.add('hidden');
            if (evidenceCard) clearElement(evidenceCard);
          }

          statusDot.className = 'status-dot ready';
          statusText.textContent = '상태: 과거 조사 결과 표시 중';
          btnText.textContent = '실행';
          updateSubmitButtonState();
        }
        break;

      case 'history.deleted':
        if (payload && payload.id) {
          const card = document.querySelector(`.history-item-card[data-history-id="${payload.id}"]`);
          if (card && card.parentNode) {
            card.parentNode.removeChild(card);
          }
          sendMessage('history.status.get', currentRequestId, {});
          statusText.textContent = '상태: 조사 기록 삭제 완료';
        }
        break;

      case 'history.cleared':
        if (historyList) clearElement(historyList);
        if (historyEmpty) historyEmpty.classList.remove('hidden');
        if (historyLoadMore) historyLoadMore.classList.add('hidden');
        if (historyCountBadge) historyCountBadge.textContent = '0';
        if (settingsHistoryStatus) settingsHistoryStatus.textContent = '0건 저장됨';
        if (isViewingHistoryRecord) {
          returnToComposer();
        }
        statusText.textContent = '상태: 모든 조사 기록이 삭제되었습니다.';
        break;

      case 'history.status':
        if (payload) {
          const cnt = typeof payload.count === 'number' ? payload.count : 0;
          historyTotalCount = cnt;
          if (historyCountBadge) historyCountBadge.textContent = String(cnt);
          if (settingsHistoryStatus) settingsHistoryStatus.textContent = `${cnt}건 저장됨`;
        }
        break;

      case 'history.error':
        statusText.textContent = `오류: ${(payload && payload.error) || '기록 처리 실패'}`;
        break;

      case 'app.error':
        statusDot.className = 'status-dot error';
        statusText.textContent = `오류: ${(payload && payload.error) || '내부 오류'}`;
        btnText.textContent = '실행';
        updateSubmitButtonState();
        break;

      default:
        console.warn('Unhandled message type:', type);
    }
  }

  function updateSubmitButtonState() {
    const hasText = queryInput && queryInput.value.trim().length > 0;
    if (submitBtn) {
      submitBtn.disabled = !(isBridgeReady && hasText && btnText.textContent !== '조사 중...');
    }
  }

  function submitQuery() {
    if (!queryInput || !isBridgeReady) return;
    const text = queryInput.value.trim();
    if (!text) return;

    currentRequestId = generateRequestId();

    // Set UI immediately to running state
    statusDot.className = 'status-dot busy';
    statusText.textContent = '상태: 요청 전송 중...';
    btnText.textContent = '조사 중...';
    if (submitBtn) submitBtn.disabled = true;

    sendMessage('query.submit', currentRequestId, { query: text });
  }

  // Setup DOM event listeners
  if (queryInput) {
    queryInput.addEventListener('input', function () {
      updateSubmitButtonState();
      sendMessage('query.draft_changed', null, { query: queryInput.value });
    });

    queryInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (submitBtn && !submitBtn.disabled) {
          submitQuery();
        }
      }
    });
  }

  if (submitBtn) {
    submitBtn.addEventListener('click', function () {
      if (!submitBtn.disabled) {
        submitQuery();
      }
    });
  }

  if (consentGrantBtn) {
    consentGrantBtn.addEventListener('click', function () {
      if (consentModal) consentModal.classList.add('hidden');
      statusDot.className = 'status-dot busy';
      statusText.textContent = '상태: 외부 AI 분석 허용됨, 조사 시작 중...';
      btnText.textContent = '조사 중...';
      if (submitBtn) submitBtn.disabled = true;
      sendMessage('consent.external_analysis.grant', currentRequestId, {});
    });
  }

  if (consentCancelBtn) {
    consentCancelBtn.addEventListener('click', function () {
      if (consentModal) consentModal.classList.add('hidden');
      statusDot.className = 'status-dot';
      statusText.textContent = '상태: 외부 AI 분석 허용 취소됨';
      btnText.textContent = '실행';
      updateSubmitButtonState();
      sendMessage('consent.external_analysis.cancel', currentRequestId, {});
    });
  }

  if (headerSettingsBtn) {
    headerSettingsBtn.addEventListener('click', openSettingsModal);
  }

  if (settingsCloseBtn) {
    settingsCloseBtn.addEventListener('click', closeSettingsModal);
  }

  if (settingsCloseXBtn) {
    settingsCloseXBtn.addEventListener('click', closeSettingsModal);
  }

  if (settingsSlackLoginBtn) {
    settingsSlackLoginBtn.addEventListener('click', function () {
      closeSettingsModal();
      statusText.textContent = '상태: Slack 로그인 창 여는 중...';
      sendMessage('slack.login.request', 'login-req-' + Date.now(), {});
    });
  }

  if (settingsAiSetupBtn) {
    settingsAiSetupBtn.addEventListener('click', function () {
      closeSettingsModal();
      if (apiKeyModal) {
        apiKeyModal.classList.remove('hidden');
        if (apiKeyInput) apiKeyInput.value = '';
        if (apiKeyError) apiKeyError.classList.add('hidden');
      }
    });
  }

  if (settingsAiRemoveBtn) {
    settingsAiRemoveBtn.addEventListener('click', function () {
      if (keyConfirmModal) keyConfirmModal.classList.remove('hidden');
    });
  }

  if (keyConfirmCancelBtn) {
    keyConfirmCancelBtn.addEventListener('click', function () {
      if (keyConfirmModal) keyConfirmModal.classList.add('hidden');
    });
  }

  if (keyConfirmOkBtn) {
    keyConfirmOkBtn.addEventListener('click', function () {
      if (keyConfirmModal) keyConfirmModal.classList.add('hidden');
      if (apiKeyInput) apiKeyInput.value = '';
      statusText.textContent = '상태: 저장된 API 키 삭제 중...';
      sendMessage('provider.openrouter.key.remove', currentRequestId, {});
    });
  }

  if (settingsReopenOnboardingBtn) {
    settingsReopenOnboardingBtn.addEventListener('click', function () {
      closeSettingsModal();
      openOnboardingModal();
    });
  }

  if (onboardingNext1Btn) {
    onboardingNext1Btn.addEventListener('click', function () {
      showOnboardingStep(2);
    });
  }

  if (onboardingSkipBtn) {
    onboardingSkipBtn.addEventListener('click', function () {
      closeOnboardingModal(true);
    });
  }

  if (onboardingPrev2Btn) {
    onboardingPrev2Btn.addEventListener('click', function () {
      showOnboardingStep(1);
    });
  }

  if (onboardingNext2Btn) {
    onboardingNext2Btn.addEventListener('click', function () {
      showOnboardingStep(3);
    });
  }

  if (onboardingPrev3Btn) {
    onboardingPrev3Btn.addEventListener('click', function () {
      showOnboardingStep(2);
    });
  }

  if (onboardingFinishBtn) {
    onboardingFinishBtn.addEventListener('click', function () {
      closeOnboardingModal(true);
    });
  }

  if (onboardingAiSetupBtn) {
    onboardingAiSetupBtn.addEventListener('click', function () {
      if (apiKeyModal) {
        apiKeyModal.classList.remove('hidden');
        if (apiKeyInput) apiKeyInput.value = '';
        if (apiKeyError) apiKeyError.classList.add('hidden');
      }
    });
  }

  if (onboardingSlackLoginBtn) {
    onboardingSlackLoginBtn.addEventListener('click', function () {
      statusText.textContent = '상태: Slack 로그인 창 여는 중...';
      sendMessage('slack.login.request', 'login-req-' + Date.now(), {});
    });
  }

  if (providerBadge) {
    providerBadge.addEventListener('click', openSettingsModal);
  }

  if (apiKeySaveBtn) {
    apiKeySaveBtn.addEventListener('click', function () {
      if (!apiKeyInput) return;
      const key = apiKeyInput.value.trim();
      if (!key) {
        if (apiKeyError) {
          apiKeyError.textContent = 'API 키를 입력해 주세요.';
          apiKeyError.classList.remove('hidden');
        }
        return;
      }
      apiKeyInput.value = '';
      if (apiKeyModal) apiKeyModal.classList.add('hidden');
      statusText.textContent = '상태: API 키 저장 중...';
      sendMessage('provider.openrouter.key.save', currentRequestId, { key: key });
    });
  }

  if (apiKeyCancelBtn) {
    apiKeyCancelBtn.addEventListener('click', function () {
      if (apiKeyInput) apiKeyInput.value = '';
      if (apiKeyModal) apiKeyModal.classList.add('hidden');
      sendMessage('provider.openrouter.key.cancel', currentRequestId, {});
    });
  }

  if (apiKeyRemoveBtn) {
    apiKeyRemoveBtn.addEventListener('click', function () {
      if (apiKeyModal) apiKeyModal.classList.add('hidden');
      if (keyConfirmModal) keyConfirmModal.classList.remove('hidden');
    });
  }

  function updateAutostartUI(payload) {
    if (!settingsAutostartStatus || !settingsAutostartToggleBtn) return;
    const status = payload.status || 'DISABLED';
    currentAutostartStatus = status;

    if (status === 'UNSUPPORTED') {
      settingsAutostartStatus.textContent = '지원되지 않음 (개발 모드)';
      settingsAutostartStatus.style.color = 'var(--text-dim)';
      settingsAutostartToggleBtn.textContent = '켜기';
      settingsAutostartToggleBtn.disabled = true;
      if (settingsAutostartDesc) {
        settingsAutostartDesc.textContent = '• 자동 시작 설정은 배포된 PESU 앱에서 사용할 수 있습니다.';
      }
    } else if (status === 'ENABLED') {
      settingsAutostartStatus.textContent = '켜짐 (ON)';
      settingsAutostartStatus.style.color = 'var(--status-ready)';
      settingsAutostartToggleBtn.textContent = '끄기';
      settingsAutostartToggleBtn.disabled = false;
      if (settingsAutostartDesc) {
        settingsAutostartDesc.textContent = '• 켜면 Windows 로그인 후 PESU가 시스템 트레이에서 자동으로 시작됩니다.';
      }
    } else if (status === 'STALE') {
      settingsAutostartStatus.textContent = '경로 변경 감지 (재설정 필요)';
      settingsAutostartStatus.style.color = 'var(--status-busy)';
      settingsAutostartToggleBtn.textContent = '다시 켜기';
      settingsAutostartToggleBtn.disabled = false;
      if (settingsAutostartDesc) {
        settingsAutostartDesc.textContent = '• PESU 앱 위치가 변경되었습니다. \'다시 켜기\'를 누르면 현재 위치로 자동 실행이 갱신됩니다.';
      }
    } else if (status === 'ERROR') {
      settingsAutostartStatus.textContent = '확인 실패';
      settingsAutostartStatus.style.color = 'var(--status-error)';
      settingsAutostartToggleBtn.textContent = '다시 시도';
      settingsAutostartToggleBtn.disabled = false;
      if (settingsAutostartDesc) {
        settingsAutostartDesc.textContent = '• 자동 시작 설정을 확인하지 못했습니다.';
      }
    } else { // DISABLED
      settingsAutostartStatus.textContent = '꺼짐 (OFF)';
      settingsAutostartStatus.style.color = 'var(--text-muted)';
      settingsAutostartToggleBtn.textContent = '켜기';
      settingsAutostartToggleBtn.disabled = false;
      if (settingsAutostartDesc) {
        settingsAutostartDesc.textContent = '• 켜면 Windows 로그인 후 PESU가 시스템 트레이에서 자동으로 시작됩니다.';
      }
    }
  }

  if (settingsAutostartToggleBtn) {
    settingsAutostartToggleBtn.addEventListener('click', function () {
      settingsAutostartToggleBtn.disabled = true;
      if (currentAutostartStatus === 'ENABLED') {
        sendMessage('startup.auto.disable', currentRequestId, {});
      } else {
        sendMessage('startup.auto.enable', currentRequestId, {});
      }
    });
  }

  // History Event Listeners
  if (headerHistoryBtn) {
    headerHistoryBtn.addEventListener('click', function () {
      if (historyDrawer && !historyDrawer.classList.contains('hidden')) {
        closeHistoryDrawer();
      } else {
        openHistoryDrawer();
      }
    });
  }

  if (historyDrawerCloseBtn) {
    historyDrawerCloseBtn.addEventListener('click', closeHistoryDrawer);
  }

  if (historyDrawerBackdrop) {
    historyDrawerBackdrop.addEventListener('click', closeHistoryDrawer);
  }

  if (historyLoadMoreBtn) {
    historyLoadMoreBtn.addEventListener('click', function () {
      sendMessage('history.list', generateRequestId(), {
        limit: historyLimit,
        offset: historyOffset,
      });
    });
  }

  if (btnReturnComposer) {
    btnReturnComposer.addEventListener('click', returnToComposer);
  }

  if (settingsHistoryClearBtn) {
    settingsHistoryClearBtn.addEventListener('click', function () {
      if (historyConfirmModal) historyConfirmModal.classList.remove('hidden');
    });
  }

  if (historyConfirmCancelBtn) {
    historyConfirmCancelBtn.addEventListener('click', function () {
      if (historyConfirmModal) historyConfirmModal.classList.add('hidden');
    });
  }

  if (historyConfirmOkBtn) {
    historyConfirmOkBtn.addEventListener('click', function () {
      if (historyConfirmModal) historyConfirmModal.classList.add('hidden');
      statusText.textContent = '상태: 조사 기록 삭제 중...';
      sendMessage('history.clear', generateRequestId(), {});
    });
  }

  // Register Native WebMessage listener
  if (window.chrome && window.chrome.webview) {
    window.chrome.webview.addEventListener('message', function (event) {
      handleIncomingMessage(event.data);
    });

    // Initial handshake
    sendMessage('ui.ready', generateRequestId(), {
      timestamp: Date.now(),
      client: 'PESU-UI-WebMessage-v1'
    });
  } else {
    console.warn('window.chrome.webview not detected. Bridge offline.');
    statusText.textContent = '오류: WebView2 브리지 미연결';
  }

  // Export for testing/verification
  window._pesuUI = {
    handleIncomingMessage: handleIncomingMessage,
    sendMessage: sendMessage,
    getCurrentRequestId: function () { return currentRequestId; },
    setCurrentRequestId: function (id) { currentRequestId = id; },
    openHistoryDrawer: openHistoryDrawer,
    closeHistoryDrawer: closeHistoryDrawer,
    returnToComposer: returnToComposer,
    renderSlackAuthState: renderSlackAuthState,
    getSlackAuthStatus: function () { return currentSlackAuthStatus; },
  };

})();
