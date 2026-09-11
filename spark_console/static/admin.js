(function () {
  "use strict";

  var body = document.body;
  var sidebarButton = document.querySelector("[data-sidebar-open]");
  var toastRegion = document.querySelector(".admin-toast-region");
  var dialogReturnFocus = new WeakMap();

  function iconMarkup(kind) {
    if (kind === "error") {
      return '<svg class="admin-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    }
    return '<svg class="admin-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m20 6-11 11-5-5"/></svg>';
  }

  function toast(message, kind) {
    if (!toastRegion) return;
    var item = document.createElement("div");
    item.className = "admin-toast " + (kind || "success");
    item.innerHTML = iconMarkup(kind) + "<span></span>";
    item.querySelector("span").textContent = message;
    toastRegion.appendChild(item);
    var timer = window.setTimeout(function () { item.remove(); }, 3600);
    item.addEventListener("pointerenter", function () { window.clearTimeout(timer); });
    item.addEventListener("pointerleave", function () {
      timer = window.setTimeout(function () { item.remove(); }, 1800);
    });
  }

  function setSidebar(open) {
    body.classList.toggle("admin-sidebar-open", open);
    if (sidebarButton) sidebarButton.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      window.setTimeout(function () {
        var firstLink = document.querySelector("#admin-sidebar .admin-nav a");
        if (firstLink) firstLink.focus();
      }, 0);
    }
  }

  function showDialog(dialog) {
    if (!dialog) return;
    dialogReturnFocus.set(dialog, document.activeElement);
    if (typeof dialog.showModal === "function") {
      if (dialog.hasAttribute("open")) dialog.close();
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    window.setTimeout(function () {
      var focusTarget = dialog.querySelector("[autofocus], input:not([type='hidden']), select, textarea, button");
      if (focusTarget) focusTarget.focus();
    }, 0);
  }

  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
    var returnFocus = dialogReturnFocus.get(dialog);
    dialogReturnFocus.delete(dialog);
    if (returnFocus && returnFocus.isConnected && typeof returnFocus.focus === "function") {
      window.setTimeout(function () { returnFocus.focus(); }, 0);
    }
  }

  function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve, reject) {
      var input = document.createElement("textarea");
      input.value = value;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      try {
        document.execCommand("copy") ? resolve() : reject(new Error("copy-failed"));
      } catch (error) {
        reject(error);
      } finally {
        input.remove();
      }
    });
  }

  document.querySelectorAll("dialog[open]").forEach(function (dialog) {
    showDialog(dialog);
  });

  document.querySelectorAll("[data-bulk-form]").forEach(function (form) {
    var group = form.dataset.bulkForm;
    function getItems() { return Array.prototype.slice.call(document.querySelectorAll('[data-bulk-item="' + group + '"]:not(:disabled)')); }
    var toggle = document.querySelector('[data-bulk-toggle="' + group + '"]');
    var countLabel = form.querySelector("[data-bulk-count]");
    var submit = form.querySelector("[data-bulk-submit]");

    function updateBulkState() {
      var items = getItems();
      var selected = items.filter(function (item) { return item.checked; }).length;
      if (countLabel) countLabel.textContent = "已选 " + selected + " 项";
      if (submit) submit.disabled = selected === 0;
      if (toggle) {
        toggle.checked = items.length > 0 && selected === items.length;
        toggle.indeterminate = selected > 0 && selected < items.length;
      }
    }

    document.addEventListener("change", function (event) {
      if (event.target.matches('[data-bulk-item="' + group + '"]')) updateBulkState();
    });
    document.addEventListener("task-groups-loaded", updateBulkState);
    if (toggle) {
      toggle.addEventListener("change", function () {
        getItems().forEach(function (item) { item.checked = toggle.checked; });
        updateBulkState();
      });
    }
    updateBulkState();
  });

  document.addEventListener("click", function (event) {
    var openSidebar = event.target.closest("[data-sidebar-open]");
    if (openSidebar) {
      setSidebar(true);
      return;
    }

    if (event.target.closest("[data-sidebar-close]")) {
      setSidebar(false);
      return;
    }

    if (event.target.closest("#admin-sidebar a") && window.matchMedia("(max-width: 980px)").matches) {
      setSidebar(false);
    }

    var openDialogButton = event.target.closest("[data-dialog-open]");
    if (openDialogButton) {
      showDialog(document.querySelector('[data-admin-dialog="' + openDialogButton.dataset.dialogOpen + '"]'));
      return;
    }

    var closeDialogButton = event.target.closest("[data-dialog-close]");
    if (closeDialogButton) {
      closeDialog(closeDialogButton.closest("dialog"));
      return;
    }

    var copyButton = event.target.closest("[data-copy-value], [data-copy-invite]");
    if (copyButton) {
      var value = copyButton.dataset.copyValue || copyButton.dataset.copyInvite;
      copyText(value).then(function () {
        toast("已复制到剪贴板", "success");
      }).catch(function () {
        toast("复制失败，请手动选择", "error");
      });
      return;
    }

    if (event.target.closest("[data-page-refresh]")) {
      window.location.reload();
      return;
    }

    if (event.target.tagName === "DIALOG") {
      closeDialog(event.target);
      return;
    }

    var details = event.target.closest("details.admin-action-menu");
    document.querySelectorAll("details.admin-action-menu[open]").forEach(function (item) {
      if (item !== details) item.removeAttribute("open");
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setSidebar(false);
  });

  var confirmDialog = document.querySelector("[data-confirm-dialog]");
  var pendingForm = null;

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-confirm]");
    if (!form || form.dataset.confirmed === "true" || !confirmDialog) return;
    event.preventDefault();
    pendingForm = form;
    confirmDialog.querySelector("[data-confirm-title]").textContent = form.dataset.confirmTitle || "确认操作";
    confirmDialog.querySelector("[data-confirm-message]").textContent = form.dataset.confirm || "此操作可能无法撤销。";
    showDialog(confirmDialog);
  });

  document.querySelectorAll("[data-confirm-cancel]").forEach(function (button) {
    button.addEventListener("click", function () {
      pendingForm = null;
      closeDialog(confirmDialog);
    });
  });

  var confirmAccept = document.querySelector("[data-confirm-accept]");
  if (confirmAccept) {
    confirmAccept.addEventListener("click", function () {
      if (!pendingForm) return;
      pendingForm.dataset.confirmed = "true";
      closeDialog(confirmDialog);
      pendingForm.requestSubmit();
      pendingForm = null;
    });
  }

  document.addEventListener("submit", function (event) {
    if (event.defaultPrevented) return;
    var form = event.target.closest("form");
    if (!form || form.dataset.adminSubmitting === "true") {
      if (form && form.dataset.adminSubmitting === "true") event.preventDefault();
      return;
    }
    form.dataset.adminSubmitting = "true";
    form.setAttribute("aria-busy", "true");
    var submitter = event.submitter || form.querySelector('button[type="submit"], input[type="submit"]');
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (control) {
      control.dataset.adminWasDisabled = control.disabled ? "true" : "false";
      control.disabled = true;
      control.setAttribute("aria-disabled", "true");
    });
    if (submitter) {
      submitter.classList.add("is-loading");
      var label = submitter.dataset.submittingLabel || "处理中…";
      if (submitter.tagName === "INPUT") {
        submitter.dataset.adminOriginalValue = submitter.value;
        submitter.value = label;
      }
      else {
        var labelNode = submitter.querySelector("span");
        if (labelNode) {
          labelNode.dataset.adminOriginalLabel = labelNode.textContent;
          labelNode.textContent = label;
        } else {
          submitter.dataset.adminOriginalHtml = submitter.innerHTML;
          submitter.textContent = label;
        }
      }
    }
  });

  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-admin-submitting='true']").forEach(function (form) {
      delete form.dataset.adminSubmitting;
      form.removeAttribute("aria-busy");
      form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (control) {
        control.disabled = control.dataset.adminWasDisabled === "true";
        if (!control.disabled) control.removeAttribute("aria-disabled");
        delete control.dataset.adminWasDisabled;
        control.classList.remove("is-loading");
        if (control.dataset.adminOriginalValue !== undefined) {
          control.value = control.dataset.adminOriginalValue;
          delete control.dataset.adminOriginalValue;
        }
        if (control.dataset.adminOriginalHtml !== undefined) {
          control.innerHTML = control.dataset.adminOriginalHtml;
          delete control.dataset.adminOriginalHtml;
        }
        control.querySelectorAll("[data-admin-original-label]").forEach(function (labelNode) {
          labelNode.textContent = labelNode.dataset.adminOriginalLabel;
          delete labelNode.dataset.adminOriginalLabel;
        });
      });
    });
  });
})();
