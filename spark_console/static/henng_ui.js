(() => {
  "use strict";

  function taskPage(items, query = "", state = "", requestedPage = 1, size = 12) {
    const needle = String(query).trim().toLocaleLowerCase();
    const filtered = items.filter((item) => (!state || item.state === state) && String(item.search).toLocaleLowerCase().includes(needle));
    const pages = Math.max(1, Math.ceil(filtered.length / size));
    const page = Math.min(pages, Math.max(1, Math.floor(Number(requestedPage)) || 1));
    return {items: filtered.slice((page - 1) * size, page * size), total: filtered.length, pages, page};
  }

  function scheduleHours(tasks) {
    const hours = Array.from({length: 24}, (_, hour) => ({hour, tasks: [], enabled: 0}));
    tasks.forEach((task) => {
      if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(task.time)) return;
      const bin = hours[Number(task.time.slice(0, 2))];
      bin.tasks.push(task);
      if (task.enabled) bin.enabled++;
    });
    return hours;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {taskPage, scheduleHours};
    return;
  }

  document.querySelectorAll("[data-hn-date]").forEach((element) => {
    element.textContent = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "long"}).format(new Date());
  });

  document.querySelectorAll("[data-schedule]").forEach((section) => {
    const hours = scheduleHours([...section.querySelectorAll("[data-schedule-task]")].map((element) => ({
      time: element.dataset.time, name: element.dataset.name, enabled: element.dataset.enabled === "true"
    })));
    const plot = section.querySelector("[data-schedule-plot]");
    const reset = section.querySelector("[data-schedule-reset]");
    const caption = section.querySelector("[data-schedule-caption]");
    const summary = section.querySelector("[data-schedule-summary]");
    const targets = section.querySelector("[data-schedule-targets]");
    const maxCount = Math.max(1, ...hours.map((bin) => bin.tasks.length));
    let selected = null;
    const buttons = [];
    const hourLabel = (hour) => `${String(hour).padStart(2, "0")}:00`;
    const selectHour = (hour, emit = true) => {
      selected = hour;
      buttons.forEach((button, index) => button.setAttribute("aria-pressed", String(index === selected)));
      reset.hidden = selected === null;
      caption.textContent = selected === null ? "全部时段" : `${hourLabel(selected)} - ${hourLabel(selected + 1)}`;
      const chosen = selected === null ? hours.flatMap((bin) => bin.tasks) : hours[selected].tasks;
      if (summary) summary.textContent = `${chosen.length} 个任务 · ${chosen.filter((task) => task.enabled).length} 个启用`;
      if (targets) {
        targets.replaceChildren();
        chosen.slice(0, 3).forEach((task) => {
          const target = document.createElement("span");
          target.textContent = `${task.time} ${task.name}`;
          target.title = target.textContent;
          targets.append(target);
        });
        if (chosen.length > 3) {
          const more = document.createElement("span");
          more.textContent = `+${chosen.length - 3}`;
          targets.append(more);
        }
      }
      const link = section.querySelector("[data-schedule-link]");
      if (link) link.href = selected === null ? "/studio" : `/studio?task_hour=${selected}`;
      if (emit && section.dataset.scheduleMode === "filter") section.dispatchEvent(new CustomEvent("henng:hour", {bubbles: true, detail: selected}));
    };
    hours.forEach((bin) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "hn-hour";
      button.dataset.hour = String(bin.hour);
      button.setAttribute("aria-label", `${hourLabel(bin.hour)} 至 ${hourLabel(bin.hour + 1)}，${bin.tasks.length} 个任务，${bin.enabled} 个启用`);
      button.title = button.getAttribute("aria-label");
      const well = document.createElement("span");
      well.className = "hn-hour-well";
      well.setAttribute("aria-hidden", "true");
      const bar = document.createElement("i");
      bar.className = "hn-hour-bar";
      bar.style.setProperty("--hn-bar-height", `${bin.tasks.length / maxCount * 100}%`);
      if (!bin.enabled) bar.classList.add("is-paused");
      well.append(bar);
      const label = document.createElement("span");
      label.className = "hn-hour-label";
      label.textContent = String(bin.hour).padStart(2, "0");
      label.setAttribute("aria-hidden", "true");
      button.append(well, label);
      button.addEventListener("click", () => selectHour(selected === bin.hour ? null : bin.hour));
      button.addEventListener("keydown", (event) => {
        const move = {ArrowRight: 1, ArrowLeft: -1, Home: -bin.hour, End: 23 - bin.hour}[event.key];
        if (move !== undefined) { event.preventDefault(); buttons[(bin.hour + move + 24) % 24].focus(); }
      });
      plot.append(button);
      buttons.push(button);
    });
    const urlHour = new URL(location.href).searchParams.get("task_hour");
    selectHour(urlHour !== null && /^(?:[0-9]|1[0-9]|2[0-3])$/.test(urlHour) ? Number(urlHour) : null, false);
    reset.addEventListener("click", () => selectHour(null));
    section.addEventListener("henng:clear-hour", () => selectHour(null, false));
    section.hidden = false;
  });

  const createPanel = document.querySelector("#task-create");
  if (createPanel && typeof HTMLDialogElement !== "undefined" && HTMLDialogElement.prototype.showModal) {
    const drawer = document.createElement("dialog");
    drawer.className = "hn-drawer";
    drawer.setAttribute("aria-label", "新建续火计划");
    const close = document.createElement("button");
    close.className = "icon-button hn-drawer-close";
    close.type = "button";
    close.setAttribute("aria-label", "关闭新建计划");
    close.title = "关闭";
    close.textContent = "×";
    drawer.append(close, createPanel);
    document.body.appendChild(drawer);
    let trigger = null;
    const open = (source) => {
      trigger = source;
      if (!drawer.open) drawer.showModal();
    };
    document.querySelectorAll("[data-create-open]").forEach((button) => {
      button.setAttribute("aria-haspopup", "dialog");
      button.addEventListener("click", (event) => { event.preventDefault(); open(button); });
    });
    close.addEventListener("click", () => drawer.close());
    drawer.addEventListener("click", (event) => {
      if (event.target === drawer && event.clientX < drawer.getBoundingClientRect().left) drawer.close();
    });
    drawer.addEventListener("close", () => trigger?.focus());
    if (new URL(location.href).searchParams.get("create") === "1" || location.hash === "#task-create") {
      open(document.querySelector("[data-create-open]"));
    }

    // Fetch only the authorized edit form. Failed requests retain normal navigation.
    let editController = null;
    let editDrawer = null;
    document.querySelectorAll('.task-row-action[href$="/edit"]').forEach((link) => {
      link.addEventListener("click", async (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0 || !window.henngInitTaskEditor) return;
        event.preventDefault();
        if (editController) editController.abort();
        editController = new AbortController();
        const controller = editController;
        link.setAttribute("aria-busy", "true");
        try {
          const response = await fetch(link.href, {credentials: "same-origin", cache: "no-store", signal: controller.signal});
          if (!response.ok || response.redirected) { location.assign(response.url || link.href); return; }
          const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
          const editor = parsed.querySelector(".task-edit-page");
          if (controller.signal.aborted) return;
          if (!editor) { location.assign(link.href); return; }
          if (editDrawer?.open) return;
          editDrawer = document.createElement("dialog");
          editDrawer.className = "hn-drawer hn-edit-drawer";
          editDrawer.setAttribute("aria-label", "编辑续火任务");
          editor.querySelector(".edit-back")?.remove();
          const editClose = close.cloneNode(true);
          editClose.setAttribute("aria-label", "关闭编辑任务");
          editClose.addEventListener("click", () => editDrawer.close());
          editor.querySelectorAll('.edit-actions a[href="/studio"]').forEach((cancel) => {
            cancel.addEventListener("click", (e) => { e.preventDefault(); editDrawer.close(); });
          });
          // Detach the dormant create form to keep form-control IDs unique.
          createPanel.remove();
          editDrawer.append(editClose, editor);
          document.body.appendChild(editDrawer);
          window.henngInitTaskEditor(editor);
          editDrawer.addEventListener("close", () => {
            editor.dispatchEvent(new Event("henng:dispose"));
            drawer.appendChild(createPanel);
            editDrawer.remove();
            editDrawer = null;
            link.focus();
          }, {once: true});
          editDrawer.showModal();
        } catch (error) {
          if (error.name !== "AbortError") {
            window.sparkToast?.("任务加载失败，请重试", "error");
          }
        } finally {
          link.removeAttribute("aria-busy");
          if (editController === controller) editController = null;
        }
      });
    });
    window.addEventListener("pagehide", () => editController?.abort());
  }

  const toolbar = document.querySelector("[data-task-toolbar]");
  if (toolbar) {
    const search = document.querySelector("#hn-task-search");
    const state = document.querySelector("#hn-task-state");
    const size = document.querySelector("#hn-task-size");
    const rows = [...document.querySelectorAll(".task-overview-row")];
    const items = rows.map((element) => ({element, search: element.dataset.taskSearch, state: element.dataset.taskState, hour: Number(element.dataset.taskHour)}));
    const prev = document.querySelector("[data-task-prev]");
    const next = document.querySelector("[data-task-next]");
    let currentPage = 1;
    const url = new URL(location.href);
    const initialHour = url.searchParams.get("task_hour");
    let selectedHour = initialHour !== null && /^(?:[0-9]|1[0-9]|2[0-3])$/.test(initialHour) ? Number(initialHour) : null;
    search.value = url.searchParams.get("filter") || "";
    state.value = url.searchParams.get("task_state") || "";
    const savedSize = url.searchParams.get("task_size");
    size.value = ["8", "16", "32"].includes(savedSize) ? savedSize : "8";
    currentPage = Number(url.searchParams.get("task_page")) || 1;
    function render(persist = true) {
      const result = taskPage(selectedHour === null ? items : items.filter((item) => item.hour === selectedHour), search.value, state.value, currentPage, Number(size.value));
      currentPage = result.page;
      const visible = new Set(result.items.map((item) => item.element));
      rows.forEach((row) => { row.hidden = !visible.has(row); });
      document.querySelector("[data-task-filter-count]").textContent = `${result.total} 个任务`;
      document.querySelector("[data-task-no-results]").hidden = result.total > 0;
      document.querySelector("[data-task-page-label]").textContent = `第 ${result.page} / ${result.pages} 页`;
      document.querySelector("[data-task-pagination]").hidden = result.pages <= 1;
      prev.disabled = result.page === 1;
      next.disabled = result.page === result.pages;
      if (persist) {
        const nextUrl = new URL(location.href);
        for (const [key, value] of [["filter", search.value], ["task_state", state.value], ["task_hour", selectedHour === null ? "" : String(selectedHour)], ["task_size", size.value !== "8" ? size.value : ""], ["task_page", result.page > 1 ? String(result.page) : ""]]) {
          if (value) nextUrl.searchParams.set(key, value);
          else nextUrl.searchParams.delete(key);
        }
        history.replaceState(history.state, "", nextUrl);
      }
    }
    search.addEventListener("input", () => { currentPage = 1; render(); });
    state.addEventListener("change", () => { currentPage = 1; render(); });
    size.addEventListener("change", () => { currentPage = 1; render(); });
    prev.addEventListener("click", () => { currentPage--; render(); });
    next.addEventListener("click", () => { currentPage++; render(); });
    document.querySelector("[data-task-filter-clear]").addEventListener("click", () => {
      search.value = ""; state.value = ""; currentPage = 1; selectedHour = null;
      document.querySelector('[data-schedule-mode="filter"]')?.dispatchEvent(new Event("henng:clear-hour"));
      render();
    });
    document.addEventListener("henng:hour", (event) => { selectedHour = event.detail; currentPage = 1; render(); });
    toolbar.hidden = false;
    render(false);
  }

  document.querySelectorAll(".admin-table").forEach((table) => {
    const headings = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim() || "选择");
    table.querySelectorAll("tbody tr").forEach((row) => {
      [...row.cells].forEach((cell, index) => { cell.dataset.label = headings[index] || ""; });
    });
    table.classList.add("hn-responsive-table");
  });

  if (window.visualViewport) {
    const fitDrawers = () => {
      document.documentElement.style.setProperty("--hn-viewport-height", `${window.visualViewport.height}px`);
      document.documentElement.style.setProperty("--hn-viewport-top", `${window.visualViewport.offsetTop}px`);
    };
    window.visualViewport.addEventListener("resize", fitDrawers);
    window.visualViewport.addEventListener("scroll", fitDrawers);
    fitDrawers();
  }

  // Password and verification values are never persisted by UI state recovery.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll('form[data-submitting="true"]').forEach((form) => {
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll('[data-submit-was-disabled]').forEach((control) => {
        control.disabled = control.dataset.submitWasDisabled === "true";
        if (!control.disabled) control.removeAttribute("aria-disabled");
        delete control.dataset.submitWasDisabled;
        control.classList.remove("is-loading");
        if (control.dataset.originalHtml !== undefined) {
          control.innerHTML = control.dataset.originalHtml;
          delete control.dataset.originalHtml;
        }
        if (control.dataset.originalValue !== undefined) {
          control.value = control.dataset.originalValue;
          delete control.dataset.originalValue;
        }
        control.querySelectorAll("[data-original-label]").forEach((label) => {
          label.textContent = label.dataset.originalLabel;
          delete label.dataset.originalLabel;
        });
      });
    });
  });
})();
