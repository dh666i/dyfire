/*
 * Structural regression checks for the React admin customer workbench.
 *
 * jsdom cannot calculate CSS layout, so this harness deliberately checks the
 * DOM contract and the mobile rules that make the contract render safely. It
 * catches accidental child reordering (which changes grid-area assignment),
 * missing narrow-screen wrapping, and removal of the bottom clearance needed
 * by the fixed mobile dock.
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '../..');
const app = fs.readFileSync(path.join(root, 'frontend/src/app.jsx'), 'utf8');
const css = fs.readFileSync(path.join(root, 'frontend/src/react-polish.css'), 'utf8');

const userTable = app.match(/<div className="admin-table">\s*<div className="table-head user-workbench-head"[\s\S]*?<\/div>\{users\.map\(u=>\{[\s\S]*?<\/div>\}\)\}<\/div>/);
assert.ok(userTable, 'customer workbench must keep a user table and row map');
const row = userTable[0].match(/<div className="admin-row admin-user-row user-workbench-row"[\s\S]*?<\/div>\}\)\}/);
assert.ok(row, 'customer workbench must render user-workbench-row records');
const rowMarkup = row[0];

// The mobile grid assigns areas by child order. Keep this order explicit:
// checkbox, identity, usage, health, actions.
const checkbox = rowMarkup.indexOf('<input type="checkbox"');
const identity = rowMarkup.indexOf('className="admin-cell-button"');
const usage = rowMarkup.indexOf('??sub.account_count');
const health = rowMarkup.indexOf('className="admin-user-health"');
const actions = rowMarkup.indexOf('className="row-actions"');
assert.ok(checkbox >= 0 && identity >= 0 && usage >= 0 && health >= 0 && actions >= 0, 'customer row is missing a required field');
assert.deepEqual([checkbox, identity, usage, health, actions], [...[checkbox, identity, usage, health, actions].sort((a, b) => a - b)], 'customer row child order must match mobile grid areas');

const mobileStart = css.indexOf('@media (max-width: 700px)');
assert.ok(mobileStart >= 0, 'mobile breakpoint rules must exist');
const mobileCss = css.slice(mobileStart);
assert.match(mobileCss, /\.user-workbench-row\s*\{[\s\S]*?grid-template-columns:\s*28px\s+minmax\(0,\s*1fr\)\s+auto;/, 'user rows need an isolated checkbox column on mobile');
assert.match(mobileCss, /\.user-workbench-row\s*\{[\s\S]*?grid-template-areas:\s*"check primary status"\s*"\. secondary actions";/, 'user rows need non-overlapping mobile grid areas');
assert.match(mobileCss, /\.user-workbench-row\s*>\s*:nth-child\(3\)[^{]*\{[^}]*overflow-wrap:\s*anywhere;/, 'resource text must wrap inside the row');
assert.match(mobileCss, /\.user-workbench-row\s*>\s*:nth-child\(4\)[^{]*\{[^}]*text-align:\s*right;/, 'health state must stay aligned to the row edge');
assert.match(mobileCss, /\.user-workbench-row \.row-actions[^{]*\{[^}]*flex-wrap:\s*wrap;/, 'row actions must wrap instead of overflowing');
assert.match(mobileCss, /\.admin-table, \.admin-pagination\s*\{[^}]*padding-bottom:\s*(?:8|16)px;/, 'last row and pagination need mobile dock clearance');

console.log('Admin mobile customer workbench structure and narrow-screen rules passed');
