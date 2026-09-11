(() => {
  "use strict";

  const palette = document.querySelector("[data-command-dialog]");
  if (palette && typeof palette.showModal === "function") {
    const input = palette.querySelector("input");
    const items = [...palette.querySelectorAll("[data-command-item]")];
    const empty = palette.querySelector("[data-command-empty]");
    let visible = items;
    let index = 0;
    let trigger = null;
    function highlight(ensureVisible = false) {
      items.forEach((item) => item.setAttribute("aria-selected", String(item === visible[index])));
      if (visible[index]) {
        input.setAttribute("aria-activedescendant", visible[index].id);
        if (ensureVisible) visible[index].scrollIntoView?.({block: "nearest"});
      } else input.removeAttribute("aria-activedescendant");
    }
    function filter() {
      const query = input.value.trim().toLocaleLowerCase();
      visible = items.filter((item) => item.textContent.toLocaleLowerCase().includes(query));
      items.forEach((item) => { item.hidden = !visible.includes(item); });
      empty.hidden = visible.length > 0;
      index = 0;
      highlight();
    }
    function open(source) {
      // Never layer navigation over a login or an in-progress form dialog.
      if (document.querySelector("dialog[open]")) return;
      trigger = source || document.activeElement;
      input.value = "";
      filter();
      palette.showModal();
      input.focus();
    }
    document.querySelectorAll("[data-command-open]").forEach((button) => {
      button.hidden = false;
      button.setAttribute("aria-haspopup", "dialog");
      button.addEventListener("click", () => open(button));
    });
    input.addEventListener("input", filter);
    input.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && visible.length) {
        event.preventDefault();
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = visible.length - 1;
        else index = (index + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
        highlight(true);
      }
      if (event.key === "Enter") { event.preventDefault(); visible[index]?.click(); }
    });
    items.forEach((item) => item.addEventListener("pointermove", () => { index = visible.indexOf(item); highlight(); }));
    palette.querySelector("[data-command-close]").addEventListener("click", () => palette.close());
    palette.addEventListener("close", () => trigger?.focus());
    palette.addEventListener("click", (event) => {
      if (event.target !== palette) return;
      const rect = palette.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) palette.close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.isComposing || event.defaultPrevented) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        if (palette.open) { event.preventDefault(); palette.close(); }
        else if (!document.querySelector("dialog[open]")) { event.preventDefault(); open(); }
      }
    });
  }

  document.querySelectorAll("[data-workspace-tabs]").forEach((group) => {
    const tabs = [...group.querySelectorAll("[data-workspace-tab]")];
    const panels = [...group.querySelectorAll("[data-workspace-panel]")];
    const activate = (tab) => {
      tabs.forEach((item) => { item.setAttribute("aria-selected", String(item === tab)); item.tabIndex = item === tab ? 0 : -1; });
      panels.forEach((panel) => { panel.hidden = panel.id !== tab.getAttribute("aria-controls"); });
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => activate(tab));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const next = tabs[(i + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
        activate(next); next.focus();
      });
    });
    if (tabs.length) {
      activate(tabs[0]);
      group.querySelector('[role="tablist"]').hidden = false;
      group.querySelectorAll(".sp-fallback-heading").forEach((heading) => { heading.hidden = true; });
    }
  });

  document.querySelectorAll("[data-profile-menu]").forEach((menu) => {
    document.addEventListener("pointerdown", (event) => { if (!menu.contains(event.target)) menu.open = false; });
    menu.addEventListener("keydown", (event) => { if (event.key === "Escape") { menu.open = false; menu.querySelector("summary").focus(); } });
  });

  document.querySelectorAll("[data-density-control]").forEach((control) => {
    const buttons = [...control.querySelectorAll("[data-density]")];
    const list = document.querySelector(".task-overview");
    if (!list) return;
    const apply = (value) => {
      list.dataset.density = value;
      buttons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.density === value)));
      try { localStorage.setItem("spark-task-density", value); } catch (_) {}
    };
    let saved = "comfortable";
    try { if (localStorage.getItem("spark-task-density") === "compact") saved = "compact"; } catch (_) {}
    apply(saved);
    buttons.forEach((button) => button.addEventListener("click", () => apply(button.dataset.density)));
    control.hidden = false;
  });
})();
