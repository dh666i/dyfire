"""Record an already healthy manually deployed release, never an intended one."""
import json
from pathlib import Path
import re
import sys
import urllib.request

version,commit=sys.argv[1:]
assert re.fullmatch(r'\d+\.\d+\.\d+',version)
assert re.fullmatch(r'[a-f0-9]{40}',commit)
with urllib.request.urlopen('http://127.0.0.1:8899/api/version') as response:
    assert json.load(response)['version']==version
with urllib.request.urlopen('http://127.0.0.1:8899/health/ready') as response:
    assert response.status==200
root=Path('/opt/dyfire-web-updater')
temp=root/'installed.tmp'
temp.write_text(json.dumps({'version':version,'commit':commit,'image':'dyfire:'+version}))
temp.replace(root/'installed.json')
