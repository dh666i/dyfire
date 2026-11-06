"""Shared source/package/lock/tag contract for validation and publication."""
import json
import os
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version = (root/'VERSION').read_text().strip()
assert re.fullmatch(r'\d+\.\d+\.\d+', version)
package = json.loads((root/'frontend/package.json').read_text())
lock = json.loads((root/'frontend/package-lock.json').read_text())
assert package['version'] == lock['version'] == lock['packages']['']['version'] == version
tag = os.environ.get('GITHUB_REF_NAME', '')
if tag.startswith('v') and os.environ.get('GITHUB_REF_TYPE') == 'tag':
    assert tag == 'v'+version, 'tag and application version must agree'
print(version)
