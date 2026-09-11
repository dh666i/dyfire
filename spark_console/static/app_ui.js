(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const confirmDialog = document.querySelector("[data-app-confirm-dialog]");
  const confirmTitle = confirmDialog?.querySelector("[data-app-confirm-title]");
  const confirmMessage = confirmDialog?.querySelector("[data-app-confirm-message]");
  const confirmAccept = confirmDialog?.querySelector("[data-app-confirm-accept]");
  const runtimeToastRegion = document.querySelector("[data-runtime-toast-region]");
  let pendingForm = null;
  let pendingSubmitter = null;

  const openDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  };

  const closeDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  };

  const attachToastLifecycle = (toast, duration = 5200) => {
    if (!toast) return;
    let remaining = duration;
    let startedAt = Date.now();
    let timer = null;

    const dismiss = () => {
      if (!toast.isConnected || toast.dataset.closing === "true") return;
      toast.dataset.closing = "true";
      window.setTimeout(() => toast.remove(), reduceMotion ? 0 : 220);
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

  document.querySelectorAll("[data-app-toast]").forEach((toast) => attachToastLifecycle(toast));

  window.sparkToast = (message, tone = "success") => {
    if (!runtimeToastRegion || !message) return;
    const toast = document.createElement("div");
    toast.className = `app-toast app-toast-${tone}`;
    toast.setAttribute("role", tone === "error" ? "alert" : "status");
    toast.dataset.appToast = "";
    toast.innerHTML = '<span class="app-toast-mark" aria-hidden="true">✦</span><span data-toast-message></span><button class="app-toast-close" type="button" aria-label="关闭提示" data-toast-close>×</button>';
    toast.querySelector("[data-toast-message]").textContent = message;
    runtimeToastRegion.appendChild(toast);
    attachToastLifecycle(toast, 4200);
  };

  const lockForm = (form, submitter) => {
    if (form.dataset.submitting === "true") return false;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");

    const controls = form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]');
    controls.forEach((control) => {
      if (!control.disabled) control.dataset.uiLocked = '';
      control.disabled = true;
      control.setAttribute("aria-disabled", "true");
    });

    const activeSubmitter = submitter || controls[0];
    if (activeSubmitter) {
      const label = form.dataset.submittingLabel || activeSubmitter.dataset.submittingLabel || "处理中…";
      activeSubmitter.classList.add("is-loading");
      if (activeSubmitter.tagName === "INPUT") {
        activeSubmitter.dataset.originalValue = activeSubmitter.value;
        activeSubmitter.value = label;
      } else {
        const labelNode = activeSubmitter.querySelector("[data-button-label]");
        if (labelNode) {
          labelNode.dataset.originalLabel = labelNode.textContent;
          labelNode.textContent = label;
        } else {
          activeSubmitter.dataset.originalHtml = activeSubmitter.innerHTML;
          activeSubmitter.textContent = label;
        }
      }
    }
    return true;
  };

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form");
    if (!form || event.defaultPrevented) return;

    // Keep the task draft in the DOM when validation fails. Never retry a POST.
    if (form.matches('[data-task-create-form]')) {
      event.preventDefault();
      if (form.dataset.submitting === 'true') return;
      const payload = new FormData(form);
      if (!lockForm(form, event.submitter)) return;
      let feedback = form.querySelector('[data-create-feedback]');
      if (!feedback) {
        feedback = document.createElement('p'); feedback.dataset.createFeedback = '';
        feedback.className = 'ui-inline-feedback'; feedback.setAttribute('role', 'status');
        form.append(feedback);
      }
      feedback.textContent = '正在保存所选好友的任务，请稍候…';
      const unlock = () => {
        delete form.dataset.submitting; form.removeAttribute('aria-busy');
        form.querySelectorAll('[data-ui-locked]').forEach(button => {
          button.disabled = false; button.removeAttribute('aria-disabled'); delete button.dataset.uiLocked;
          button.classList.remove('is-loading');
          if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
        });
      };
      fetch(form.action, {method:'POST',body:payload,credentials:'same-origin'}).then(async response => {
        if (response.redirected && response.ok) { window.location.assign(response.url); return; }
        if ([400,403,409,422].includes(response.status)) {
          const html = new DOMParser().parseFromString(await response.text(), 'text/html');
          feedback.textContent = html.querySelector('.auth-error-message,.form-error')?.textContent.trim() || '提交未通过，请检查好友选择、发送时间和账号状态后重试。';
          feedback.setAttribute('role','alert'); unlock(); return;
        }
        throw new Error('unknown-result');
      }).catch(() => {
        // A network failure may happen after commit: leave submission locked.
        feedback.textContent = '暂时无法确认保存结果，已停止重复提交。请先查看任务列表，确认是否创建成功。当前填写内容仍保留。';
        feedback.setAttribute('role','alert');
        const link = document.createElement('a'); link.href = '/tasks'; link.target = '_blank'; link.rel = 'noopener'; link.textContent = ' 在新页面核对任务'; feedback.append(link);
      });
      return;
    }

    const confirmation = form.dataset.confirm || "";
    if (confirmation && form.dataset.confirmed !== "true") {
      event.preventDefault();
      if (!confirmDialog) {
        if (window.confirm(confirmation)) {
          form.dataset.confirmed = "true";
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

    if (form.dataset.confirmed === "true") delete form.dataset.confirmed;
    if (form.hasAttribute("data-submit-lock") && !lockForm(form, event.submitter)) {
      event.preventDefault();
    }
  });

  confirmDialog?.querySelectorAll("[data-app-confirm-cancel]").forEach((button) => {
    button.addEventListener("click", () => {
      const returnFocus = pendingSubmitter;
      pendingForm = null;
      pendingSubmitter = null;
      closeDialog(confirmDialog);
      returnFocus?.focus();
    });
  });

  confirmAccept?.addEventListener("click", () => {
    if (!pendingForm) return;
    const form = pendingForm;
    const submitter = pendingSubmitter;
    pendingForm = null;
    pendingSubmitter = null;
    closeDialog(confirmDialog);
    form.dataset.confirmed = "true";
    form.requestSubmit(submitter || undefined);
  });

  confirmDialog?.addEventListener("click", (event) => {
    if (event.target !== confirmDialog) return;
    const returnFocus = pendingSubmitter;
    pendingForm = null;
    pendingSubmitter = null;
    closeDialog(confirmDialog);
    returnFocus?.focus();
  });
})();
