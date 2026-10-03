const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function main(scenario) {
  const modulePath = path.resolve(__dirname, '../../frontend/src/cloud-frame-state.js');
  const source = fs.readFileSync(modulePath, 'utf8');
  assert.match(source, /CLOUD_FRAME_LOAD_TIMEOUT_MS/);
  assert.match(source, /retryCloudFrame/);
  const appSource = fs.readFileSync(path.resolve(__dirname, '../../frontend/src/app.jsx'), 'utf8');
  assert.match(appSource, /frameLoadError\(current/);
  assert.match(appSource, /accounts\/scan\/\$\{scan\.id\}\/qr/);
  assert.doesNotMatch(appSource, /const maxAttempts=5/);

  const {
    CLOUD_FRAME_LOAD_TIMEOUT_MS,
    cloudFrameOverlay,
    createCloudFrameState,
    frameLoadError,
    retryCloudFrame,
  } = await import(`file://${modulePath}`);

  if (scenario === 'transient-errors-stay-loading') {
    let state = createCloudFrameState('scan-transient', 1_000);
    state = frameLoadError(state, {now: 1_001, remainingSeconds: 299});
    state = frameLoadError(state, {now: 8_000, remainingSeconds: 292});
    state = frameLoadError(state, {now: 29_000, remainingSeconds: null});
    assert.equal(state.scanId, 'scan-transient');
    assert.equal(state.status, 'loading');
    assert.equal(state.error, '');
    assert.equal(cloudFrameOverlay(state).kind, 'loading');
    assert.notEqual(cloudFrameOverlay(state).title, '云端画面暂时不可用');
  } else if (scenario === 'timeout-is-explicit') {
    const state = frameLoadError(
      createCloudFrameState('scan-timeout', 1_000),
      {now: 1_000 + CLOUD_FRAME_LOAD_TIMEOUT_MS, remainingSeconds: 30},
    );
    assert.equal(state.status, 'timeout');
    assert.equal(state.error, 'timeout');
    assert.equal(cloudFrameOverlay(state).title, '云端画面加载超时');
    assert.match(cloudFrameOverlay(state).detail, /会话仍在保留/);
  } else if (scenario === 'expiry-is-explicit') {
    const state = frameLoadError(
      createCloudFrameState('scan-expired', 1_000),
      {now: 1_500, remainingSeconds: 0},
    );
    assert.equal(state.status, 'expired');
    assert.equal(state.error, 'expired');
    assert.equal(cloudFrameOverlay(state).title, '二维码已失效');
  } else if (scenario === 'retry-keeps-scan-id') {
    const initial = createCloudFrameState('scan-keep', 1_000);
    const timedOut = frameLoadError(initial, {
      now: 1_000 + CLOUD_FRAME_LOAD_TIMEOUT_MS,
      remainingSeconds: 20,
    });
    const retried = retryCloudFrame(timedOut, 40_000);
    assert.equal(retried.scanId, 'scan-keep');
    assert.equal(retried.startedAt, 40_000);
    assert.equal(retried.status, 'loading');
    assert.equal(retried.attempt, 0);
  } else {
    throw new Error(`unknown scenario: ${scenario}`);
  }
  process.stdout.write(JSON.stringify({scenario}) + '\n');
}

main(process.argv[2]).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
