"""Container smoke test. Does not start auth/worker or send any messages."""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from spark_console.config import Settings
from spark_console.db import create_engine_for, create_schema
from spark_console.version import VERSION
from spark_console.web.app import create_app

with TemporaryDirectory() as folder:
    root=Path(folder)
    cookie=root/'cookie.key';cookie.write_bytes(b'c'*32)
    session=root/'session.key';session.write_bytes(b's'*32)
    settings=Settings(root,f'sqlite:///{root}/spark.db',cookie,session)
    engine=create_engine_for(settings);create_schema(engine)
    with TestClient(create_app(settings,engine)) as client:
        assert client.get('/health/ready').status_code==200
        assert client.get('/api/version').json()['version']==VERSION
        assert client.get('/studio').status_code==200
        assert client.get('/api/studio').status_code==401
    engine.dispose()
print('Container web smoke test passed')
