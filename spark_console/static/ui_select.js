/* Progressive enhancement: native select remains the form/data source of truth. */
(() => {
  'use strict';
  if (window.SparkSelect) { window.SparkSelect.enhance(document); return; }
  const instances = new Map();
  let serial = 0, opened = null;
  const fine = () => !matchMedia('(prefers-reduced-motion: reduce)').matches;
  class SelectControl {
    constructor(source) {
      this.source = source;
      this.active = -1;
      this.buffer = '';
      this.wrapper = document.createElement('span');
      this.wrapper.className = 'ui-select';
      source.before(this.wrapper);
      this.wrapper.append(source);
      source.classList.add('ui-select-source');
      this.oldTabIndex = source.getAttribute('tabindex');
      this.oldAriaHidden = source.getAttribute('aria-hidden');
      source.tabIndex = -1;
      source.setAttribute('aria-hidden', 'true');
      this.trigger = document.createElement('button');
      this.trigger.type = 'button';
      this.trigger.className = 'ui-select-trigger';
      this.trigger.setAttribute('role', 'combobox');
      this.trigger.setAttribute('aria-haspopup', 'listbox');
      this.trigger.setAttribute('aria-expanded', 'false');
      this.caption = document.createElement('span');
      this.caption.className = 'ui-select-caption';
      const arrow = document.createElement('span');
      arrow.className = 'ui-select-chevron';
      arrow.setAttribute('aria-hidden', 'true');
      this.trigger.append(this.caption, arrow);
      this.wrapper.append(this.trigger);
      this.popup = document.createElement('div');
      this.popup.className = 'ui-select-popup';
      this.popup.id = `ui-select-options-${++serial}`;
      this.popup.setAttribute('role', 'presentation');
      this.list = document.createElement('div');
      this.list.id = `${this.popup.id}-list`;
      this.list.setAttribute('role', 'listbox');
      this.searchBox = document.createElement('div');
      this.searchBox.className = 'ui-select-search';
      this.search = document.createElement('input');
      this.search.type = 'search';
      this.search.placeholder = '搜索选项';
      this.search.setAttribute('aria-label', '搜索选项');
      this.search.setAttribute('aria-controls', this.list.id);
      this.search.setAttribute('role', 'combobox');
      this.search.setAttribute('aria-expanded', 'true');
      this.search.setAttribute('aria-autocomplete', 'list');
      this.searchBox.append(this.search);
      this.search.addEventListener('input', () => { this.build(); this.position(); });
      this.search.addEventListener('keydown', event => {
        if (event.isComposing) return;
        if (['ArrowDown','ArrowUp','Enter','Escape'].includes(event.key)) this.onKey(event);
        if (event.key === 'Tab') this.close();
      });
      this.popup.hidden = true;
      this.popup.setAttribute('popover', 'manual');
      this.trigger.setAttribute('aria-controls', this.list.id);
      // Touch focus changes may dismiss the popup between pointerdown and click.
      // Preserve the intended toggle, and suppress label activation of the source.
      this.pointerOpen = null;
      this.trigger.addEventListener('pointerdown', () => { this.pointerOpen = !!this.isOpen; });
      this.trigger.addEventListener('pointercancel', () => { this.pointerOpen = null; });
      this.toggle = event => {
        event.preventDefault();
        const shouldClose = this.pointerOpen === true || this.isOpen;
        this.pointerOpen = null;
        shouldClose ? this.close() : this.open();
      };
      this.key = event => this.onKey(event);
      this.change = () => this.sync();
      this.focus = () => this.trigger.focus();
      this.invalid = event => { event.preventDefault(); this.trigger.setAttribute('aria-invalid', 'true'); this.trigger.title = source.validationMessage; this.trigger.focus(); };
      this.reset = () => { this.close(); clearTimeout(this.resetTimer); this.resetTimer = setTimeout(() => this.sync(), 0); };
      this.pick = event => { const row = event.target.closest('[data-option-index]'); if (row && this.popup.contains(row)) this.choose(Number(row.dataset.optionIndex)); };
      this.trigger.addEventListener('click', this.toggle);
      this.trigger.addEventListener('keydown', this.key);
      source.addEventListener('change', this.change);
      source.addEventListener('input', this.change);
      source.addEventListener('focus', this.focus);
      source.addEventListener('invalid', this.invalid);
      source.form?.addEventListener('reset', this.reset);
      this.popup.addEventListener('click', this.pick);
      this.sync();
      instances.set(source, this);
    }
    name() {
      if (this.source.getAttribute('aria-label')) return this.source.getAttribute('aria-label');
      const referenced = this.source.getAttribute('aria-labelledby');
      if (referenced) return referenced.split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
      const label = this.source.labels?.[0]?.cloneNode(true);
      label?.querySelectorAll('select,.ui-select,button').forEach(node => node.remove());
      return label?.textContent.trim() || '请选择';
    }
    enabled(i) {
      const option = this.source.options[i];
      return option && !option.disabled && !option.hidden && !(option.parentElement.tagName === 'OPTGROUP' && option.parentElement.disabled);
    }
    sync() {
      const s = this.source;
      this.trigger.disabled = s.disabled;
      this.trigger.setAttribute('aria-label', this.name());
      this.trigger.setAttribute('aria-required', String(s.required));
      this.popup.setAttribute('aria-label', this.name());
      this.list.setAttribute('aria-label', this.name());
      this.caption.textContent = s.options[s.selectedIndex]?.textContent || '请选择';
      if (s.validity.valid) { this.trigger.removeAttribute('aria-invalid'); this.trigger.removeAttribute('title'); }
      if (s.disabled) this.close();
      if (this.isOpen) { this.build(); this.position(); }
    }
    build() {
      const fragment = document.createDocumentFragment();
      let group = null;
      [...this.source.options].forEach((option, i) => {
        if (option.hidden || !option.textContent.toLocaleLowerCase().includes(this.search.value.trim().toLocaleLowerCase())) return;
        if (option.parentElement.tagName === 'OPTGROUP' && group !== option.parentElement) {
          group = option.parentElement;
          const label = document.createElement('div'); label.className = 'ui-select-group'; label.textContent = group.label; fragment.append(label);
        }
        const row = document.createElement('div');
        row.className = 'ui-select-option'; row.id = `${this.popup.id}-${i}`;
        row.dataset.optionIndex = i; row.setAttribute('role', 'option');
        row.setAttribute('aria-selected', String(option.selected));
        row.setAttribute('aria-disabled', String(!this.enabled(i)));
        row.textContent = option.textContent;
        fragment.append(row);
      });
      this.list.replaceChildren(fragment);
      this.searchBox.hidden = this.source.options.length < 10;
      if (this.list.parentElement !== this.popup) this.popup.replaceChildren(this.searchBox, this.list);
      const first = this.list.querySelector('[aria-disabled=false]');
      if (!this.list.children.length) {
        const empty = document.createElement('div'); empty.className = 'ui-select-empty'; empty.textContent = '没有匹配的选项'; empty.setAttribute('role','status'); this.list.append(empty);
      }
      const visible = this.list.querySelector(`[data-option-index="${this.active}"]`);
      this.highlight(visible && this.enabled(this.active) ? this.active : first ? Number(first.dataset.optionIndex) : -1);
    }
    highlight(i) {
      this.active = i;
      this.popup.querySelectorAll('[data-option-index]').forEach(row => row.classList.toggle('is-active', Number(row.dataset.optionIndex) === i));
      const active = this.popup.querySelector(`[data-option-index="${i}"]`);
      if (active) { this.trigger.setAttribute('aria-activedescendant', active.id); active.scrollIntoView?.({block: 'nearest'}); }
      else this.trigger.removeAttribute('aria-activedescendant');
      if (active) this.search.setAttribute('aria-activedescendant', active.id);
      else this.search.removeAttribute('aria-activedescendant');
    }
    position() {
      if (!this.isOpen) return;
      const rect = this.trigger.getBoundingClientRect();
      const viewport = window.visualViewport;
      const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
      const vx = viewport?.offsetLeft || 0, vy = viewport?.offsetTop || 0;
      const w = Math.max(0, Math.min(Math.max(rect.width, 180), width - 16));
      const below = Math.max(0, vy + height - rect.bottom - 12), above = Math.max(0, rect.top - vy - 12);
      const up = below < 180 && above > below;
      const available = Math.min(280, up ? above : below);
      this.popup.style.width = `${w}px`;
      this.popup.style.maxHeight = `${available}px`;
      const actualHeight = Math.min(this.popup.scrollHeight, available);
      let left = Math.max(vx + 8, Math.min(rect.left, vx + width - w - 8));
      let top = up ? rect.top - actualHeight - 6 : rect.bottom + 6;
      if (!this.topLayer && this.host.tagName === 'DIALOG') {
        const hostRect = this.host.getBoundingClientRect();
        left -= hostRect.left + this.host.clientLeft; top -= hostRect.top + this.host.clientTop;
        top += this.host.scrollTop; left += this.host.scrollLeft;
      }
      this.popup.style.position = !this.topLayer && this.host.tagName === 'DIALOG' ? 'absolute' : 'fixed';
      this.popup.style.left = `${left}px`; this.popup.style.top = `${top}px`;
      this.popup.dataset.direction = up ? 'up' : 'down';
    }
    open() {
      if (this.source.disabled) return;
      opened?.close(); opened = this;
      this.isOpen = true; this.active = this.source.selectedIndex; this.search.value = '';
      this.host = this.source.closest('dialog') || document.body;
      this.host.append(this.popup);
      this.popup.hidden = false;
      this.build();
      this.topLayer = false;
      try { if (this.popup.showPopover) { this.popup.showPopover(); this.topLayer = true; } } catch (_) {}
      this.position();
      this.trigger.setAttribute('aria-expanded', 'true');
      this.trigger.focus();
      if (fine() && this.popup.animate) this.popup.animate([{opacity:0, transform:`translateY(${this.popup.dataset.direction==='up' ? 3 : -3}px)`},{opacity:1,transform:'translateY(0)'}],{duration:130,easing:'ease-out'});
    }
    close() {
      if (!this.isOpen) return;
      this.isOpen = false;
      try { if (this.topLayer) this.popup.hidePopover(); } catch (_) {}
      this.popup.hidden = true;
      this.popup.remove();
      this.trigger.setAttribute('aria-expanded', 'false');
      this.trigger.removeAttribute('aria-activedescendant');
      if (opened === this) opened = null;
    }
    choose(i) {
      if (!this.enabled(i)) return;
      const changed = this.source.selectedIndex !== i;
      this.source.selectedIndex = i;
      this.close(); this.sync(); this.trigger.focus();
      if (changed) {
        this.source.dispatchEvent(new Event('input', {bubbles:true}));
        this.source.dispatchEvent(new Event('change', {bubbles:true}));
      }
    }
    onKey(event) {
      const {key} = event;
      if (key === 'Escape' && this.isOpen) { event.preventDefault(); event.stopPropagation(); this.close(); this.trigger.focus(); return; }
      if (key === 'Tab') {
        if (this.isOpen && event.target === this.trigger && !this.searchBox.hidden && !event.shiftKey) { event.preventDefault(); this.search.focus(); }
        else this.close();
        return;
      }
      if (key === 'Enter' || key === ' ') { event.preventDefault(); this.isOpen ? this.choose(this.active) : this.open(); return; }
      if (['ArrowDown','ArrowUp','Home','End'].includes(key)) {
        event.preventDefault();
        if (!this.isOpen) this.open();
        const indices = [...this.list.querySelectorAll('[data-option-index]')].map(row=>Number(row.dataset.optionIndex)).filter(i=>this.enabled(i));
        if (!indices.length) return;
        const current = indices.indexOf(this.active);
        const next = key === 'Home' ? 0 : key === 'End' ? indices.length-1 : Math.max(0, Math.min(indices.length-1,current+(key==='ArrowDown'?1:-1)));
        this.highlight(indices[next]); return;
      }
      if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        clearTimeout(this.typeTimer); this.buffer += key.toLocaleLowerCase();
        this.typeTimer = setTimeout(()=>{this.buffer='';},600);
        if (!this.isOpen) this.open();
        const i = [...this.source.options].findIndex((option,index)=>this.enabled(index)&&option.textContent.trim().toLocaleLowerCase().startsWith(this.buffer));
        if (i >= 0) this.highlight(i);
      }
    }
    destroy() {
      this.close(); clearTimeout(this.typeTimer); clearTimeout(this.resetTimer);
      this.source.removeEventListener('change',this.change); this.source.removeEventListener('input',this.change);
      this.source.removeEventListener('focus',this.focus); this.source.removeEventListener('invalid',this.invalid);
      this.source.form?.removeEventListener('reset',this.reset);
      this.source.classList.remove('ui-select-source');
      if (this.oldTabIndex === null) this.source.removeAttribute('tabindex'); else this.source.setAttribute('tabindex',this.oldTabIndex);
      if (this.oldAriaHidden === null) this.source.removeAttribute('aria-hidden'); else this.source.setAttribute('aria-hidden',this.oldAriaHidden);
      if (this.wrapper.isConnected) this.wrapper.replaceWith(this.source);
      instances.delete(this.source);
    }
  }
  function enhance(root) {
    const selects = root.matches?.('select') ? [root] : [...(root.querySelectorAll?.('select') || [])];
    selects.filter(s=>!instances.has(s)&&!s.multiple&&s.size<=1&&!s.hasAttribute('data-native-select')).forEach(s=>new SelectControl(s));
  }
  const observer = new MutationObserver(records=>{
    const refresh = new Set();
    for (const r of records) {
      const source = r.target.nodeType === 1 ? (r.target.matches('select') ? r.target : r.target.closest('select')) : r.target.parentElement?.closest('select');
      if (source && instances.has(source)) refresh.add(source);
      for (const node of r.addedNodes) if (node.nodeType === 1) enhance(node);
    }
    refresh.forEach(s=>instances.get(s)?.sync());
    for (const [source,instance] of instances) if (!source.isConnected) instance.destroy();
    if (opened && !opened.source.closest('dialog')?.open && opened.host?.tagName==='DIALOG') opened.close();
  });
  const start = () => { enhance(document); observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled','selected','label','hidden','required','open']}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
  document.addEventListener('pointerdown',event=>{if(opened&&!opened.wrapper.contains(event.target)&&!opened.popup.contains(event.target))opened.close();},true);
  document.addEventListener('focusin',event=>{if(opened&&!opened.wrapper.contains(event.target)&&!opened.popup.contains(event.target))opened.close();});
  document.addEventListener('scroll',event=>{if(opened&&!opened.popup.contains(event.target))opened.position();},true);
  window.addEventListener('resize',()=>opened?.position());
  window.visualViewport?.addEventListener('resize',()=>opened?.position());
  window.visualViewport?.addEventListener('scroll',()=>opened?.position());
  window.SparkSelect = {enhance, destroy(){observer.disconnect();[...instances.values()].forEach(i=>i.destroy());}};
})();
