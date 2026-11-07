"""Bounded fair dispatch; a waiting QR no longer blocks every other account."""
import asyncio
import logging
from pathlib import Path

logger=logging.getLogger(__name__)


def admission_limit(configured):
    try:
        maximum=Path('/sys/fs/cgroup/memory.max').read_text().strip()
        current=int(Path('/sys/fs/cgroup/memory.current').read_text())
        if maximum!='max' and int(maximum)-current<192*1024**2:return 1
    except (OSError,ValueError):pass
    return configured


async def dispatch(worker,stopping,background,*,slots=2,poll_seconds=1):
    active={};turn=0
    async def run(which):
        try:return await (worker.run_once(stopping) if which=='scan' else background())
        except asyncio.CancelledError:raise
        except Exception as error:logger.error('auth operation failed type=%s',type(error).__name__);return False
    try:
        while not stopping.is_set():
            finished={job for job in active if job.done()}
            for job in finished:
                try:job.result()
                except asyncio.CancelledError:pass
                active.pop(job,None)
            while len(active)<admission_limit(slots) and not stopping.is_set():
                order=('scan','background') if turn%2==0 else ('background','scan')
                which=next((kind for kind in order if kind not in active.values()),None)
                if which is None:break
                turn+=1
                job=asyncio.create_task(run(which));active[job]=which
                # Let empty queue claims finish before admitting the next
                # operation, avoiding a hot polling loop on an idle site.
                await asyncio.sleep(0)
            try:await asyncio.wait_for(stopping.wait(),timeout=poll_seconds)
            except TimeoutError:pass
    finally:
        for job in active:job.cancel()
        await asyncio.gather(*active,return_exceptions=True)
