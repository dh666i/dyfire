(() => {
  "use strict";

  const DEFAULT_TARGET_NAME_MAX = 256;

  function foldTargetName(value) {
    return String(value || "")
      .trim()
      .toLocaleLowerCase("und")
      .replace(/[ßẞ]/g, "ss")
      .replace(/ς/g, "σ")
      .replace(/ﬀ/g, "ff")
      .replace(/ﬁ/g, "fi")
      .replace(/ﬂ/g, "fl")
      .replace(/ﬃ/g, "ffi")
      .replace(/ﬄ/g, "ffl")
      .replace(/ﬅ/g, "st")
      .replace(/ﬆ/g, "st");
  }

  function safeAvatarUrl(value) {
    if (typeof value !== "string" || value.length > 2048) return "";
    const candidate = value.trim();
    if (/[\x00-\x1f\\]/.test(candidate)) return "";
    try {
      const url = new URL(candidate);
      const domains = ["douyinpic.com", "douyincdn.com", "byteimg.com", "ibytedtos.com", "pstatp.com"];
      return url.protocol === "https:" && !url.username && !url.password && !url.port &&
        domains.some((domain) => url.hostname === domain || url.hostname.endsWith(`.${domain}`))
        ? candidate : "";
    } catch (_) {
      return "";
    }
  }

  function normalizeTargetItem(item) {
    const avatarUrl = safeAvatarUrl(item?.avatar_url);
    return {
      name: String(item?.name || "").trim(),
      sec_uid: String(item?.sec_uid || "").trim(),
      ...Object.fromEntries(["nickname", "remark_name", "unique_id", "short_id"].filter(key => item?.[key]).map(key => [key, String(item[key])])),
      ...(avatarUrl ? {avatar_url: avatarUrl} : {}),
    };
  }

  function targetIdentityKey(item) {
    const normalized = normalizeTargetItem(item);
    return normalized.sec_uid
      ? `sec_uid\u0000${normalized.sec_uid}`
      : `name\u0000${foldTargetName(normalized.name)}`;
  }

  function targetIsAllowed(item, maxNameLength = DEFAULT_TARGET_NAME_MAX) {
    const normalized = normalizeTargetItem(item);
    return Boolean(normalized.name) && Array.from(normalized.name).length <= maxNameLength;
  }

  function dedupeTargetItems(items, maxNameLength = DEFAULT_TARGET_NAME_MAX) {
    const accepted = new Map();
    const rejected = [];
    for (const rawItem of items || []) {
      const item = normalizeTargetItem(rawItem);
      if (!targetIsAllowed(item, maxNameLength)) {
        rejected.push(item);
        continue;
      }
      accepted.set(targetIdentityKey(item), item);
    }
    return {items: Array.from(accepted.values()), rejected};
  }

  function targetSubmissionEntries(items) {
    const entries = [];
    for (const rawItem of items || []) {
      const item = normalizeTargetItem(rawItem);
      entries.push(["target_name", item.name], ["target_sec_uid", item.sec_uid]);
    }
    return entries;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      DEFAULT_TARGET_NAME_MAX,
      dedupeTargetItems,
      foldTargetName,
      normalizeTargetItem,
      safeAvatarUrl,
      targetIdentityKey,
      targetIsAllowed,
      targetSubmissionEntries,
    };
    return;
  }

  const account = document.querySelector("#task-account");
  const refresh = document.querySelector("#refresh-targets");
  const status = document.querySelector("#target-status");
  const message = document.querySelector("#task-message");
  const messageCount = document.querySelector("#task-message-count");
  const messageKind = document.querySelector("#task-message-kind");
  const textMessageField = document.querySelector("#task-message-text-field");
  const stickerMessageField = document.querySelector("#task-message-sticker-field");
  let textDraft = message && message.value !== "续火花" ? message.value : "今日火花";

  function syncMessageKind() {
    if (!messageKind || !message) return;
    const isSticker = messageKind.value === "douyin_sticker";
    if (isSticker) {
      if (message.value && message.value !== "续火花") textDraft = message.value;
      message.value = "续火花";
      message.required = false;
      if (textMessageField) textMessageField.hidden = true;
      if (stickerMessageField) stickerMessageField.hidden = false;
    } else {
      if (message.value === "续火花") message.value = textDraft || "今日火花";
      message.required = true;
      if (textMessageField) textMessageField.hidden = false;
      if (stickerMessageField) stickerMessageField.hidden = true;
    }
    if (messageCount) messageCount.textContent = String(Array.from(message.value).length);
  }

  if (messageKind) {
    messageKind.addEventListener("change", syncMessageKind);
    syncMessageKind();
  }
  if (message && messageCount) {
    const updateMessageCount = () => {
      messageCount.textContent = String(Array.from(message.value).length);
    };
    message.addEventListener("input", updateMessageCount);
    updateMessageCount();
  }

  async function readJson(response) {
    if (response.redirected) {
      window.location.assign("/login");
      throw new Error("登录状态已失效，请重新登录");
    }
    try {
      return await response.json();
    } catch (_error) {
      throw new Error("好友列表响应异常，请稍后重试");
    }
  }

  function setStatus(messageText, tone = "neutral") {
    if (!status) return;
    status.textContent = messageText;
    status.dataset.tone = tone;
  }

  function initSingleTargetPicker() {
    const target = document.querySelector("#task-target");
    const targetSecUid = document.querySelector("#task-target-sec-uid");
    const options = document.querySelector("#task-target-options");
    if (!account || !target || !targetSecUid || !options || !refresh || !status) return false;
    let requestVersion = 0;
    let activeController = null;

    function bindSelectedIdentity() {
      const matches = Array.from(options.options).filter((item) => item.value === target.value);
      const selected = matches.find(item => item.dataset.secUid === targetSecUid.value) || (matches.length === 1 ? matches[0] : null);
      targetSecUid.value = selected?.dataset.secUid || "";
    }

    async function loadTargets(clearExisting = false) {
      const version = ++requestVersion;
      if (activeController) activeController.abort();
      activeController = typeof AbortController === "function" ? new AbortController() : null;
      refresh.disabled = true;
      refresh.setAttribute("aria-busy", "true");
      setStatus("正在读取该账号的聊天列表…", "loading");
      if (clearExisting) options.replaceChildren();
      try {
        const response = await fetch(`/accounts/${encodeURIComponent(account.value)}/conversations`, {
          cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"},
          ...(activeController ? {signal: activeController.signal} : {}),
        });
        const body = await readJson(response);
        if (!response.ok) throw new Error(body.message || "好友列表读取失败");
        if (version !== requestVersion) return;
        options.replaceChildren();
        for (const item of body.items || []) {
          if (!item || !item.name) continue;
          const option = document.createElement("option");
          option.value = item.name;
          option.dataset.secUid = item.sec_uid || "";
          options.appendChild(option);
        }
        bindSelectedIdentity();
        setStatus(body.items?.length ? `已读取 ${body.items.length} 个最近会话，可在列表中筛选` : "暂未读取到最近会话，请先重新同步", body.items?.length ? "success" : "neutral");
      } catch (error) {
        if (error?.name === "AbortError" || version !== requestVersion) return;
        setStatus(error.message || "好友列表读取失败，仍可手动输入", "error");
      } finally {
        if (version === requestVersion) {
          refresh.disabled = false;
          refresh.removeAttribute("aria-busy");
          activeController = null;
        }
      }
    }

    account.addEventListener("change", () => {
      target.value = "";
      targetSecUid.value = "";
      loadTargets(true);
    });
    target.addEventListener("input", bindSelectedIdentity);
    refresh.addEventListener("click", () => loadTargets(false));
    document.addEventListener("directory-updated", (event) => { if (event.detail.accountId === account.value) loadTargets(false); });
    window.addEventListener("pagehide", () => activeController?.abort());
    loadTargets();
    return true;
  }

  function initMultiTargetPicker() {
    const root = document.querySelector("[data-target-multiselect]");
    const form = document.querySelector("[data-task-create-form]");
    if (!root || !form || !account || !refresh || !status) return false;
    const trigger = root.querySelector("#target-picker-trigger");
    const dialog = root.querySelector("#target-picker-dialog");
    const search = root.querySelector("#target-search-input");
    const manual = root.querySelector("#target-manual-input");
    const optionList = root.querySelector("#target-option-list");
    const hiddenFields = root.querySelector("#target-hidden-fields");
    const selectedView = root.querySelector("#selected-targets");
    const summary = root.querySelector("#target-picker-summary");
    const countBadge = root.querySelector("#target-picker-count");
    const dialogCount = root.querySelector("#target-dialog-count");
    const pickerFeedback = root.querySelector("#target-picker-feedback");
    const contacts = new Map();
    let selected = new Map();
    let selectionSnapshot = new Map();
    let requestVersion = 0;
    let activeController = null;
    const maxTargetNameLength = manual.maxLength > 0 ? manual.maxLength : DEFAULT_TARGET_NAME_MAX;

    const keyFor = targetIdentityKey;
    const normalizeItem = normalizeTargetItem;
    const dialogIsOpen = () => dialog.hasAttribute("open") || dialog.open;

    function setPickerFeedback(messageText, tone = "neutral") {
      if (!pickerFeedback) return;
      pickerFeedback.hidden = !messageText;
      pickerFeedback.textContent = messageText || "";
      pickerFeedback.dataset.tone = tone;
    }

    function makeHidden(name, value) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      return input;
    }

    function syncSelection(renderList = true) {
      const items = Array.from(selected.values());
      const fields = document.createDocumentFragment();
      for (const [name, value] of targetSubmissionEntries(items)) {
        fields.append(makeHidden(name, value));
      }
      hiddenFields.replaceChildren(fields);
      selectedView.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("span");
        empty.textContent = "尚未选择好友";
        selectedView.appendChild(empty);
        summary.textContent = "请选择一个或多个好友";
      } else {
        for (const item of items) {
          const chip = document.createElement("span");
          chip.className = "selected-target-chip";
          const name = document.createElement("span");
          name.textContent = item.name;
          const remove = document.createElement("button");
          remove.type = "button";
          remove.setAttribute("aria-label", `移除好友 ${item.name}`);
          remove.textContent = "×";
          remove.addEventListener("click", () => {
            selected.delete(keyFor(item));
            syncSelection();
          });
          chip.append(name, remove);
          selectedView.appendChild(chip);
        }
        const preview = items.slice(0, 2).map((item) => item.name).join("、");
        summary.textContent = items.length > 2 ? `${preview} 等 ${items.length} 位` : preview;
      }
      countBadge.textContent = String(items.length);
      countBadge.setAttribute("aria-label", `已选择 ${items.length} 位好友`);
      dialogCount.textContent = String(items.length);
      const createCount = form.querySelector('[data-create-target-count]');
      const createSubmit = form.querySelector('[data-create-submit]');
      if (createCount) createCount.textContent = String(items.length);
      if (createSubmit && form.dataset.submitting !== 'true') createSubmit.textContent = items.length ? `创建任务（${items.length} 位好友）` : '创建任务';
      trigger.classList.toggle("has-selection", items.length > 0);
      if (renderList && dialogIsOpen()) renderOptions();
    }

    function renderEmpty(title, detail) {
      const empty = document.createElement("div");
      empty.className = "target-option-empty";
      const mark = document.createElement("span");
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "◎";
      const strong = document.createElement("strong");
      strong.textContent = title;
      const small = document.createElement("small");
      small.textContent = detail;
      empty.append(mark, strong, small);
      optionList.replaceChildren(empty);
    }

    function visibleContacts() {
      const query = search.value.trim().toLocaleLowerCase("zh-CN");
      return Array.from(contacts.values()).filter((item) => !query || [item.name, item.nickname, item.remark_name, item.unique_id, item.short_id].some(value => String(value || "").toLocaleLowerCase("zh-CN").includes(query)));
    }

    function renderOptions() {
      const items = visibleContacts();
      if (!items.length) {
        renderEmpty(contacts.size ? "没有匹配的已同步会话" : "好友列表尚未读取", contacts.size ? "换一个关键词，或先同步更多最近会话" : "关闭抽屉并点击“读取好友列表”");
        return;
      }
      const fragment = document.createDocumentFragment();
      for (const item of items) {
        const key = keyFor(item);
        const label = document.createElement("label");
        label.className = "target-option";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(key);
        checkbox.setAttribute("aria-label", `选择好友 ${item.name}`);
        const avatar = document.createElement("span");
        avatar.className = "target-option-avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatar.textContent = Array.from(item.name)[0]?.toUpperCase() || "·";
        const avatarUrl = safeAvatarUrl(item.avatar_url);
        if (avatarUrl) {
          const photo = document.createElement("img");
          photo.alt = "";
          photo.loading = "lazy";
          photo.decoding = "async";
          photo.referrerPolicy = "no-referrer";
          photo.addEventListener("error", () => photo.remove(), {once: true});
          photo.src = avatarUrl;
          avatar.appendChild(photo);
        }
        const copy = document.createElement("span");
        copy.className = "target-option-copy";
        const strong = document.createElement("strong");
        strong.textContent = item.name;
        const small = document.createElement("small");
        small.textContent = [item.nickname !== item.name ? item.nickname : "", item.unique_id ? `抖音号 ${item.unique_id}` : ""].filter(Boolean).join(" · ") || (item.sec_uid ? "已匹配好友身份" : "最近会话 · 请核对昵称");
        copy.append(strong, small);
        const check = document.createElement("i");
        check.setAttribute("aria-hidden", "true");
        check.textContent = "✓";
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.set(key, item);
          else selected.delete(key);
          syncSelection(false);
          label.classList.toggle("is-selected", checkbox.checked);
        });
        label.classList.toggle("is-selected", checkbox.checked);
        label.append(checkbox, avatar, copy, check);
        fragment.appendChild(label);
      }
      optionList.replaceChildren(fragment);
      window.sparkReveal?.(optionList);
    }

    function openPicker() {
      selectionSnapshot = new Map(selected);
      trigger.setAttribute("aria-expanded", "true");
      search.value = "";
      renderOptions();
      if (typeof dialog.showModal === "function") {
        if (!dialog.open) dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      window.setTimeout(() => search.focus(), 0);
    }

    function closePicker() {
      trigger.setAttribute("aria-expanded", "false");
      if (typeof dialog.close === "function" && dialog.open) dialog.close();
      else dialog.removeAttribute("open");
      trigger.focus();
    }

    function cancelPicker() {
      selected = new Map(selectionSnapshot);
      syncSelection(false);
      closePicker();
    }

    function addManualTarget() {
      const draft = normalizeItem({name: manual.value});
      if (!draft.name) {
        manual.focus();
        return;
      }
      if (!targetIsAllowed(draft, maxTargetNameLength)) {
        setStatus(`好友昵称最多 ${maxTargetNameLength} 个字符`, "error");
        manual.focus();
        return;
      }
      const foldedName = foldTargetName(draft.name);
      const matches = Array.from(contacts.values()).filter((candidate) => foldTargetName(candidate.name) === foldedName);
      if (matches.length > 1) {
        search.value = draft.name;
        renderOptions();
        setPickerFeedback("有多位同名好友，请通过头像或抖音号勾选具体好友", "error");
        return;
      }
      const item = matches[0] || draft;
      const key = keyFor(item);
      for (const [candidateKey, candidate] of selected) {
        if (!candidate.sec_uid && foldTargetName(candidate.name) === foldedName && candidateKey !== key) selected.delete(candidateKey);
      }
      contacts.set(key, item);
      selected.set(key, item);
      manual.value = "";
      search.value = "";
      syncSelection();
      setStatus(`已添加好友“${item.name}”`, "success");
    }

    async function loadTargets(clearSelection = false, openAfter = false) {
      const version = ++requestVersion;
      if (activeController) activeController.abort();
      activeController = typeof AbortController === "function" ? new AbortController() : null;
      if (clearSelection) {
        selected.clear();
        syncSelection(false);
      }
      refresh.disabled = true;
      refresh.setAttribute("aria-busy", "true");
      setStatus("正在读取该账号的最近会话…", "loading");
      setPickerFeedback("正在读取好友列表…", "loading");
      try {
        const response = await fetch(`/accounts/${encodeURIComponent(account.value)}/conversations`, {
          cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"},
          ...(activeController ? {signal: activeController.signal} : {}),
        });
        const body = await readJson(response);
        if (!response.ok) throw new Error(body.message || "好友列表读取失败");
        if (version !== requestVersion) return;
        contacts.clear();
        for (const item of selected.values()) {
          if (targetIsAllowed(item, maxTargetNameLength)) contacts.set(keyFor(item), item);
        }
        let ignoredLongNames = 0;
        for (const rawItem of body.items || []) {
          const item = normalizeItem(rawItem);
          if (!item.name) continue;
          if (!targetIsAllowed(item, maxTargetNameLength)) {
            ignoredLongNames += 1;
            continue;
          }
          const foldedName = foldTargetName(item.name);
          if (item.sec_uid) {
            const manualMatch = Array.from(selected.entries()).find(([, candidate]) => !candidate.sec_uid && foldTargetName(candidate.name) === foldedName);
            const unambiguous = (body.items || []).filter(candidate => candidate.sec_uid && foldTargetName(candidate.name) === foldedName).length === 1;
            if (manualMatch && unambiguous) {
              selected.delete(manualMatch[0]);
              selected.set(keyFor(item), item);
            }
            const contactManualMatch = Array.from(contacts.entries()).find(([, candidate]) => !candidate.sec_uid && foldTargetName(candidate.name) === foldedName);
            if (contactManualMatch) contacts.delete(contactManualMatch[0]);
            if (selected.has(keyFor(item))) selected.set(keyFor(item), item);
          } else if (Array.from(contacts.values()).some((candidate) => candidate.sec_uid && foldTargetName(candidate.name) === foldedName)) {
            continue;
          }
          contacts.set(keyFor(item), item);
        }
        syncSelection(false);
        renderOptions();
        const count = contacts.size;
        const ignoredNote = ignoredLongNames ? `，已忽略 ${ignoredLongNames} 个昵称过长的会话` : "";
        setStatus(count ? `已读取 ${count} 个最近会话，可在本列表搜索或多选${ignoredNote}` : `暂未读取到最近会话，请先重新同步${ignoredNote}`, count ? "success" : "neutral");
        setPickerFeedback("", "neutral");
        if (openAfter && !dialogIsOpen()) openPicker();
      } catch (error) {
        if (error?.name === "AbortError" || version !== requestVersion) return;
        const errorMessage = error?.message || "好友列表读取失败，请稍后重试";
        setStatus(errorMessage, "error");
        setPickerFeedback(errorMessage, "error");
        if (openAfter && !dialogIsOpen()) openPicker();
      } finally {
        if (version === requestVersion) {
          refresh.disabled = false;
          refresh.removeAttribute("aria-busy");
          activeController = null;
        }
      }
    }

    trigger.addEventListener("click", openPicker);
    refresh.addEventListener("click", () => {
      openPicker();
      loadTargets(false, false);
    });
    search.addEventListener("input", renderOptions);
    document.addEventListener("directory-local-search", (event) => {
      if (event.detail?.query != null) {
        search.value = event.detail.query;
        renderOptions();
      }
    });
    document.addEventListener("directory-updated", (event) => { if (event.detail.accountId === account.value) loadTargets(false, false); });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    root.querySelector("[data-target-add-manual]").addEventListener("click", addManualTarget);
    manual.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addManualTarget();
    });
    root.querySelector("[data-target-select-all]").addEventListener("click", () => {
      for (const item of visibleContacts()) selected.set(keyFor(item), item);
      syncSelection();
    });
    root.querySelector("[data-target-clear]").addEventListener("click", () => {
      selected.clear();
      syncSelection();
    });
    root.querySelector("[data-target-confirm]").addEventListener("click", closePicker);
    root.querySelectorAll("[data-target-cancel]").forEach((button) => button.addEventListener("click", cancelPicker));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      cancelPicker();
    });
    dialog.addEventListener("close", () => trigger.setAttribute("aria-expanded", "false"));
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) cancelPicker();
    });
    account.addEventListener("change", () => {
      contacts.clear();
      loadTargets(true, false);
    });
    form.addEventListener("submit", (event) => {
      if (selected.size) return;
      event.preventDefault();
      event.stopPropagation();
      setStatus("请至少选择一位目标好友", "error");
      openPicker();
    });
    window.addEventListener("pagehide", () => activeController?.abort());
    syncSelection(false);
    loadTargets(false, false);
    return true;
  }

  if (!initMultiTargetPicker()) initSingleTargetPicker();
})();
