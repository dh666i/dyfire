import React, {useEffect, useRef, useState} from 'react';
import {Button, SiteSelect} from './ui-controls.jsx';
import {AnimatedDetails} from './ui-motion.jsx';
import {formatUpdateBytes, isUpdateBusy, progressValue, updateAvailability, updatePhaseLabel} from './update-state.js';
import './update-panel.css';

function DownloadEstimate({label, plan}) {
  if (!plan?.known) return null;
  const reusedBytes = Math.max(0, plan.compressed_image_bytes - plan.download_upper_bound_bytes);
  return <div className="update-download-estimate">
    <span>{label}</span>
    <b>最多下载 {formatUpdateBytes(plan.download_upper_bound_bytes)}</b>
    <small>可复用 {formatUpdateBytes(reusedBytes)} · {plan.reused_layers || 0} 个镜像层</small>
  </div>;
}

export default function UpdateReleasePanel({update, data, onFeedback, api, Card, Badge, confirmModal}) {
  const [busy, setBusy] = useState(false);
  const [pendingOperation, setPendingOperation] = useState('');
  const [rollbackVersion, setRollbackVersion] = useState('');
  const requestLock = useRef(false);
  const availability = updateAvailability(update || {});
  const operationBusy = busy || Boolean(pendingOperation) || isUpdateBusy(update?.phase);
  const rollbackOptions = update?.rollback_versions || [];
  const selectedRollback = rollbackOptions.some(item => item.version === rollbackVersion) ? rollbackVersion : rollbackOptions[0]?.version || '';
  const progress = update?.pull_progress;

  // Keep actions locked across the gap between the POST response and the next
  // status poll. A stale terminal response must not unlock an accepted update.
  useEffect(() => {
    if (pendingOperation && update?.operation_id === pendingOperation) setPendingOperation('');
  }, [pendingOperation, update?.operation_id]);

  const submit = async action => {
    if (requestLock.current || operationBusy || !update?.online) return;
    requestLock.current = true;
    setBusy(true);
    try {
      const version = action === 'rollback' ? selectedRollback : action === 'check' ? '' : update.available || availability.latest;
      if (action === 'install' || action === 'rollback') {
        const approved = await confirmModal({
          title: action === 'rollback' ? '确认回滚版本' : '确认完整更新',
          message: action === 'rollback'
            ? `将恢复到 ${version}，登录和发送服务也会重启。`
            : `将所有服务更新到 ${version}。系统会先等待当前操作结束，再重启登录和发送服务。`,
          confirmLabel: action === 'rollback' ? '确认回滚' : '开始更新',
        });
        if (!approved) return;
      }
      const result = await api(`/api/admin/updates/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': data.csrf},
        body: JSON.stringify({version}),
      });
      if (result?.operation_id) setPendingOperation(result.operation_id);
      onFeedback(action === 'check' ? '正在检查最新版本，结果会自动刷新。' : '操作已接受，进度会自动刷新。');
    } catch (error) {
      onFeedback(error.message);
    } finally {
      requestLock.current = false;
      setBusy(false);
    }
  };

  const phase = pendingOperation ? 'accepted' : update?.phase;
  const message = pendingOperation ? '请求已提交，等待更新服务接收。' : update?.message;
  return <Card className="update-panel">
    <div className="section-head"><div><h2>版本更新</h2></div><Badge tone={update?.online ? 'good' : 'neutral'}>{update?.online ? '更新服务在线' : '更新服务未连接'}</Badge></div>
    <div className="update-version-grid">
      <div><small>网页服务</small><strong>{availability.web}</strong></div>
      <div><small>登录与发送</small><strong>{availability.browser}</strong></div>
      <div><small>最新版本</small><strong className={availability.fullAvailable ? 'update-new' : ''}>{availability.latest}</strong></div>
    </div>
    {update?.release_body && <AnimatedDetails className="update-release-notes"><summary>{update.release_name || '查看版本说明'}</summary><pre>{update.release_body}</pre></AnimatedDetails>}
    <p className="muted-copy">仅更新网页不重启登录和发送服务。</p>
    {!availability.webAllowed && <p className="update-compatibility-note" id="web-update-compatibility">{update?.web_update_reason || '此版本需要同步升级登录和发送服务，请选择完整更新。'}</p>}
    {availability.fullAvailable && !availability.webAvailable && <p className="muted-copy">网页已是最新版本，登录与发送服务可通过完整更新升级。</p>}
    <div className="update-download-estimates">
      <DownloadEstimate label="仅更新网页" plan={update?.web_download_plan}/>
      <DownloadEstimate label="完整更新" plan={update?.download_plan}/>
    </div>
    {progress && operationBusy && !pendingOperation && <div className="image-pull-progress" aria-label="镜像下载状态">
      <div className="update-transfer-heading"><span>{progress.label || '正在准备镜像'}</span>{progressValue(progress.percent) !== undefined && <b>{progressValue(progress.percent)}%</b>}</div>
      <progress value={progressValue(progress.percent)} max="100" aria-label="镜像层下载进度"/>
      <dl className="update-transfer-metrics">
        <div><dt>已下载</dt><dd>{formatUpdateBytes(progress.downloaded_bytes)}</dd></div>
        <div><dt>已复用</dt><dd>{formatUpdateBytes(progress.reused_bytes)}</dd></div>
        <div><dt>平均下载速度</dt><dd>{formatUpdateBytes(progress.bytes_per_second)}{typeof progress.bytes_per_second === 'number' ? '/s' : ''}</dd></div>
      </dl>
      <small className="update-transfer-detail">{progress.layers_total > 0 ? `已就绪 ${progress.layers_ready || 0} / ${progress.layers_total} 个镜像层` : '正在读取镜像层信息'}{progress.attempt > 1 ? ` · 第 ${progress.attempt} 次连接` : ''}</small>
      {progress.idle_seconds >= 30 && <p className="update-transfer-wait" role="status">已有 {Math.floor(progress.idle_seconds)} 秒没有新进展，正在等待镜像仓库响应。</p>}
    </div>}
    <div className="setting-actions">
      <Button variant="quiet" disabled={operationBusy || !update?.online} onClick={() => submit('check')}>检查更新</Button>
      <Button variant="quiet" disabled={operationBusy || !update?.online || !availability.webAvailable || !availability.webAllowed} aria-describedby={!availability.webAllowed ? 'web-update-compatibility' : undefined} onClick={() => submit('install-web')}>仅更新网页</Button>
      <Button disabled={operationBusy || !update?.online || !availability.fullAvailable} onClick={() => submit('install')}>完整更新</Button>
    </div>
    {rollbackOptions.length > 0 && <div className="update-rollback">
      <div className="update-rollback-choice"><span>安全回滚版本</span><SiteSelect label="安全回滚版本" value={selectedRollback} disabled={operationBusy || !update?.online} onValueChange={setRollbackVersion} items={rollbackOptions.map(item => ({value: item.version, label: item.version + (item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString('zh-CN')}` : '')}))}/></div>
      <Button variant="danger" disabled={operationBusy || !update?.online} onClick={() => submit('rollback')}>回滚</Button>
    </div>}
    {phase && <div className="update-progress" role="status"><span>{message || updatePhaseLabel(phase)}</span><b>{updatePhaseLabel(phase)}</b></div>}
  </Card>;
}
