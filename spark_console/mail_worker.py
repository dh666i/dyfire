"""One bounded outbox tick, invoked by the host mail timer."""
import json
import logging
import os
from pathlib import Path
import time
from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for
from spark_console.services.mail import run_once

HEALTH_FILE='mail-worker-health.json'


def read_health(data_dir, *, now=None):
    now=time.time() if now is None else now
    try:
        path=Path(data_dir)/HEALTH_FILE
        if path.stat().st_size>4096:raise ValueError('invalid heartbeat')
        value=json.loads(path.read_text())
        age=now-float(value['at'])
        online=-30<=age<=180 and value.get('state') in ('ready','disabled')
        return {'online':online,'state':value.get('state') if online else 'stale',
                'last_run_at':value.get('at'),'enabled':value.get('enabled') is True}
    except (OSError,ValueError,TypeError,KeyError):
        return {'online':False,'state':'not_running','last_run_at':None,'enabled':False}


def main():
    settings=Settings.from_env(os.environ)
    engine=create_engine_for(settings)
    status={'at':time.time(),'state':'error','enabled':False}
    result=1
    try:
        tick=run_once(engine,CookieCipher(settings.cookie_key_file.read_bytes()))
        status.update(tick);status['state']='ready' if tick['enabled'] else 'disabled';result=0
    except Exception as error:
        logging.getLogger(__name__).error('mail tick failed type=%s',type(error).__name__)
        status['error_type']=type(error).__name__
    finally:
        engine.dispose();status['at']=time.time()
        destination=settings.data_dir/HEALTH_FILE
        temporary=destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(status),encoding='utf-8');temporary.replace(destination)
    return result


if __name__=='__main__':raise SystemExit(main())
