(() => {
  "use strict";

  for (const button of document.querySelectorAll("[data-password-toggle]")) {
    const field = button.closest("[data-password-field]");
    const input = field && field.querySelector("[data-password-input]");
    if (!input) continue;

    const sync = () => {
      const visible = input.type === "text";
      const label = visible ? "隐藏密码" : "显示密码";
      button.setAttribute("aria-pressed", String(visible));
      button.setAttribute("aria-label", label);
      button.title = label;
    };

    button.addEventListener("click", () => {
      const start = input.selectionStart;
      const end = input.selectionEnd;
      input.type = input.type === "password" ? "text" : "password";
      sync();
      try {
        input.focus({preventScroll: true});
      } catch (_error) {
        input.focus();
      }
      if (start !== null && end !== null && input.setSelectionRange) {
        try {
          input.setSelectionRange(start, end);
        } catch (_error) {
          // Some mobile browsers do not restore password selections.
        }
      }
    });

    sync();
  }
})();
