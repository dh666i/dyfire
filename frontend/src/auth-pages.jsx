import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {Link} from 'react-router-dom';
import {Button, StatusIcon} from './ui-controls.jsx';
import './auth-pages.css';

function AuthPassword({name, id, label, autoComplete, required = true, minLength, hint = true}) {
  const [shown, setShown] = useState(false);
  const description = minLength && hint ? `${id}-hint` : undefined;
  return <div className="auth-field">
    <label htmlFor={id}>{label}</label>
    <div className="auth-password">
      <input id={id} name={name} type={shown ? 'text' : 'password'} autoComplete={autoComplete}
        minLength={minLength} maxLength={minLength ? 256 : undefined} required={required}
        aria-describedby={description}/>
      <button type="button" className="auth-password-toggle" onClick={() => setShown(value => !value)}
        aria-label={shown ? '隐藏密码' : '显示密码'} aria-pressed={shown} aria-controls={id}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {shown ? <path d="m3 3 18 18M9.8 9.8a3 3 0 0 0 4.4 4.4M6.6 6.6C4.5 8.1 3 10.3 2 12c2 3.5 5.5 7 10 7a10.6 10.6 0 0 0 5.4-1.6M10 5.2A10.7 10.7 0 0 1 12 5c4.5 0 8 3.5 10 7a22.1 22.1 0 0 1-3 4"/> : <><path d="M2 12c2-3.5 5.5-7 10-7s8 3.5 10 7c-2 3.5-5.5 7-10 7S4 15.5 2 12Z"/><circle cx="12" cy="12" r="3"/></>}
        </svg>
      </button>
    </div>
    {description && <small id={description}>至少 12 位，包含字母和数字，避免常见、连续或重复组合。</small>}
  </div>;
}

