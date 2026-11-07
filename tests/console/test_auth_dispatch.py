import asyncio
import unittest

from spark_console.auth_dispatch import dispatch


class BlockingLoginWorker:
    def __init__(self):self.started=asyncio.Event()
    async def run_once(self,stopping):
        self.started.set()
        await stopping.wait()
        return True


class AuthDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_account_work_runs_while_qr_login_waits(self):
        stopping=asyncio.Event();worker=BlockingLoginWorker();background_ran=asyncio.Event()
        async def background():
            await worker.started.wait();background_ran.set();stopping.set();return True
        await asyncio.wait_for(dispatch(worker,stopping,background,slots=2,poll_seconds=.01),timeout=1)
        self.assertTrue(worker.started.is_set())
        self.assertTrue(background_ran.is_set())

    async def test_single_browser_slot_stops_cleanly(self):
        stopping=asyncio.Event();calls=[]
        class Worker:
            async def run_once(self,_stopping):
                calls.append('scan');stopping.set();return False
        async def background():calls.append('background');return False
        await asyncio.wait_for(dispatch(Worker(),stopping,background,slots=1,poll_seconds=.01),timeout=1)
        self.assertEqual(['scan'],calls)

    async def test_memory_constrained_single_slot_still_runs_background_jobs(self):
        stopping=asyncio.Event();calls=[]
        class Worker:
            async def run_once(self,_stopping):calls.append('scan');return False
        async def background():calls.append('background');stopping.set();return True
        await asyncio.wait_for(dispatch(Worker(),stopping,background,slots=1,poll_seconds=.005),timeout=1)
        self.assertEqual(['scan','background'],calls)


if __name__=='__main__':unittest.main()
