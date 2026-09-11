/* Only view state is remembered: never passwords, cookies or message drafts. */
(() => {
  'use strict';
  const key = `spark-view:${location.pathname}${location.search}`;
  const read = () => { try { return JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { return null; } };
  const detailKey = node => node.dataset.groupUrl || node.dataset.runId || node.id;
  const details = () => [...document.querySelectorAll('details[data-task-group],details[data-run-id]')];
  const state = read();
  if (state && Date.now() - state.at < 3600000) {
    for (const node of details()) if (state.open?.includes(detailKey(node))) node.open = true;
    const navigation = performance.getEntriesByType?.('navigation')[0]?.type;
    if (navigation === 'reload' || navigation === 'back_forward') requestAnimationFrame(() => window.scrollTo(0, state.scroll || 0));
  }
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(key, JSON.stringify({at:Date.now(),scroll:window.scrollY,open:details().filter(node=>node.open).map(detailKey)})); } catch (_) {}
  });
  // A cached page must not retain a disabled submit button after navigating back.
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    document.querySelectorAll('form[data-submitting=true]').forEach(form => {
      delete form.dataset.submitting; form.removeAttribute('aria-busy');
      form.querySelectorAll('[data-ui-locked]').forEach(button => {
        button.disabled = false; button.removeAttribute('aria-disabled'); delete button.dataset.uiLocked;
      });
      form.querySelectorAll('.is-loading').forEach(button => {
        button.classList.remove('is-loading');
        if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
        if (button.dataset.originalValue) button.value = button.dataset.originalValue;
        const label = button.querySelector('[data-original-label]');
        if (label) label.textContent = label.dataset.originalLabel;
      });
    });
  });
})();