function AuthNotice({children, tone = ''}) {
  return <div className={`auth-notice ${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
    <StatusIcon kind={tone === 'danger' ? 'error' : tone === 'good' ? 'success' : 'info'}/><span>{children}</span>
  </div>;
}

export default function AuthPage({kind, api, Logo, ServiceIndicator}) {
  const params = new URLSearchParams(window.location.search);
  const error = params.get('error');
  const username = params.get('username') || '';
  const registered = params.get('registered') === '1';
  const sent = params.get('sent') === '1';
  const [csrf, setCsrf] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const formRef = useRef(null), heightRef = useRef(0), animationRef = useRef(null);
  const [recovery, setRecovery] = useState(null);

  useEffect(() => { submittingRef.current = false; setSubmitting(false); }, [kind, error]);
  useEffect(() => {
    const restore = () => { submittingRef.current = false; setSubmitting(false); };
    window.addEventListener('pageshow', restore);
    return () => window.removeEventListener('pageshow', restore);
  }, []);
  useLayoutEffect(() => {
    const node = formRef.current;
    if (!node) return;
    const next = node.scrollHeight, previous = heightRef.current;
    heightRef.current = next;
    animationRef.current?.cancel();
    if (previous && previous !== next && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      animationRef.current = node.animate([{height: `${previous}px`}, {height: `${next}px`}], {duration: 240, easing: 'cubic-bezier(.22,1,.36,1)'});
    }
    return () => animationRef.current?.cancel();
  }, [kind, sent]);
  useEffect(() => {
    if (kind !== 'forgot' || sent) return;
    const controller = new AbortController();
    setRecovery(null);
    api('/api/auth/recovery-status', {signal: controller.signal}).then(setRecovery)
      .catch(value => { if (value.name !== 'AbortError') setRecovery({available: false, message: value.message}); });
    return () => controller.abort();
  }, [kind, sent, api]);
  useEffect(() => {
    if (kind !== 'change') return;
    const controller = new AbortController();
    api('/api/auth/bootstrap', {signal: controller.signal}).then(value => setCsrf(value?.csrf || '')).catch(() => {});
    return () => controller.abort();
  }, [kind, api]);

  const title = kind === 'login' ? '欢迎回来' : kind === 'register' ? '创建账号' : kind === 'forgot' ? (sent ? '设置新密码' : '找回网站密码') : '设置登录密码';
  const description = kind === 'login' ? '登录后，继续管理你的账号与续火任务。'
    : kind === 'register' ? '免费绑定 1 个抖音账号，创建 1 个好友任务。'
    : kind === 'forgot' ? (sent ? '填写邮件中的重置代码，设置新的登录密码。' : '通过已验证的邮箱，重设网站登录密码。')
    : '更新你的平台密码，下次登录使用新密码。';
  const action = kind === 'login' ? '/login' : kind === 'register' ? '/register' : kind === 'forgot' ? (sent ? '/forgot-password/reset' : '/forgot-password') : '/change-password';
  const hasTabs = kind === 'login' || kind === 'register';
  return <main className="fire-auth-shell auth-refined" data-auth-kind={kind}>
    <header className="fire-auth-top"><Logo/><ServiceIndicator/></header>
    <div className="auth-main-content">
      <section className="fire-auth-card" aria-labelledby="auth-title">
        <div className="fire-auth-intro" key={`intro-${kind}-${sent}`}>
          <h1 id="auth-title">{title}</h1><p>{description}</p>
        </div>
        {hasTabs && <nav className="fire-auth-tabs" aria-label="账户入口" data-active={kind}>
          <Link className={kind === 'login' ? 'active' : ''} aria-current={kind === 'login' ? 'page' : undefined} to="/login">登录</Link>
          <Link className={kind === 'register' ? 'active' : ''} aria-current={kind === 'register' ? 'page' : undefined} to="/register">注册</Link>
        </nav>}
        {registered && <AuthNotice tone="good">注册成功，请登录后开始绑定抖音账号。</AuthNotice>}
        {sent && <AuthNotice>如果账号和已验证邮箱匹配，重置邮件会进入发送队列。</AuthNotice>}
        {error && <AuthNotice tone="danger">{error}</AuthNotice>}
        {kind === 'forgot' && !sent && recovery?.available === false && <AuthNotice>{recovery.message}</AuthNotice>}
        <div className="auth-form-motion" ref={formRef}>
          <form key={`${kind}-${sent}`} className="auth-form fire-auth-form" method="post" action={action}
            onSubmit={event => {
              if (submittingRef.current) { event.preventDefault(); return; }
              submittingRef.current = true; setSubmitting(true);
            }}>
            {kind === 'change' && <input type="hidden" name="csrf_token" value={csrf}/>}
            {kind === 'login' && <>
              <label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="用户名或邮箱" required/></label>
              <AuthPassword name="password" id="auth-login-password" label="密码" autoComplete="current-password"/>
            </>}
            {kind === 'register' && <>
              <label className="auth-field"><span>登录账号</span><input name="username" defaultValue={username} autoComplete="username" autoCapitalize="none" spellCheck={false} minLength="3" maxLength="32" placeholder="设置用户名或邮箱" required aria-describedby="auth-username-hint"/><small id="auth-username-hint">3–32 位字母、数字、@、点、下划线或短横线。</small></label>
              <AuthPassword name="password" id="auth-register-password" label="密码" autoComplete="new-password" minLength={12}/>
              <AuthPassword name="password_confirmation" id="auth-register-password-confirmation" label="确认密码" autoComplete="new-password" minLength={12} hint={false}/>
            </>}
            {kind === 'forgot' && !sent && <>
              <label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="输入网站用户名" required/></label>
              <label className="auth-field"><span>已验证的接收邮箱</span><input name="email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false} placeholder="输入绑定并验证的邮箱" required/></label>
            </>}
            {kind === 'forgot' && sent && <>
              <label className="auth-field"><span>网站用户名</span><input name="username" defaultValue={username} autoComplete="username" autoCapitalize="none" spellCheck={false} required/></label>
              <label className="auth-field"><span>重置代码</span><input name="code" inputMode="text" autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} minLength="8" maxLength="12" placeholder="邮件中的重置代码" required/></label>
              <AuthPassword name="password" id="auth-reset-password" label="新密码" autoComplete="new-password" minLength={12}/>
              <AuthPassword name="confirmation" id="auth-reset-confirmation" label="再次输入新密码" autoComplete="new-password" minLength={12} hint={false}/>
            </>}
            {kind === 'change' && <>
              <AuthPassword name="current_password" id="auth-current-password" label="当前密码" autoComplete="current-password"/>
              <AuthPassword name="new_password" id="auth-new-password" label="新密码" autoComplete="new-password" minLength={12}/>
              <AuthPassword name="new_password_confirmation" id="auth-new-password-confirmation" label="确认新密码" autoComplete="new-password" minLength={12} hint={false}/>
            </>}
            <Button busy={submitting} busyLabel="正在提交" className="auth-submit" type="submit" disabled={kind === 'forgot' && !sent && !recovery?.available}>
              {kind === 'login' ? '登录' : kind === 'register' ? '创建账号' : kind === 'forgot' ? (sent ? '重置密码' : '发送重置邮件') : '保存新密码'}
            </Button>
          </form>
        </div>
        <footer className="auth-footer fire-auth-footer">
          {kind === 'login' ? <Link to="/forgot-password">忘记网站密码？</Link> : <Link to="/login">返回登录</Link>}
        </footer>
      </section>
    </div>
  </main>;
}
