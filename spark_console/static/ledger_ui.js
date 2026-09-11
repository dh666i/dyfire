(() => {
  "use strict";

  const documentRoot = document.documentElement;
  const body = document.body;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const legacyAppUiPresent = Boolean(document.querySelector('script[src*="/static/app_ui.js"]'));
  const legacyPasswordUiPresent = Boolean(document.querySelector('script[src*="/static/password_toggle.js"]'));

  documentRoot.classList.toggle("ledger-reduced-motion", reduceMotion);
  window.requestAnimationFrame(() => body.classList.add("ledger-ready"));

  const openDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
      return;
    }
    dialog.setAttribute("open", "");
  };

  const closeDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.close === "function" && dialog.open) {
      dialog.close();
      return;
    }
    dialog.removeAttribute("open");
  };

  const initClock = () => {
    const clocks = [...document.querySelectorAll("[data-live-clock]")];
    if (!clocks.length) return;

    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });

    const update = () => {
      const now = new Date();
      const value = formatter.format(now);
      clocks.forEach((clock) => {
        clock.textContent = value;
        if (clock.tagName === "TIME") clock.dateTime = now.toISOString();
      });
      window.setTimeout(update, 1000 - (Date.now() % 1000) + 20);
    };

    update();
  };

  const initSidebar = () => {
    const triggers = [...document.querySelectorAll("[data-sidebar-open], [data-sidebar-toggle]")];
    if (!triggers.length) return;

    const controlledId = triggers.map((trigger) => trigger.getAttribute("aria-controls")).find(Boolean);
    const sidebar = (controlledId && document.getElementById(controlledId))
      || document.querySelector("[data-ledger-sidebar], #app-sidebar, .sidebar");
    if (!sidebar) return;

    const mobileQuery = window.matchMedia("(max-width: 960px)");
    let returnFocus = null;

    const isOpen = () => body.classList.contains("ledger-sidebar-open");
    const sync = (open, { focus = false } = {}) => {
      body.classList.toggle("ledger-sidebar-open", open);
      body.classList.toggle("sidebar-open", open);
      sidebar.dataset.open = String(open);
      if (mobileQuery.matches) sidebar.setAttribute("aria-hidden", String(!open));
      else sidebar.removeAttribute("aria-hidden");

      triggers.forEach((trigger) => {
        trigger.setAttribute("aria-expanded", String(open));
        const label = open ? "关闭导航" : "打开导航";
        trigger.setAttribute("aria-label", label);
        if (trigger.hasAttribute("title")) trigger.title = label;
      });

      if (open && focus) {
        window.setTimeout(() => {
          sidebar.querySelector("a[href], button:not(:disabled), input:not(:disabled)")?.focus();
        }, 0);
      }
    };

    const open = (trigger) => {
      returnFocus = trigger || document.activeElement;
      sync(true, { focus: true });
    };

    const close = ({ restoreFocus = true } = {}) => {
      if (!isOpen()) return;
      sync(false);
      if (restoreFocus && returnFocus instanceof HTMLElement) returnFocus.focus();
      returnFocus = null;
    };

    document.addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-sidebar-toggle]");
      if (toggle) {
        event.preventDefault();
        if (isOpen()) close();
        else open(toggle);
        return;
      }

      const opener = event.target.closest("[data-sidebar-open]");
      if (opener) {
        event.preventDefault();
        open(opener);
        return;
      }

      if (event.target.closest("[data-sidebar-close], [data-sidebar-backdrop]")) {
        event.preventDefault();
        close();
        return;
      }

      if (mobileQuery.matches && event.target.closest(".sidebar a[href], [data-ledger-sidebar] a[href]")) {
        close({ restoreFocus: false });
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isOpen()) close();
    });

    const handleViewportChange = () => sync(false);
    if (typeof mobileQuery.addEventListener === "function") {
      mobileQuery.addEventListener("change", handleViewportChange);
    } else {
      mobileQuery.addListener(handleViewportChange);
    }
    sync(false);
  };

  const initPasswordVisibility = () => {
    if (legacyPasswordUiPresent) return;

    const sync = (button, input) => {
      const visible = input.type === "text";
      const label = visible ? "隐藏密码" : "显示密码";
      button.setAttribute("aria-pressed", String(visible));
      button.setAttribute("aria-label", label);
      button.title = label;
    };

    document.querySelectorAll("[data-password-toggle]").forEach((button) => {
      const controlledId = button.getAttribute("aria-controls");
      const input = (controlledId && document.getElementById(controlledId))
        || button.closest("[data-password-field]")?.querySelector("[data-password-input]");
      if (input) sync(button, input);
    });

    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-password-toggle]");
      if (!button) return;

      const controlledId = button.getAttribute("aria-controls");
      const input = (controlledId && document.getElementById(controlledId))
        || button.closest("[data-password-field]")?.querySelector("[data-password-input]");
      if (!input) return;

      const selectionStart = input.selectionStart;
      const selectionEnd = input.selectionEnd;
      input.type = input.type === "password" ? "text" : "password";
      sync(button, input);
      try {
        input.focus({ preventScroll: true });
      } catch (_error) {
        input.focus();
      }
      if (selectionStart !== null && selectionEnd !== null && input.setSelectionRange) {
        try {
          input.setSelectionRange(selectionStart, selectionEnd);
        } catch (_error) {
          // Some mobile browsers do not restore selections on password fields.
        }
      }
    });
  };

  const initToasts = () => {
    if (legacyAppUiPresent) return;

    const runtimeRegion = document.querySelector("[data-runtime-toast-region]");

    const attachLifecycle = (toast, duration = 5200) => {
      if (!toast || toast.dataset.ledgerToastBound === "true") return;
      toast.dataset.ledgerToastBound = "true";
      let remaining = duration;
      let startedAt = Date.now();
      let timer = null;

      const dismiss = () => {
        if (!toast.isConnected || toast.dataset.closing === "true") return;
        toast.dataset.closing = "true";
        toast.classList.add("is-closing");
        window.setTimeout(() => toast.remove(), reduceMotion ? 0 : 180);
      };

      const resume = () => {
        window.clearTimeout(timer);
        startedAt = Date.now();
        timer = window.setTimeout(dismiss, remaining);
      };

      const pause = () => {
        window.clearTimeout(timer);
        remaining = Math.max(800, remaining - (Date.now() - startedAt));
      };

      toast.querySelector("[data-toast-close]")?.addEventListener("click", dismiss);
      toast.addEventListener("pointerenter", pause);
      toast.addEventListener("pointerleave", resume);
      toast.addEventListener("focusin", pause);
      toast.addEventListener("focusout", resume);
      resume();
    };

    document.querySelectorAll("[data-app-toast]").forEach((toast) => attachLifecycle(toast));

    window.sparkToast = (message, tone = "success") => {
      if (!runtimeRegion || !message) return;
      const toast = document.createElement("div");
      const mark = document.createElement("span");
      const text = document.createElement("span");
      const close = document.createElement("button");

      toast.className = `app-toast app-toast-${tone}`;
      toast.dataset.appToast = "";
      toast.setAttribute("role", tone === "error" ? "alert" : "status");
      mark.className = "app-toast-mark";
      mark.setAttribute("aria-hidden", "true");
      text.dataset.toastMessage = "";
      text.textContent = message;
      close.className = "app-toast-close";
      close.type = "button";
      close.dataset.toastClose = "";
      close.setAttribute("aria-label", "关闭提示");
      close.textContent = "×";
      toast.append(mark, text, close);
      runtimeRegion.appendChild(toast);
      attachLifecycle(toast, 4200);
    };
  };

  const initCopyActions = () => {
    const copyText = async (value) => {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return;
      }

      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      textarea.style.pointerEvents = "none";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      if (!copied) throw new Error("copy_failed");
    };

    document.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-copy-value], [data-copy-text], [data-copy-invite], [data-copy-target]");
      if (!button || button.dataset.copyBusy === "true") return;

      const targetId = button.dataset.copyTarget;
      const target = targetId ? document.getElementById(targetId) : null;
      const value = button.dataset.copyValue
        ?? button.dataset.copyText
        ?? button.dataset.copyInvite
        ?? (target && ("value" in target ? target.value : target.textContent));
      if (!value) return;

      button.dataset.copyBusy = "true";
      try {
        await copyText(String(value));
        button.dataset.copyState = "copied";
        button.classList.add("is-copied");
        window.sparkToast?.("已复制", "success");
      } catch (_error) {
        button.dataset.copyState = "failed";
        window.sparkToast?.("复制失败，请手动复制", "error");
      } finally {
        window.setTimeout(() => {
          delete button.dataset.copyBusy;
          delete button.dataset.copyState;
          button.classList.remove("is-copied");
        }, 1600);
      }
    });
  };

  const initForms = () => {
    if (legacyAppUiPresent) return;

    const confirmDialog = document.querySelector("[data-app-confirm-dialog]");
    const confirmTitle = confirmDialog?.querySelector("[data-app-confirm-title]");
    const confirmMessage = confirmDialog?.querySelector("[data-app-confirm-message]");
    const confirmAccept = confirmDialog?.querySelector("[data-app-confirm-accept]");
    let pendingForm = null;
    let pendingSubmitter = null;

    const resetPending = ({ restoreFocus = false } = {}) => {
      const focusTarget = pendingSubmitter;
      pendingForm = null;
      pendingSubmitter = null;
      closeDialog(confirmDialog);
      if (restoreFocus && focusTarget instanceof HTMLElement) focusTarget.focus();
    };

    const lockForm = (form, submitter) => {
      if (form.dataset.submitting === "true") return false;
      form.dataset.submitting = "true";
      form.dataset.ledgerSubmitting = "true";
      form.setAttribute("aria-busy", "true");

      const controls = [...form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]')];
      controls.forEach((control) => {
        control.disabled = true;
        control.setAttribute("aria-disabled", "true");
      });

      const activeSubmitter = submitter || controls[0];
      if (!activeSubmitter) return true;
      const label = form.dataset.submittingLabel || activeSubmitter.dataset.submittingLabel || "处理中…";
      activeSubmitter.classList.add("is-loading");
      if (activeSubmitter.tagName === "INPUT") {
        activeSubmitter.dataset.ledgerOriginalValue = activeSubmitter.value;
        activeSubmitter.value = label;
        return true;
      }

      const labelNode = activeSubmitter.querySelector("[data-button-label]");
      if (labelNode) {
        labelNode.dataset.ledgerOriginalLabel = labelNode.textContent;
        labelNode.textContent = label;
      } else {
        activeSubmitter.dataset.ledgerOriginalHtml = activeSubmitter.innerHTML;
        activeSubmitter.textContent = label;
      }
      return true;
    };

    const unlockForm = (form) => {
      delete form.dataset.submitting;
      delete form.dataset.ledgerSubmitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach((control) => {
        control.disabled = false;
        control.removeAttribute("aria-disabled");
        control.classList.remove("is-loading");
        if (control.dataset.ledgerOriginalValue !== undefined) {
          control.value = control.dataset.ledgerOriginalValue;
          delete control.dataset.ledgerOriginalValue;
        }
        if (control.dataset.ledgerOriginalHtml !== undefined) {
          control.innerHTML = control.dataset.ledgerOriginalHtml;
          delete control.dataset.ledgerOriginalHtml;
        }
        const labelNode = control.querySelector("[data-button-label]");
        if (labelNode?.dataset.ledgerOriginalLabel !== undefined) {
          labelNode.textContent = labelNode.dataset.ledgerOriginalLabel;
          delete labelNode.dataset.ledgerOriginalLabel;
        }
      });
    };

    document.addEventListener("submit", (event) => {
      const form = event.target.closest("form");
      if (!form) return;

      const confirmation = form.dataset.confirm;
      if (confirmation && form.dataset.ledgerConfirmed !== "true") {
        event.preventDefault();
        if (!confirmDialog) {
          if (window.confirm(confirmation)) {
            form.dataset.ledgerConfirmed = "true";
            form.requestSubmit(event.submitter || undefined);
          }
          return;
        }

        pendingForm = form;
        pendingSubmitter = event.submitter || null;
        if (confirmTitle) confirmTitle.textContent = form.dataset.confirmTitle || "确认操作";
        if (confirmMessage) confirmMessage.textContent = confirmation;
        openDialog(confirmDialog);
        window.setTimeout(() => confirmAccept?.focus(), 0);
        return;
      }

      if (form.dataset.ledgerConfirmed === "true") delete form.dataset.ledgerConfirmed;
      if (form.hasAttribute("data-submit-lock") && !lockForm(form, event.submitter)) {
        event.preventDefault();
      }
    });

    confirmDialog?.querySelectorAll("[data-app-confirm-cancel]").forEach((button) => {
      button.addEventListener("click", () => resetPending({ restoreFocus: true }));
    });

    confirmAccept?.addEventListener("click", () => {
      if (!pendingForm) return;
      const form = pendingForm;
      const submitter = pendingSubmitter;
      resetPending();
      form.dataset.ledgerConfirmed = "true";
      form.requestSubmit(submitter || undefined);
    });

    confirmDialog?.addEventListener("click", (event) => {
      if (event.target === confirmDialog) resetPending({ restoreFocus: true });
    });

    confirmDialog?.addEventListener("cancel", (event) => {
      event.preventDefault();
      resetPending({ restoreFocus: true });
    });

    window.addEventListener("pageshow", (event) => {
      if (!event.persisted) return;
      document.querySelectorAll('form[data-ledger-submitting="true"]').forEach(unlockForm);
    });
  };

  initClock();
  initSidebar();
  initPasswordVisibility();
  initToasts();
  initCopyActions();
  initForms();
})();
