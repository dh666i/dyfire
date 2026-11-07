"""Isolated browser lifecycle measurement. All network requests are blocked."""
import argparse
import asyncio
import json
from pathlib import Path
import time
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spark_console.credentials import CredentialPayload
from spark_console.executor import DouyinExecutor


def browser_rss_kib():
    """Linux process RSS sum; shared pages are counted more than once."""
    total=0;count=0
    for entry in Path('/proc').glob('[0-9]*/status'):
        try:
            name=(entry.parent/'cmdline').read_bytes().split(b'\0')[0].lower()
            if b'chrome' not in name and b'headless_shell' not in name:continue
            fields=dict(line.split(':',1) for line in entry.read_text().splitlines() if ':' in line)
            total+=int(fields.get('VmRSS','0 kB').split()[0]);count+=1
        except (OSError,ValueError):pass
    return {'processes':count,'rss_sum_kib':total if count else 0,'shared_memory_double_counted':True}


async def measure(slots=1,rounds=3):
    raw=b'[{"name":"sid","value":"isolated-fixture","domain":".example.invalid","path":"/"}]'
    payload=CredentialPayload.parse(raw,1)
    executors=[DouyinExecutor() for _ in range(slots)];durations=[];snapshots=[]
    before=browser_rss_kib()
    try:
        for executor in executors:
            executor._live=await executor._open_browser(payload)
            await executor._live['context'].route('**/*',lambda route:route.abort())
            await executor._live['page'].set_content('<!doctype html><main>'+''.join(f'<button data-row="{i}">测试 {i}</button>' for i in range(100))+'</main>')
        for _ in range(rounds):
            async def step(executor):
                started=time.monotonic()
                for index in (0,40,99):
                    await executor._live['page'].locator(f'[data-row="{index}"]').click()
                await executor._live['page'].screenshot(type='png')
                return time.monotonic()-started
            durations.extend(await asyncio.gather(*(step(executor) for executor in executors)))
        snapshots=browser_rss_kib()
        browsers=[executor._live['browser'] for executor in executors]
        recycled=[await executor.close_idle_session(now=time.monotonic()+executor.SESSION_IDLE_SECONDS+1) for executor in executors]
        disconnected=all(not browser.is_connected() for browser in browsers)
        recovered=await executors[0]._open_browser(payload)
        executors[0]._live=recovered
        cookies=await recovered['context'].cookies()
        preserved=any(cookie['name']=='sid' and cookie['value']=='isolated-fixture' for cookie in cookies)
    finally:
        for executor in executors:await executor.close_session()
    after=browser_rss_kib()
    return {'schema':1,'network':'blocked','isolated_browser_slots':slots,'rounds':rounds,
        'operation_seconds':[round(value,3) for value in durations],'before':before,'while_open':snapshots,'after_close':after,
        'idle_sessions_recycled':all(recycled),'chromium_disconnected':disconnected,'saved_cookie_restored':preserved,
        'production_account_capacity':None,'limitation':'Local DOM and screenshot workload; does not measure Douyin latency, challenges, or send throughput'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--slots',type=int,choices=range(1,5),default=1);parser.add_argument('--rounds',type=int,choices=range(1,21),default=3);args=parser.parse_args()
    report=asyncio.run(measure(args.slots,args.rounds));print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['idle_sessions_recycled'] and report['chromium_disconnected'] and report['saved_cookie_restored'] else 1


if __name__=='__main__':raise SystemExit(main())
