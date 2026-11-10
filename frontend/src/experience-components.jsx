import React, {useEffect, useLayoutEffect, useId, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Link, useLocation, useNavigationType} from 'react-router-dom';

const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export function RouteScroll() {
  const location = useLocation();
  const navigation = useNavigationType();
  const positions = useRef(new Map());
  useLayoutEffect(() => {
    const previous = history.scrollRestoration;
    history.scrollRestoration = 'manual';
    return () => { history.scrollRestoration = previous; };
  }, []);
  useLayoutEffect(() => {
    const key = location.pathname;
    const timer = requestAnimationFrame(() => {
      const anchor = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (anchor) anchor.scrollIntoView({block: 'start', behavior: 'instant'});
      else window.scrollTo({top: navigation === 'POP' ? positions.current.get(key) || 0 : 0, behavior: 'instant'});
    });
    return () => { cancelAnimationFrame(timer); positions.current.set(key, window.scrollY); };
  }, [location.pathname, location.hash]);
  return null;
}

export {Toast} from './ui-feedback.jsx';

export function UserNavigation({name, admin, routes, adminRoutes, onLogout, children}) {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const root = useRef(null);
  const trigger = useRef(null);
  const id = useId();
  const location = useLocation();
  useEffect(() => {
    if (open) { setPresent(true); return; }
    const timer = setTimeout(() => setPresent(false), reduced() ? 0 : 140);
    return () => clearTimeout(timer);
  }, [open]);
  useEffect(() => { setOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);
  const links = admin ? [...routes, ...adminRoutes] : routes;
  return <div className="user-navigation" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button type="button" className="fire-account" ref={trigger} aria-label={`${admin ? '管理员' : name}，打开账户菜单`} aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>{children}</button>
    {present && <nav id={id} className={`user-navigation-menu${open?'':' is-closing'}`} inert={!open} aria-hidden={!open} aria-label="全部页面与账户操作">
      <strong>{admin ? '管理员' : name}</strong>
      {links.map(([href, label, Icon]) => <Link key={href} to={href} aria-current={location.pathname === href ? 'page' : undefined}><Icon/>{admin && label === '执行记录' ? (href.startsWith('/admin') ? '管理执行记录' : '我的执行记录') : label}</Link>)}
      <Link to="/notifications">通知设置</Link><Link to="/help">使用帮助</Link>
      <button type="button" onClick={onLogout}>退出登录</button>
    </nav>}
  </div>;
}

export function WorkspaceSkeleton() {
  return <div className="workspace-skeleton" role="status" aria-label="正在加载工作区" aria-busy="true">
    <div className="skeleton-nav"><i/><i/><i/></div><div className="skeleton-heading"/>
    <div className="skeleton-summary"/><div className="skeleton-cards"><i/><i/><i/></div>
    <span>正在读取账号与任务…</span>
  </div>;
}

export function ActivityHeatmap({activity = {}}) {
  const days = (activity.days || []).filter(Boolean).map(day => ({...day}));
  while (days.length && days.length % 7) days.push({outside: true});
  const columns = Math.max(1, Math.ceil(days.length / 7));
  const maximum = Math.max(1, ...days.map(day => Number(day.count) || 0));
  const [tip, setTip] = useState(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const grid = useRef(null);
  const pointer = useRef(null);
  const dismissedAt = useRef(null);
  const id = useId();
  const dismiss = () => {
    // A reveal/scroll can move another cell underneath a stationary pointer.
    // Escape must stay dismissed until the user actually moves or acts again.
    dismissedAt.current = pointer.current;
    setTip(null);
  };
  useEffect(() => {
    if (!tip) return;
    const close = event => { if (!grid.current?.contains(event.target)) setTip(null); };
    const scroll = () => setTip(null);
    const escape = event => { if (event.key === 'Escape') dismiss(); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    window.addEventListener('scroll', scroll, true);
    window.addEventListener('resize', scroll);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); window.removeEventListener('scroll', scroll, true); window.removeEventListener('resize', scroll); };
  }, [Boolean(tip)]);
  const show = (node, day, index) => {
    const rect = node.getBoundingClientRect();
    setTip({index, text: `${day.date} · ${day.count || 0} 次互动`, x: Math.max(112, Math.min(innerWidth - 112, rect.left + rect.width / 2)), y: rect.top > 56 ? rect.top - 42 : rect.bottom + 8});
  };
  const showFromPointer = (event, day, index) => {
    if (event.pointerType === 'touch') return;
    const current = {x: event.clientX, y: event.clientY};
    pointer.current = current;
    if (dismissedAt.current && Math.hypot(current.x - dismissedAt.current.x, current.y - dismissedAt.current.y) < 2) return;
    dismissedAt.current = null;
    show(event.currentTarget, day, index);
  };
  const key = (event, index) => {
    if (event.key === 'Escape') { dismiss(); return; }
    const steps = {ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7};
    if (!(event.key in steps) && !['Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const last = days.findLastIndex(day => !day.outside);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? last : Math.max(0, Math.min(last, index + steps[event.key]));
    setFocusedIndex(next); grid.current?.querySelector(`[data-day-index="${next}"]`)?.focus();
  };
  return <section data-reveal="section" className="fire-heat-card">
    <div className="fire-section-title">近 365 天互动 · {activity.total || 0} 次</div>
    <p className="fire-heat-hint">每格代表一天，悬停或点按查看当天记录</p>
    <div className="fire-heat-scroll" tabIndex={0} role="region" aria-label="近365天发送热力图，可左右滚动">
      <div className="fire-heat-months" style={{gridTemplateColumns: `repeat(${columns},minmax(0,1fr))`}}>{Array.from({length: columns}, (_, index) => {
        const date = days[index * 7]?.date || '';
        return <span key={index}>{date && Number(date.slice(8, 10)) <= 7 ? `${Number(date.slice(5, 7))}月` : ''}</span>;
      })}</div>
      <div ref={grid} className="fire-heat-grid" style={{gridTemplateColumns: `repeat(${columns},minmax(0,1fr))`}}>
        {days.map((day, index) => day.outside ? <i key={index} className="outside" aria-hidden="true"/> : <button key={day.date} type="button" className="fire-heat-day" data-day-index={index} tabIndex={index === focusedIndex ? 0 : -1}
          style={{'--heat-intensity': Math.min(1, .12 + Number(day.count || 0) / maximum * .88)}} aria-label={`${day.date} · ${day.count || 0} 次互动`} aria-describedby={tip?.index === index ? id : undefined}
          onPointerEnter={event => showFromPointer(event, day, index)} onPointerMove={event => { if (dismissedAt.current) showFromPointer(event, day, index); else pointer.current = {x: event.clientX, y: event.clientY}; }} onPointerLeave={() => setTip(null)}
          onClick={event => { dismissedAt.current = null; show(event.currentTarget, day, index); }} onFocus={event => { dismissedAt.current = null; setFocusedIndex(index); show(event.currentTarget, day, index); }} onBlur={() => setTip(null)} onKeyDown={event => key(event, index)}/>)}
      </div>
    </div>
    <div className="fire-heat-legend"><span>少</span><i/><i/><i/><i/><span>多</span></div>
    {tip && createPortal(<div id={id} role="tooltip" className="heat-tooltip" style={{left: tip.x, top: tip.y}}>{tip.text}</div>, document.body)}
  </section>;
}

export function flashTask(id) {
  requestAnimationFrame(() => {
    const node = document.getElementById(`task-row-${id}`);
    if (!node) return;
    node.classList.remove('is-just-saved');
    void node.offsetWidth;
    node.classList.add('is-just-saved');
    node.addEventListener('animationend', () => node.classList.remove('is-just-saved'), {once: true});
  });
}
