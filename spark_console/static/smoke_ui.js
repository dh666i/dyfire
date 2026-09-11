import { Card } from './smoke_card.js?v=20260910-1';
const selectors = '.panel,.admin-panel,.admin-stat-card,.ops-resource-card,.studio-metric,.studio-schedule,.studio-plan-list,.studio-account-column,.studio-onboarding,.stats article,.records-list,.auth-card';
const mounted = new Map();
function mount(root) {
  const nodes = root.matches?.(selectors) ? [root,...root.querySelectorAll(selectors)] : [...root.querySelectorAll(selectors)];
  for (const node of nodes) {
    if (mounted.has(node) || node.closest('.scan-stage,.scan-frame,.target-picker-dialog')) continue;
    mounted.set(node,new Card(node));
  }
}
mount(document);
const observer = new MutationObserver(records => {
  for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) mount(node);
  for (const [node,card] of mounted) if (!node.isConnected) { card.destroy(); mounted.delete(node); }
});
observer.observe(document.body,{childList:true,subtree:true});
// Native form controls remain intact; data is never copied to a demo store.
window.addEventListener('pagehide',()=>{observer.disconnect(); for (const card of mounted.values()) card.destroy();});
