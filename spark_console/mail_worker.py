"""Bounded mail tick, invoked by the host's one-minute systemd timer."""
import os
from spark_console.config import Settings
from spark_console.crypto import CookieCipher
from spark_console.db import create_engine_for
from spark_console.services.mail import run_once

if __name__=='__main__':
    settings=Settings.from_env(os.environ)
    run_once(create_engine_for(settings),CookieCipher(settings.cookie_key_file.read_bytes()))
