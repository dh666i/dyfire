import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import '@fontsource/inter/latin-400.css';
import '@fontsource/noto-sans-sc/400.css';
import './react.css';
import './react-polish.css';
import './react-fire.css';
import './ui-quality.css';
import './ui-lab.css';
import {Button,LiveNumber,SiteSelect} from './ui-controls.jsx';
import {FeedbackHost,notify} from './ui-feedback.jsx';
import {Drawer} from './ui-dialog.jsx';
import {ContactPanel} from './ui-panels.jsx';

const wait=()=>new Promise(resolve=>setTimeout(resolve,900));
const names=['小琪','一个用于检查换行与布局的很长很长的好友名称','阿杰'];
function Lab(){
  const [theme,setTheme]=useState('dark'),[panel,setPanel]=useState(''),[count,setCount]=useState(18);
  const [draft,setDraft]=useState(''),[selected,setSelected]=useState([]),[account,setAccount]=useState('main');
  useEffect(()=>{document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme},[theme]);
  const operation=async()=>{const id=notify.loading('正在保存');await wait();setCount(v=>v+1);notify.success('任务已保存',{id})};
  return <main className="quality-lab">
    <header className="quality-lab-head"><div><h1>组件状态检查</h1><p>本地交互样例，使用实际共用组件；不会请求后台或发送消息。</p></div><Button variant="quiet" onClick={()=>setTheme(t=>t==='dark'?'light':'dark')}>切换{theme==='dark'?'浅色':'深色'}</Button></header>
    <div className="quality-lab-grid">
      <section className="card"><h2>勾选框与开关</h2><p>好友多选、管理列表和设置项使用同一套控件状态。</p><div className="quality-lab-controls"><label><input type="checkbox"/>未选中</label><label><input type="checkbox" defaultChecked/>已选中</label><label><input type="checkbox" disabled/>不可选择</label><label><input type="checkbox" disabled defaultChecked/>已选但不可修改</label><label><input type="checkbox" role="switch" className="ui-switch" defaultChecked/>开启提醒</label><label><input type="checkbox" role="switch" className="ui-switch"/>关闭提醒</label></div></section>
      <section className="card"><h2>通知与关闭按钮</h2><p>关闭图标固定在通知内部右侧，透明热区方便点击；长消息也不会与按钮重叠。</p><div className="quality-lab-actions"><Button onClick={()=>notify.success('任务已保存',{id:'lab-success',description:'新的发送时间将用于下一次执行。',duration:Infinity})}>显示成功通知</Button><Button variant="quiet" onClick={()=>notify.error('暂时无法保存，请稍后重试',{id:'lab-error',description:'输入内容已保留，你可以关闭提示后继续编辑。',duration:Infinity})}>显示失败通知</Button></div></section>
      <section className="card"><h2>按钮与操作反馈</h2><p>连续点击只提交一次，处理中保持原始宽度。</p><div className="quality-lab-actions"><Button onClick={operation}>保存任务</Button><Button busy>保存任务</Button><Button disabled>暂不可用</Button><Button variant="quiet" onClick={async()=>{await wait();throw new Error('暂时无法保存，输入已保留，请重试。')}}>测试失败反馈</Button></div></section>
      <section className="card"><h2>真实数值变化</h2><strong className="quality-lab-number"><LiveNumber value={count}/></strong><p>首次显示实际值；操作完成后才变化。</p><Button variant="quiet" onClick={()=>setCount(v=>v+1)}>增加 1</Button></section>
      <section className="card"><h2>选择器与键盘</h2><label className="form-stack">发送账号<SiteSelect label="发送账号" value={account} onValueChange={setAccount} items={[{value:'main',label:'主账号'},{value:'long',label:'一个名称很长的抖音账号，用来检查菜单换行'},{value:'offline',label:'需要验证的账号（不可选示例）',disabled:true}]}/></label><p>支持方向键、输入定位、Esc 与焦点返回。</p><Button variant="quiet" onClick={()=>setPanel('friends')}>选择好友</Button></section>
      <section className="card"><h2>编辑与退出</h2><p>打开编辑，检查输入、窗口关闭、焦点返回和手机可视高度。</p><Button onClick={()=>setPanel('edit')}>编辑任务</Button></section>
      <section className="card"><h2>长内容与不同状态</h2><div className="quality-lab-states">{['已完成','待发送','需要验证','失败'].map((label,index)=><div key={label}><span className={`badge ${['good','neutral','warn','danger'][index]}`}><i/>{label}</span><b>{names[index%3]}</b><small>今天 09:30 · 主账号</small></div>)}</div></section>
      <section className="card"><h2>空态与错误恢复</h2><div className="quality-lab-empty"><b>没有匹配的会话</b><p>换一个关键词，或清除筛选查看已同步的好友。</p><Button variant="quiet" onClick={()=>notify('筛选已清除')}>清除筛选</Button></div><div className="inline-feedback danger-text" role="alert">暂时无法读取，当前内容已保留。<Button variant="quiet" onClick={async()=>{await wait();notify.success('内容已更新')}}>重试读取</Button></div></section>
    </div>
    <Drawer open={panel==='edit'} title="编辑续火任务" centered size="compact" onClose={()=>setPanel('')}>
      <div className="drawer-content"><form className="form-stack" onSubmit={e=>e.preventDefault()}><label>发送内容<textarea className="input textarea" placeholder="写一句日常问候" value={draft} onChange={e=>setDraft(e.target.value)}/></label><Button onClick={async()=>{await operation();setPanel('')}}>保存修改</Button><Button variant="quiet" onClick={()=>setPanel('')}>取消</Button></form></div>
    </Drawer>
    <ContactPanel DesktopDrawer={Drawer} open={panel==='friends'} title="选择目标好友" onClose={()=>setPanel('')} wide>
      <div className="drawer-content contact-picker-content"><label className="form-stack">搜索已同步会话<input className="input" placeholder="输入好友名称"/></label><div className="quality-lab-friends">{names.map(name=><label key={name}><input type="checkbox" checked={selected.includes(name)} onChange={e=>setSelected(list=>e.target.checked?[...list,name]:list.filter(n=>n!==name))}/><span>{name}</span></label>)}</div></div><footer className="drawer-foot contact-picker-footer"><div className="contact-picker-actions"><span>{selected.length} 位好友</span><Button onClick={()=>{notify.success(`已选择 ${selected.length} 位好友`);setPanel('')}}>完成选择</Button></div></footer>
    </ContactPanel>
    <FeedbackHost/>
  </main>;
}
createRoot(document.getElementById('app')).render(<Lab/>);
