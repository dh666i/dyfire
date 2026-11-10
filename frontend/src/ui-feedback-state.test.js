import test from 'node:test';
import assert from 'node:assert/strict';
import {createFeedbackTimers, feedbackOptions, FEEDBACK_DURATION, FEEDBACK_LIFETIME} from './ui-feedback-state.js';

function setup() {
  let time = 0, next = 0;
  const callbacks = new Map(), expired = [];
  const timers = createFeedbackTimers({now: () => time,
    schedule: (callback, duration) => { const id = ++next; callbacks.set(id, {callback, due: time + duration}); return id; },
    cancel: id => callbacks.delete(id), onExpire: notice => expired.push(notice.id)});
  const advance = duration => {
    const end = time + duration;
    for (;;) {
      const job = [...callbacks].filter(([,v]) => v.due <= end).sort((a,b) => a[1].due - b[1].due)[0];
      if (!job) break;
      time = job[1].due; callbacks.delete(job[0]); job[1].callback();
    }
    time = end;
  };
  return {timers, advance, expired, callbacks};
}
const notice = (id, options = {}, type = 'success') => ({id, type, ...feedbackOptions(options)});

test('hover pauses the remaining reading time, and leaving resumes without resetting it', () => {
  const {timers, advance, expired} = setup();
  const saved = notice('saved'); timers.sync([saved]);
  advance(1200); timers.setPaused(true); advance(10000);
  timers.sync([{...saved}]); assert.deepEqual(expired, []);
  timers.setPaused(false); advance(FEEDBACK_DURATION - 1200 - 1);
  assert.deepEqual(expired, []); advance(1); assert.deepEqual(expired, ['saved']);
});

test('queue changes while hovered cannot unpause the remaining notice', () => {
  const {timers, advance, expired} = setup();
  const one = notice('one'), two = notice('two');
  timers.sync([one,two]); advance(500); timers.setPaused(true);
  timers.sync([one]); advance(10000); assert.deepEqual(expired, []);
  timers.setPaused(false); advance(FEEDBACK_DURATION - 500);
  assert.deepEqual(expired, ['one']);
});

test('same-id updates get new reading time and still respect keyboard focus', () => {
  const {timers, advance, expired} = setup();
  timers.sync([notice('operation')]); advance(4000); timers.setPaused(true);
  timers.sync([notice('operation', {duration: 1800})]); advance(9000);
  assert.deepEqual(expired, []); timers.setPaused(false); advance(1799);
  assert.deepEqual(expired, []); advance(1); assert.deepEqual(expired, ['operation']);
});

test('loading and infinite notices never expire, and loading-to-success starts a clock', () => {
  const {timers, advance, expired, callbacks} = setup();
  const permanent = notice('pinned', {duration: Infinity});
  timers.sync([notice('request', {}, 'loading'),permanent]); advance(100000);
  assert.deepEqual(expired, []); assert.equal(callbacks.size, 0);
  timers.sync([notice('request'),permanent]); advance(FEEDBACK_DURATION);
  assert.deepEqual(expired, ['request']); assert.equal(callbacks.size, 0);
});

test('dismissal and host cleanup cancel clocks, and expired notices do not restart', () => {
  const {timers, advance, expired, callbacks} = setup();
  timers.sync([notice('removed')]); advance(100); timers.sync([]); advance(10000);
  assert.deepEqual(expired, []);
  const done = notice('done'); timers.sync([done]); advance(FEEDBACK_DURATION);
  timers.sync([{...done}]); advance(FEEDBACK_DURATION); assert.deepEqual(expired, ['done']);
  timers.sync([notice('unmount')]); timers.clear(); advance(10000);
  assert.deepEqual(expired, ['done']); assert.equal(callbacks.size, 0);
});

test('multiple pause reasons can overlap without consuming time or creating duplicate clocks', () => {
  const {timers, advance, expired, callbacks} = setup();
  timers.sync([notice('read')]); advance(1000);
  timers.setPaused(true); advance(5000); timers.setPaused(true); advance(5000);
  assert.equal(callbacks.size, 0); timers.setPaused(false); timers.setPaused(false);
  assert.equal(callbacks.size, 1); advance(FEEDBACK_DURATION - 1000);
  assert.deepEqual(expired, ['read']);
});

test('notification options retain actions and separate auto-close from manual dismiss', () => {
  const calls = [], action = {label: '撤销', onClick: () => calls.push('undo')};
  const source = {id: 'operation', duration: 1250, action,
    onDismiss: () => calls.push('manual'), onAutoClose: () => calls.push('auto')};
  const options = feedbackOptions(source);
  assert.equal(options.duration, Infinity); assert.equal(options[FEEDBACK_LIFETIME].duration, 1250);
  assert.equal(options.action, action); options.action.onClick(); options.onDismiss();
  options.onDismiss(); options.onAutoClose();
  const auto = feedbackOptions(source); auto.onAutoClose(); auto.onDismiss(); auto.onAutoClose();
  assert.deepEqual(calls, ['undo','manual','auto']);
});
