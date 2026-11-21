import React from 'react';
import {contactKey} from './contact-picker-state.js';
import {streakSnapshot} from './spark-streak-state.js';
import {reducedWorkflowMotion} from './workflow-motion.jsx';

// Measure the visible row before React moves it into its new sort position.
export class SyncedContactList extends React.Component {
  list = React.createRef();

  getSnapshotBeforeUpdate(previous) {
    if (previous.scope !== this.props.scope || !previous.items.length) return null;
    const refreshed = previous.revision !== this.props.revision;
    if (!refreshed && previous.syncing === this.props.syncing) return null;

    const scroller = this.list.current.closest('.contact-picker-content');
    const changes = new Set();
    if (refreshed) {
      const old = new Map(previous.items.map(item => [contactKey(item), streakSnapshot(item.spark)]));
      for (const item of this.props.items) {
        const key = contactKey(item);
        const before = old.get(key);
        const after = streakSnapshot(item.spark);
        if (after.stale) continue;
        const lit = after.state === 'active' && before?.state !== 'active';
        const daysChanged = before && after.days !== null && after.days !== before.days;
        if (lit || daysChanged) changes.add(key);
      }
    }
    const anchors = [];
    if (scroller && scroller.scrollTop > 0) {
      const viewport = scroller.getBoundingClientRect();
      for (const row of this.list.current.children) {
        const rect = row.getBoundingClientRect();
        if (rect.top >= viewport.bottom) break;
        if (rect.bottom > viewport.top) anchors.push([row.dataset.contactId, rect.top - viewport.top]);
      }
    }
    return {scroller, anchors, changes};
  }

  componentDidUpdate(previous, previousState, snapshot) {
    if (!snapshot) return;
    const {scroller, anchors, changes} = snapshot;
    const rows = new Map(Array.from(this.list.current.children, row => [row.dataset.contactId, row]));
    const anchor = anchors.find(([id]) => rows.has(id) && !changes.has(id)) || anchors.find(([id]) => rows.has(id));
    if (anchor) {
      scroller.scrollTop += rows.get(anchor[0]).getBoundingClientRect().top - scroller.getBoundingClientRect().top - anchor[1];
    }
    if (reducedWorkflowMotion()) return;
    for (const key of changes) {
      const badge = rows.get(key)?.querySelector('.streak-indicator');
      badge?.animate?.([
        {backgroundColor:'var(--ui-row-selected)', boxShadow:'0 0 0 3px var(--ui-row-selected)'},
        {backgroundColor:'transparent', boxShadow:'0 0 0 3px transparent'},
      ], {duration:1100, easing:'ease-out'});
    }
  }

  render() {
    return <div className="contact-list"><div className="contact-motion-list" ref={this.list}>{this.props.children}</div></div>;
  }
}
