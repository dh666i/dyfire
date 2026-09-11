(() => {
  "use strict";

  const root = document.querySelector("[data-account-scan]");
  if (!root) return;
  const INITIALIZED_KEY = "__douyinSparkAccountScanInitialized";
  if (window[INITIALIZED_KEY]) return;
  window[INITIALIZED_KEY] = true;

  const TERMINAL = new Set(["succeeded", "failed", "expired", "cancelled"]);
  const POLL_INTERVAL_MS = 1000;
  const INTERACTION_POLL_INTERVAL_MS = 750;
  const INTERACTION_MAX_POLLS = 20;
  const INTERACTION_FALLBACK_WAIT_MS = 6000;
  const INTERACTION_SETTLE_LOCK_MS = 1500;
  const MAX_BROWSER_TEXT_LENGTH = 256;
  const csrfToken = root.dataset.csrfToken || "";

  const startButton = document.querySelector("#scan-start");
  const reloginButtons = Array.from(document.querySelectorAll("[data-scan-account-id]"));
  const launchButtons = [startButton, ...reloginButtons].filter(Boolean);
  const dialog = document.querySelector("#scan-dialog");
  const dialogTitleNode = document.querySelector("#scan-dialog-title");
  const closeButton = document.querySelector("#scan-close");
  const cancelButton = document.querySelector("#scan-cancel");
  const stageNode = document.querySelector("#scan-stage");
  const statusNode = document.querySelector("#scan-status");
  const countdownNode = document.querySelector("#scan-countdown");
  const qrPanelNode = document.querySelector("#scan-qr-panel");
  const qrShellNode = document.querySelector("#scan-qr-shell");
  let qrNode = document.querySelector("#scan-qr");
  const qrLoadingNode = document.querySelector("#scan-qr-loading");
  const placeholderNode = document.querySelector("#scan-placeholder");
  const browserHintNode = document.querySelector("#scan-browser-hint");
  const resultNode = document.querySelector("#scan-result");
  const resultTitleNode = document.querySelector("#scan-result-title");
  const textNode = document.querySelector("#scan-text");
  const typeButton = document.querySelector("#scan-type");
  const interactionStatusNode = document.querySelector("#scan-interaction-status");

  let scanId = null;
  let currentStatus = null;
  let timer = null;
  let successTimer = null;
  let startPending = false;
  let closeRequested = false;
  let cancelPending = false;
  let textPending = false;
  let textInteractionPending = false;
  let textCooldown = false;
  let textCooldownTimer = null;
  let interactionTimer = null;
  let activeTextInteraction = null;
  let textFlowGeneration = 0;
  let textAttemptCount = 0;
  let uncertainConfirmationRequired = false;
  let requestSequence = 0;
  let clickPending = false;
  let pollFailureCount = 0;
  let viewLoaded = false;
  let currentFrameRevision = null;
  let viewRequest = null;
  let viewRefreshQueued = false;
  let queuedFrameRevision = null;
  let keyboardCaptureActive = false;
  let targetAccountId = null;
  let targetAccountName = "";
  let currentLaunchButton = startButton;

  for (const button of launchButtons) {
    button.dataset.scanIdleLabel = button.textContent.trim()
      || (button.dataset.scanAccountId ? "重新登录" : "扫码绑定抖音账号");
  }

  function clearTimer() {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  }

  function clearSuccessTimer() {
    if (successTimer !== null) window.clearTimeout(successTimer);
    successTimer = null;
  }

  function clearTextCooldownTimer() {
    if (textCooldownTimer !== null) window.clearTimeout(textCooldownTimer);
    textCooldownTimer = null;
  }

  function clearInteractionTimer() {
    if (interactionTimer !== null) window.clearTimeout(interactionTimer);
    interactionTimer = null;
  }

  function setInteractionMessage(status, message) {
    if (!interactionStatusNode) return;
    interactionStatusNode.dataset.status = status || "";
    interactionStatusNode.textContent = message || "";
    interactionStatusNode.hidden = !message;
  }

  function resetTextInteraction({clearAttempts = true, hideMessage = true} = {}) {
    clearTextCooldownTimer();
    clearInteractionTimer();
    textFlowGeneration += 1;
    textPending = false;
    textInteractionPending = false;
    textCooldown = false;
    activeTextInteraction = null;
    uncertainConfirmationRequired = false;
    if (clearAttempts) textAttemptCount = 0;
    if (hideMessage) setInteractionMessage("", "");
  }

  function beginTextCooldown(delay = INTERACTION_SETTLE_LOCK_MS) {
    clearTextCooldownTimer();
    textCooldown = true;
    const generation = textFlowGeneration;
    textCooldownTimer = window.setTimeout(() => {
      textCooldownTimer = null;
      if (generation !== textFlowGeneration) return;
      textCooldown = false;
      syncControls();
    }, delay);
  }

  function createRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    requestSequence += 1;
    const timePart = Date.now().toString(16).padStart(12, "0").slice(-12);
    const sequencePart = requestSequence.toString(16).padStart(4, "0").slice(-4);
    const randomPart = Math.floor(Math.random() * 0x100000000)
      .toString(16)
      .padStart(8, "0");
    return `${timePart.slice(0, 8)}-${timePart.slice(8)}-4${sequencePart.slice(1)}-a${randomPart.slice(1, 4)}-${randomPart}${sequencePart}`;
  }

  function textControlsLocked() {
    return textPending || textInteractionPending || textCooldown;
  }

  function isTerminal(status = currentStatus) {
    return TERMINAL.has(status);
  }

  function isActive() {
    return Boolean(scanId) && !isTerminal() && !cancelPending;
  }

  function isRelogin() {
    return Boolean(targetAccountId);
  }

  function setScanMode(accountId, accountName, launchButton) {
    targetAccountId = accountId || null;
    targetAccountName = String(accountName || "").trim();
    currentLaunchButton = launchButton || startButton || null;
    if (dialogTitleNode) {
      dialogTitleNode.textContent = isRelogin()
        ? `重新登录${targetAccountName ? `“${targetAccountName}”` : "抖音账号"}`
        : "绑定抖音账号";
    }
    if (cancelButton) cancelButton.textContent = isRelogin() ? "取消重新登录" : "取消绑定";
  }

  function setBusy(busy) {
    for (const button of launchButtons) {
      button.disabled = busy;
      if (!busy) {
        button.textContent = button.dataset.scanIdleLabel;
      } else if (button === currentLaunchButton) {
        button.textContent = isRelogin() ? "正在发起重新登录…" : "正在发起绑定…";
      }
    }
  }

  function syncControls() {
    const active = isActive();
    const locked = textControlsLocked();
    if (textNode) textNode.disabled = !active || locked;
    if (typeButton) {
      typeButton.disabled = !active || locked;
      if (textPending) typeButton.textContent = "正在提交…";
      else if (textInteractionPending) typeButton.textContent = "等待云端回执…";
      else if (textCooldown) typeButton.textContent = "请稍候…";
      else if (uncertainConfirmationRequired) typeButton.textContent = "确认后重试";
      else typeButton.textContent = "发送到云端";
    }
    if (cancelButton) cancelButton.disabled = !active;
  }

  function phaseLabel(status = currentStatus) {
    if (status === "succeeded") return "步骤 4/4 · 保存完成";
    if (status === "confirming") return "步骤 3/4 · 确认与验证";
    if (status === "awaiting_scan") return "步骤 2/4 · 扫码登录";
    if (isTerminal(status)) return "本次会话已结束";
    return "步骤 1/4 · 启动云端页面";
  }

  function showMessage(message, seconds) {
    if (statusNode) statusNode.textContent = message || "扫码状态暂不可用";
    if (countdownNode) {
      const countdown = Number.isFinite(seconds) && seconds > 0 ? ` · 剩余 ${seconds} 秒` : "";
      countdownNode.textContent = `${phaseLabel()}${countdown}`;
    }
  }

  let loadingStarted = null;
  const viewRetry = document.querySelector('#scan-view-retry');
  const loadingCopy = document.querySelector('.scan-loading-copy');
  viewRetry?.addEventListener('click', () => {
    if (!scanId || isTerminal()) return;
    if (viewRequest) { viewRequest.onload = null; viewRequest.onerror = null; viewRequest.src = ''; viewRequest = null; }
    viewRetry.hidden = true;
    if (loadingCopy) loadingCopy.textContent = '正在重新读取当前会话，不会重复提交登录验证。';
    refreshBrowserView(scanId);
  });
  function setViewLoading(loading) {
    if (loading && loadingStarted === null) loadingStarted = Date.now();
    if (loading && Date.now() - loadingStarted >= 20000) {
      if (!viewLoaded && scanId && !isTerminal()) {
        if (loadingCopy) loadingCopy.textContent = '连接耗时较长，尚未收到云端画面。可重新读取，或取消后稍后再试。';
        if (viewRetry) viewRetry.hidden = false;
      }
    }
    if (!loading) loadingStarted = null;
    if (dialog) dialog.dataset.frameReady = viewLoaded ? "true" : "false";
    if (qrShellNode) qrShellNode.dataset.loading = loading ? "true" : "false";
    if (qrLoadingNode) qrLoadingNode.hidden = !loading;
    if (placeholderNode) placeholderNode.hidden = viewLoaded;
    if (stageNode) stageNode.setAttribute("aria-busy", loading || startPending ? "true" : "false");
  }

  function clearView() {
    loadingStarted = null;
    if (viewRetry) viewRetry.hidden = true;
    if (loadingCopy) loadingCopy.textContent = '首次连接需要一点时间，页面就绪后会自动显示。';
    if (viewRequest) {
      viewRequest.onload = null;
      viewRequest.onerror = null;
      viewRequest.src = "";
      viewRequest = null;
    }
    viewRefreshQueued = false;
    queuedFrameRevision = null;
    viewLoaded = false;
    if (dialog) dialog.dataset.frameReady = "false";
    currentFrameRevision = null;
    keyboardCaptureActive = false;
    if (qrNode) qrNode.removeAttribute("src");
    if (qrShellNode) qrShellNode.dataset.loading = "true";
    if (qrLoadingNode) qrLoadingNode.hidden = false;
    if (placeholderNode) placeholderNode.hidden = false;
  }

  function refreshBrowserView(id, revision = null) {
    if (!id || !qrNode || isTerminal()) return;
    // Status polling happens more often than the browser frame changes. Keep
    // the existing <img> when the server reports the same content revision;
    // replacing it on every poll causes a visible flash and loses focus.
    if (revision && revision === currentFrameRevision) return;
    if (viewRequest) {
      viewRefreshQueued = true;
      // Keep the newest known revision while the current image is loading.
      // A click-triggered refresh has no revision and must not overwrite a
      // newer status-poll revision that is already queued.
      if (revision) queuedFrameRevision = revision;
      return;
    }
    if (!viewLoaded) setViewLoading(true);
    const cacheKey = revision || Date.now();
    const url = `/accounts/scan/${encodeURIComponent(id)}/qr?v=${encodeURIComponent(cacheKey)}`;
    if (typeof window.Image !== "function") {
      qrNode.src = url;
      return;
    }

    const candidate = new window.Image();
    const targetScanId = id;
    candidate.id = "scan-qr";
    candidate.className = "scan-qr";
    candidate.alt = "云端抖音登录页面";
    candidate.decoding = "async";
    candidate.draggable = false;
    viewRequest = candidate;

    const finish = () => {
      if (viewRequest !== candidate) return;
      viewRequest = null;
      const refreshAgain = viewRefreshQueued;
      const nextRevision = queuedFrameRevision;
      viewRefreshQueued = false;
      queuedFrameRevision = null;
      if (refreshAgain && scanId === targetScanId && !isTerminal()) {
        window.setTimeout(
          () => refreshBrowserView(targetScanId, nextRevision),
          0,
        );
      }
    };

    candidate.onload = () => {
      if (viewRequest !== candidate) return;
      if (scanId === targetScanId && !isTerminal()) {
        candidate.addEventListener("click", forwardBrowserClick);
        if (typeof qrNode.replaceWith === "function") {
          qrNode.replaceWith(candidate);
          qrNode = candidate;
        } else {
          qrNode.src = url;
        }
        viewLoaded = true;
        if (revision) currentFrameRevision = revision;
        setViewLoading(false);
      }
      finish();
    };
    candidate.onerror = () => {
      if (viewRequest !== candidate) return;
      if (!viewLoaded) {
        setViewLoading(true);
        if (currentStatus === "awaiting_scan") showMessage("云端页面尚未就绪，正在重试", 0);
      }
      finish();
    };
    candidate.src = url;
  }

  function showBrowserStage() {
    if (qrPanelNode) qrPanelNode.hidden = false;
    if (resultNode) resultNode.hidden = true;
    if (stageNode) stageNode.dataset.view = "browser";
    if (!viewLoaded) setViewLoading(true);
  }

  function showTerminalStage(state) {
    if (qrPanelNode) qrPanelNode.hidden = true;
    if (resultNode) {
      resultNode.hidden = false;
      resultNode.dataset.tone = state.status === "succeeded" ? "success" : "error";
    }
    if (stageNode) {
      stageNode.dataset.view = "result";
      stageNode.setAttribute("aria-busy", "false");
    }
    clearView();
    resetTextInteraction();
    if (textNode) textNode.value = "";
    if (placeholderNode) placeholderNode.hidden = true;
    if (qrLoadingNode) qrLoadingNode.hidden = true;
    if (resultTitleNode) {
      resultTitleNode.textContent = state.status === "succeeded"
        ? (isRelogin() ? "账号重新登录成功" : "账号绑定成功")
        : (isRelogin() ? "本次重新登录未完成" : "本次绑定未完成");
    }
  }

  function statusMessage(state) {
    if (state.status === "awaiting_scan") return "请使用抖音 App 扫码并在手机确认";
    if (state.needs_verification === true
      || state.error === "needs_verification"
      || state.error === "needs_app_verification") {
      return "请继续在上方云端抖音页面完成验证";
    }
    return state.message || "云端登录页正在处理中";
  }

  function browserHint(state) {
    if (keyboardCaptureActive) {
      return "已选中云端位置，请直接键入内容；按回车或点击“发送到云端”。";
    }
    if (state.needs_verification === true
      || state.error === "needs_verification"
      || state.error === "needs_app_verification") {
      return "请直接在上方云端页面完成当前验证。点击页面中的输入框后可立即键入，按回车或下方按钮发送。";
    }
    if (state.status === "confirming") {
      return "已扫码，请在手机上确认登录；后续验证会同步显示在上方云端页面。";
    }
    if (state.status === "awaiting_scan") {
      return "请使用抖音 App 扫描上方页面中的二维码。";
    }
    return "云端抖音页面会实时同步；点击页面中的输入框后可立即键入内容。";
  }

  function renderState(state) {
    if (!state || typeof state !== "object") return false;
    currentStatus = state.status || null;
    if (dialog) dialog.dataset.status = currentStatus || "";
    showMessage(statusMessage(state), state.remaining_seconds);
    if (browserHintNode) browserHintNode.textContent = browserHint(state);

    const terminal = isTerminal(state.status);
    if (terminal) {
      showTerminalStage(state);
      setBusy(false);
      syncControls();
      if (state.status === "succeeded") {
        showMessage("登录成功，正在刷新账号列表", 0);
        if (cancelButton) cancelButton.disabled = true;
        clearSuccessTimer();
        successTimer = window.setTimeout(() => {
          if (dialog && dialog.open) dialog.close();
          window.location.reload();
        }, 1800);
      }
      return true;
    }

    showBrowserStage();
    refreshBrowserView(scanId, state.frame_revision || null);
    setBusy(true);
    syncControls();
    return false;
  }

  async function readJson(response) {
    if (response.redirected) {
      window.location.assign("/login");
      throw new Error("登录状态已失效");
    }
    let body = {};
    try {
      body = await response.json();
    } catch (_error) {
      body = {};
    }
    if (!response.ok) {
      const error = new Error(body.message || "请求未完成，请稍后重试");
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  function schedulePoll() {
    clearTimer();
    if (scanId && !closeRequested && !cancelPending && !isTerminal()) {
      timer = window.setTimeout(pollScan, POLL_INTERVAL_MS);
    }
  }

  async function pollScan() {
    if (!scanId || closeRequested || cancelPending) return;
    try {
      const response = await fetch(
        `/accounts/scan/${encodeURIComponent(scanId)}`,
        {cache: "no-store", credentials: "same-origin"},
      );
      const state = await readJson(response);
      if (closeRequested || cancelPending) return;
      pollFailureCount = 0;
      if (!renderState(state)) schedulePoll();
    } catch (_error) {
      if (!scanId || closeRequested || cancelPending) return;
      pollFailureCount += 1;
      setBusy(true);
      showMessage(
        pollFailureCount > 1
          ? `连接暂时不稳定，正在自动重试（${pollFailureCount}）`
          : "连接暂时不稳定，正在自动重试",
        0,
      );
      schedulePoll();
    }
  }

  async function startScan(accountId = null, accountName = "", launchButton = startButton) {
    setScanMode(accountId, accountName, launchButton);
    clearTimer();
    clearSuccessTimer();
    scanId = null;
    currentStatus = null;
    startPending = true;
    closeRequested = false;
    cancelPending = false;
    resetTextInteraction();
    clickPending = false;
    keyboardCaptureActive = false;
    pollFailureCount = 0;
    if (textNode) textNode.value = "";
    clearView();
    if (closeButton) closeButton.disabled = false;
    if (dialog && !dialog.open) dialog.showModal();
    if (stageNode) {
      stageNode.dataset.view = "loading";
      stageNode.setAttribute("aria-busy", "true");
    }
    if (qrPanelNode) qrPanelNode.hidden = false;
    if (resultNode) resultNode.hidden = true;
    showMessage(isRelogin() ? "正在创建重新登录会话" : "正在创建账号绑定会话", 0);
    setBusy(true);
    syncControls();
    if (cancelButton) cancelButton.disabled = false;

    try {
      const body = new URLSearchParams({csrf_token: csrfToken});
      if (targetAccountId) body.set("account_id", targetAccountId);
      const response = await fetch("/accounts/scan", {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        body,
      });
      const state = await readJson(response);
      const returnedAccountId = typeof state.account_id === "string" && state.account_id
        ? state.account_id
        : null;
      if (returnedAccountId !== targetAccountId) {
        throw new Error("扫码会话目标不一致，请关闭其他登录窗口后重试");
      }
      scanId = state.id;
      startPending = false;
      const terminal = renderState(state);
      if (closeRequested) {
        if (terminal || await cancelScan()) {
          closeRequested = false;
          if (dialog) dialog.close();
        } else {
          closeRequested = false;
        }
        return;
      }
      if (!terminal) schedulePoll();
    } catch (error) {
      startPending = false;
      if (closeRequested && !scanId) {
        closeRequested = false;
        if (closeButton) closeButton.disabled = false;
        setBusy(false);
        if (dialog) dialog.close();
        return;
      }
      setBusy(false);
      if (cancelButton) cancelButton.disabled = true;
      if (resultNode) {
        resultNode.hidden = false;
        resultNode.dataset.tone = "error";
      }
      if (qrPanelNode) qrPanelNode.hidden = true;
      if (stageNode) stageNode.dataset.view = "result";
      if (resultTitleNode) {
        resultTitleNode.textContent = isRelogin() ? "无法发起重新登录" : "无法发起绑定";
      }
      showMessage(
        error.message || (isRelogin() ? "暂时无法发起重新登录，请重试" : "暂时无法发起绑定，请重试"),
        0,
      );
      syncControls();
    }
  }

  async function cancelScan() {
    clearTimer();
    if (!scanId || isTerminal()) return true;
    if (cancelPending) return false;
    cancelPending = true;
    resetTextInteraction();
    clickPending = false;
    syncControls();
    if (closeButton) closeButton.disabled = true;
    showMessage(isRelogin() ? "正在取消重新登录" : "正在取消绑定", 0);
    try {
      const response = await fetch(
        `/accounts/scan/${encodeURIComponent(scanId)}/cancel`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
          body: new URLSearchParams({csrf_token: csrfToken}),
        },
      );
      const state = await readJson(response);
      cancelPending = false;
      if (closeButton) closeButton.disabled = false;
      renderState(state);
      return true;
    } catch (_error) {
      cancelPending = false;
      if (closeButton) closeButton.disabled = false;
      if (cancelButton) cancelButton.disabled = false;
      showMessage("取消失败，请重试", 0);
      syncControls();
      if (!closeRequested) schedulePoll();
      return false;
    }
  }

  async function requestClose(confirmClose) {
    const active = startPending || (scanId && !isTerminal());
    if (!active) {
      if (dialog) dialog.close();
      return;
    }
    const closeMessage = isRelogin()
      ? "关闭窗口将取消本次重新登录，确定继续吗？"
      : "关闭窗口将取消本次绑定，确定继续吗？";
    if (confirmClose && !window.confirm(closeMessage)) return;
    if (closeRequested || cancelPending) return;
    closeRequested = true;
    clearTimer();
    if (closeButton) closeButton.disabled = true;
    if (cancelButton) cancelButton.disabled = true;
    showMessage(isRelogin() ? "正在取消重新登录" : "正在取消绑定", 0);
    if (startPending) return;
    if (await cancelScan()) {
      closeRequested = false;
      if (dialog) dialog.close();
    } else {
      closeRequested = false;
      schedulePoll();
    }
  }

  function imageContentRect() {
    if (!qrNode || typeof qrNode.getBoundingClientRect !== "function") return null;
    const bounds = qrNode.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return null;
    let objectFit = "fill";
    try {
      if (window.getComputedStyle) objectFit = window.getComputedStyle(qrNode).objectFit || "fill";
    } catch (_error) {
      objectFit = "fill";
    }
    const naturalWidth = Number(qrNode.naturalWidth);
    const naturalHeight = Number(qrNode.naturalHeight);
    if (!naturalWidth || !naturalHeight || objectFit === "fill" || objectFit === "scale-down") return bounds;
    const sourceRatio = naturalWidth / naturalHeight;
    const boxRatio = bounds.width / bounds.height;
    if (objectFit === "contain") {
      if (sourceRatio > boxRatio) {
        const height = bounds.width / sourceRatio;
        return {left: bounds.left, top: bounds.top + (bounds.height - height) / 2, width: bounds.width, height};
      }
      const width = bounds.height * sourceRatio;
      return {left: bounds.left + (bounds.width - width) / 2, top: bounds.top, width, height: bounds.height};
    }
    if (objectFit === "cover") {
      if (sourceRatio > boxRatio) {
        const width = bounds.height * sourceRatio;
        return {left: bounds.left + (bounds.width - width) / 2, top: bounds.top, width, height: bounds.height};
      }
      const height = bounds.width / sourceRatio;
      return {left: bounds.left, top: bounds.top + (bounds.height - height) / 2, width: bounds.width, height};
    }
    return bounds;
  }

  function focusKeyboardCapture() {
    if (!textNode || textNode.disabled) return;
    try {
      textNode.focus({preventScroll: true});
    } catch (_error) {
      textNode.focus();
    }
    const end = textNode.value.length;
    if (typeof textNode.setSelectionRange === "function") {
      try {
        textNode.setSelectionRange(end, end);
      } catch (_error) {
        // Some older browsers do not expose a selection for password fields.
      }
    }
  }

  async function forwardBrowserClick(event) {
    if (!isActive()) return;
    const bounds = imageContentRect();
    if (!bounds) return;
    const clientX = Number(event.clientX);
    const clientY = Number(event.clientY);
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
    const x = Math.min(1, Math.max(0, (clientX - bounds.left) / bounds.width));
    const y = Math.min(1, Math.max(0, (clientY - bounds.top) / bounds.height));
    // A screenshot cannot own a local keyboard focus. Focus the encrypted
    // text proxy during the same trusted click gesture so desktop and mobile
    // users can type immediately after selecting a field in the cloud page.
    focusKeyboardCapture();
    keyboardCaptureActive = true;
    if (browserHintNode) {
      browserHintNode.textContent = "已选中云端位置，请直接键入内容；按回车或点击“发送到云端”。";
    }
    if (clickPending) return;
    const targetScanId = scanId;
    clickPending = true;
    try {
      const response = await fetch(
        `/accounts/scan/${encodeURIComponent(targetScanId)}/interact`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
          body: new URLSearchParams({
            csrf_token: csrfToken,
            kind: "click",
            x: String(x),
            y: String(y),
          }),
        },
      );
      await readJson(response);
      if (response.status !== 202) throw new Error("云端页面点击未确认");
    } catch (_error) {
      if (scanId === targetScanId && !isTerminal()) showMessage("云端页面点击未送达，请重试", 0);
    } finally {
      clickPending = false;
    }
  }

  function normalizeInteractionStatus(status) {
    const normalized = String(status || "").trim().toLowerCase();
    if (["queued", "processing", "delivered", "uncertain", "failed"].includes(normalized)) {
      return normalized;
    }
    if (normalized === "claimed") return "processing";
    if (["accepted", "pending"].includes(normalized)) return "queued";
    if (["completed", "succeeded"].includes(normalized)) return "delivered";
    return null;
  }

  function interactionDeliveryStatus(state) {
    if (!state || typeof state !== "object") return null;
    return normalizeInteractionStatus(state.delivery_status || state.status);
  }

  function interactionAttemptNumber(state, fallback) {
    if (!state || typeof state !== "object") return fallback;
    const value = Number.parseInt(
      state.session_text_attempt_number ?? state.attempt_number,
      10,
    );
    return Number.isFinite(value) && value > 0 ? value : fallback;
  }

  function interactionStatusUrl(value, targetScanId, interactionId = null) {
    const prefix = `/accounts/scan/${encodeURIComponent(targetScanId)}/`;
    if (typeof value === "string") {
      const candidate = value.trim();
      if (candidate.startsWith(prefix) && !candidate.startsWith("//")) return candidate;
    }
    const id = typeof interactionId === "number"
      ? interactionId
      : (typeof interactionId === "string" && /^\d+$/.test(interactionId)
        ? Number(interactionId)
        : NaN);
    if (!Number.isSafeInteger(id) || id < 1) return null;
    return `${prefix}interactions/${encodeURIComponent(String(id))}`;
  }

  function attemptWarning(attemptNumber) {
    return attemptNumber >= 3
      ? `注意：这是本会话第 ${attemptNumber} 次文字尝试。连续输错可能触发抖音冷却，请先确认云端页面确实要求再次输入。`
      : `本会话第 ${attemptNumber} 次文字输入`;
  }

  function isCurrentTextInteraction(interaction) {
    return activeTextInteraction === interaction
      && interaction.generation === textFlowGeneration
      && interaction.scanId === scanId
      && isActive();
  }

  function finishTextInteraction(interaction, status) {
    if (!isCurrentTextInteraction(interaction)) return;
    clearInteractionTimer();
    activeTextInteraction = null;
    textInteractionPending = false;
    uncertainConfirmationRequired = status === "uncertain";
    const lead = attemptWarning(interaction.attemptNumber);
    if (status === "delivered") {
      setInteractionMessage(
        "delivered",
        `${lead}已送达一次。请先确认上方云端页面的提示，再输入下一项。`,
      );
    } else if (status === "failed") {
      setInteractionMessage(
        "failed",
        `${lead}未被云端处理。请查看页面提示后再决定是否重试。`,
      );
    } else {
      setInteractionMessage(
        "uncertain",
        `${lead}的结果无法确认。请先查看上方云端画面；仅在确认没有输入后再手动重试，系统不会自动重发。`,
      );
    }
    beginTextCooldown();
    syncControls();
  }

  function scheduleTextInteractionPoll(interaction) {
    if (!isCurrentTextInteraction(interaction)) return;
    clearInteractionTimer();
    interactionTimer = window.setTimeout(
      () => pollTextInteraction(interaction),
      INTERACTION_POLL_INTERVAL_MS,
    );
  }

  function waitForLegacyInteraction(interaction) {
    if (!isCurrentTextInteraction(interaction)) return;
    clearInteractionTimer();
    interactionTimer = window.setTimeout(() => {
      if (!isCurrentTextInteraction(interaction)) return;
      finishTextInteraction(interaction, "uncertain");
    }, INTERACTION_FALLBACK_WAIT_MS);
  }

  function renderTextInteractionState(interaction, status) {
    if (!isCurrentTextInteraction(interaction)) return;
    const lead = attemptWarning(interaction.attemptNumber);
    if (status === "delivered" || status === "failed" || status === "uncertain") {
      finishTextInteraction(interaction, status);
      return;
    }
    textInteractionPending = true;
    uncertainConfirmationRequired = false;
    if (interaction.resumed) {
      setInteractionMessage(
        status === "processing" ? "processing" : "queued",
        `上一条文字输入仍在${status === "processing" ? "处理" : "排队"}，已恢复原回执；刚才的内容没有再次发送。`,
      );
    } else if (status === "processing") {
      setInteractionMessage(
        "processing",
        `${lead}正在由云端处理，请勿重复发送。`,
      );
    } else {
      setInteractionMessage(
        "queued",
        `${lead}已排队，正在等待云端回执，请勿重复发送。`,
      );
    }
    syncControls();
    if (interaction.statusUrl) scheduleTextInteractionPoll(interaction);
    else waitForLegacyInteraction(interaction);
  }

  async function pollTextInteraction(interaction) {
    if (!isCurrentTextInteraction(interaction)) return;
    interaction.polls += 1;
    if (interaction.polls > INTERACTION_MAX_POLLS) {
      finishTextInteraction(interaction, "uncertain");
      return;
    }
    try {
      const response = await fetch(interaction.statusUrl, {
        cache: "no-store",
        credentials: "same-origin",
      });
      const state = await readJson(response);
      if (!isCurrentTextInteraction(interaction)) return;
      const status = interactionDeliveryStatus(state);
      if (!status) {
        finishTextInteraction(interaction, "uncertain");
        return;
      }
      renderTextInteractionState(interaction, status);
    } catch (_error) {
      if (!isCurrentTextInteraction(interaction)) return;
      if (interaction.polls >= INTERACTION_MAX_POLLS) {
        finishTextInteraction(interaction, "uncertain");
        return;
      }
      setInteractionMessage(
        "queued",
        `${attemptWarning(interaction.attemptNumber)}已排队，回执查询暂时中断，仍不会自动重复输入。`,
      );
      scheduleTextInteractionPoll(interaction);
    }
  }

  async function sendBrowserText() {
    if (!isActive() || textControlsLocked() || !textNode) return;
    // Preserve leading/trailing spaces: they are valid password characters.
    const value = textNode.value;
    const characterCount = Array.from(value).length;
    const hasControlCharacters = /[\u0000-\u001F\u007F-\u009F\u2028\u2029]/u.test(value);
    if (characterCount < 1
      || characterCount > MAX_BROWSER_TEXT_LENGTH
      || hasControlCharacters) {
      setInteractionMessage("failed", "请输入 1–256 个可打印字符。");
      textNode.focus();
      return;
    }

    const nextAttemptNumber = textAttemptCount + 1;
    const confirmationMessages = [];
    if (uncertainConfirmationRequired) {
      confirmationMessages.push(
        "上一次文字输入结果无法确认。请先查看云端画面，只有确认上一次没有被输入时才继续。",
      );
    }
    if (nextAttemptNumber >= 3) {
      confirmationMessages.push(
        `这是本会话第 ${nextAttemptNumber} 次文字尝试，连续输错可能触发抖音安全冷却。请确认页面明确要求再次输入。`,
      );
    }
    if (confirmationMessages.length) {
      const confirmed = typeof window.confirm === "function"
        && window.confirm(`${confirmationMessages.join("\n\n")}\n\n点击“确定”只发送一次；点击“取消”不会发送。`);
      if (!confirmed) return;
      uncertainConfirmationRequired = false;
    }

    const targetScanId = scanId;
    const generation = textFlowGeneration;
    const requestId = createRequestId();
    const requestBody = new URLSearchParams({
      csrf_token: csrfToken,
      kind: "text",
      text: value,
      request_id: requestId,
    });
    textPending = true;
    syncControls();
    setInteractionMessage(
      "submitting",
      `${attemptWarning(nextAttemptNumber)}正在安全提交，请勿重复点击或按回车。`,
    );
    let response = null;
    try {
      response = await fetch(
        `/accounts/scan/${encodeURIComponent(targetScanId)}/interact`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
          body: requestBody,
        },
      );
      const state = await readJson(response);
      if (![200, 202].includes(response.status) || state.accepted !== true) {
        throw new Error("云端未接受本次输入，请稍后重试");
      }
      if (generation === textFlowGeneration && scanId === targetScanId && isActive()) {
        const acceptedAttemptNumber = interactionAttemptNumber(state, nextAttemptNumber);
        textAttemptCount = Math.max(textAttemptCount, acceptedAttemptNumber);
        textNode.value = "";
        keyboardCaptureActive = false;
        if (browserHintNode) {
          browserHintNode.textContent = "本次文字输入正在等待云端回执，请勿重复发送。";
        }
        const interaction = {
          attemptNumber: acceptedAttemptNumber,
          generation,
          id: state.interaction_id ?? null,
          polls: 0,
          requestId,
          scanId: targetScanId,
          statusUrl: interactionStatusUrl(
            state.status_url,
            targetScanId,
            state.interaction_id,
          ),
        };
        activeTextInteraction = interaction;
        textPending = false;
        renderTextInteractionState(
          interaction,
          interactionDeliveryStatus(state) || "queued",
        );
      }
    } catch (error) {
      if (generation === textFlowGeneration && scanId === targetScanId && isActive()) {
        const conflictState = error && error.status === 409
          && error.body && error.body.error === "interaction_in_progress"
          ? error.body
          : null;
        if (conflictState) {
          const existingAttemptNumber = interactionAttemptNumber(
            conflictState,
            Math.max(1, textAttemptCount),
          );
          textAttemptCount = Math.max(textAttemptCount, existingAttemptNumber);
          const interaction = {
            attemptNumber: existingAttemptNumber,
            generation,
            id: conflictState.interaction_id ?? null,
            polls: 0,
            requestId: typeof conflictState.request_id === "string"
              ? conflictState.request_id
              : null,
            resumed: true,
            scanId: targetScanId,
            statusUrl: interactionStatusUrl(
              conflictState.status_url,
              targetScanId,
              conflictState.interaction_id,
            ),
          };
          activeTextInteraction = interaction;
          textPending = false;
          renderTextInteractionState(
            interaction,
            interactionDeliveryStatus(conflictState) || "queued",
          );
        } else if (response === null) {
          textAttemptCount = Math.max(textAttemptCount, nextAttemptNumber);
          uncertainConfirmationRequired = true;
          setInteractionMessage(
            "uncertain",
            `${attemptWarning(nextAttemptNumber)}的提交结果无法确认。请先查看云端画面；确认没有输入后才可手动重试，系统不会自动重发。`,
          );
          beginTextCooldown();
        } else {
          setInteractionMessage(
            "failed",
            error.message || "云端未接受本次输入，请稍后重试。",
          );
          beginTextCooldown();
        }
        textNode.focus();
      }
    } finally {
      if (generation === textFlowGeneration && scanId === targetScanId) {
        textPending = false;
        syncControls();
      }
    }
  }

  if (qrNode) qrNode.addEventListener("click", forwardBrowserClick);
  if (startButton) {
    startButton.addEventListener("click", () => startScan(null, "", startButton));
  }
  for (const button of reloginButtons) {
    button.addEventListener("click", () => startScan(
      button.dataset.scanAccountId || null,
      button.dataset.scanAccountName || "",
      button,
    ));
  }
  if (closeButton) closeButton.addEventListener("click", () => requestClose(true));
  if (cancelButton) cancelButton.addEventListener("click", () => requestClose(false));
  if (typeButton) typeButton.addEventListener("click", sendBrowserText);
  if (textNode) {
    textNode.addEventListener("input", () => {
      if (!keyboardCaptureActive || !browserHintNode) return;
      const count = Array.from(textNode.value).length;
      browserHintNode.textContent = count
        ? `已选中云端位置，已输入 ${count} 个字符；按回车或点击“发送到云端”。`
        : "已选中云端位置，请直接键入内容；按回车或点击“发送到云端”。";
    });
    textNode.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      sendBrowserText();
    });
  }
  if (dialog) {
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      requestClose(true);
    });
  }
  window.addEventListener("pagehide", () => {
    if (!scanId || isTerminal()) return;
    clearTimer();
    clearSuccessTimer();
    navigator.sendBeacon(
      `/accounts/scan/${encodeURIComponent(scanId)}/cancel`,
      new URLSearchParams({csrf_token: csrfToken}),
    );
    scanId = null;
    currentStatus = null;
    keyboardCaptureActive = false;
    resetTextInteraction();
    if (textNode) textNode.value = "";
    clearView();
  });

  clearView();
  syncControls();
})();
