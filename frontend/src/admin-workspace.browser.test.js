import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const enabled = process.env.DYFIRE_BROWSER_SMOKE === '1';

test('admin user editor renders, switches tabs, and resets for another user', {skip: !enabled}, async () => {
  const require = createRequire(import.meta.url);
  let playwright;
  try {
    playwright = require('playwright');
  } catch (error) {
    assert.fail(`browser smoke requires Playwright: ${error.message}`);
  }
  const executablePath = process.env.DYFIRE_BROWSER_PATH || '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
  const browser = await playwright.chromium.launch({headless: true, executablePath});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(process.env.DYFIRE_BROWSER_URL || 'http://127.0.0.1:5195/admin/users?preview=1&admin=1', {waitUntil: 'networkidle'});
    const editButtons = page.getByRole('button', {name: '编辑'});
    assert.equal(await editButtons.count(), 2);
    await editButtons.nth(0).click();
    assert.equal(await page.locator('.workspace-tabs').innerText(), '概览\n管理');
    assert.equal(await page.locator('.workspace-tabs button.active').innerText(), '概览');
    await page.getByRole('button', {name: '管理', exact: true}).click();
    assert.equal(await page.locator('.workspace-tabs button.active').innerText(), '管理');
    assert.match(await page.locator('.drawer-content').innerText(), /订阅与额度/);
    await page.getByLabel('关闭').click();
    await editButtons.nth(1).click();
    assert.equal(await page.locator('.workspace-tabs').innerText(), '概览\n管理');
    assert.equal(await page.locator('.workspace-tabs button.active').innerText(), '概览');
    await page.goto('http://127.0.0.1:5195/admin/codes?preview=1&admin=1', {waitUntil: 'networkidle'});
    const plan = page.locator('#code-plan');
    assert.equal(await plan.inputValue(), 'monthly');
    assert.match(await page.locator('.admin-code-selection').innerText(), /1 个月.*不限好友任务/);
    await plan.selectOption('half_year');
    assert.match(await page.locator('.admin-code-selection').innerText(), /6 个月.*不限好友任务/);
    assert.equal(await page.locator('#code-days').count(), 0);
    await plan.selectOption('');
    assert.equal(await page.locator('#code-days').inputValue(), '30');
    assert.equal(await page.locator('#code-tasks').inputValue(), '2147483647');
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
  }
});
