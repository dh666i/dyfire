"""Explicit one-time installation of the two Dyfire origin upstream includes.

Never visits the Hong Kong server or edits other virtual hosts. The default
command only checks the planned inputs; --apply performs the migration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

try:
    from deploy.web_switch import _write, upstream, CANONICAL_PORT, CANDIDATE_PORT
except ModuleNotFoundError:
    from web_switch import _write, upstream, CANONICAL_PORT, CANDIDATE_PORT


VHOSTS = ('dyfire.cn.conf', 'dyfire-origin-http.conf')


def migrated_vhost(text):
    names = re.findall(r'\bserver_name\s+([^;]+);', text)
    if len(names) != 1 or not set(names[0].split()).issubset({'dyfire.cn', 'dyfire-origin.internal'}) or 'dyfire.cn' not in names[0].split():
        raise ValueError('Refusing to edit a virtual host not dedicated to Dyfire')
    old = 'proxy_pass http://127.0.0.1:8899;'
    new = 'proxy_pass http://dyfire_web_active;'
    if text.count(new) == 1 and old not in text:
        return text
    if text.count(old) != 1 or new in text:
        raise ValueError('Dyfire origin does not have the expected Web proxy')
    return text.replace(old, new)


def install(root, *, nginx_root=Path('/etc/nginx'), systemd_root=Path('/etc/systemd/system'), apply=False):
    root, nginx_root, systemd_root = Path(root).resolve(), Path(nginx_root).resolve(), Path(systemd_root).resolve()
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', root.as_posix()):
        raise ValueError('Deployment root cannot be embedded safely in nginx configuration')
    planned = {}
    for name in VHOSTS:
        path = (nginx_root / 'sites-enabled' / name).resolve(strict=True)
        if nginx_root not in path.parents:
            raise ValueError('Dyfire virtual host resolves outside the nginx directory')
        planned[path] = migrated_vhost(path.read_text())
    upstream_file = root / 'deploy/web-upstream.conf'
    include = nginx_root / 'conf.d/dyfire-web-upstream.conf'
    include_text = 'include ' + upstream_file.as_posix() + ';\n'
    if include.exists() and include.read_text() != include_text:
        raise ValueError('Existing upstream include is not managed by Dyfire')
    planned[include] = include_text
    if not upstream_file.exists():
        planned[upstream_file] = upstream(CANONICAL_PORT)
    elif upstream_file.read_text() != upstream(CANONICAL_PORT):
        raise ValueError('Complete or recover the current Web handoff before installation')
    unit = systemd_root / 'dyfire-web-route.service'
    unit_text = (root / 'deploy/dyfire-web-route.service').read_text()
    if unit.exists() and unit.read_text() != unit_text:
        raise ValueError('Existing route service differs from the reviewed Dyfire unit')
    planned[unit] = unit_text
    if not apply:
        return {'changed_paths': [str(path) for path in planned], 'applied': False}
    original = {path: path.read_bytes() if path.exists() else None for path in planned}
    # A small configuration recovery record, not an application/database copy.
    receipt = root / 'deploy/web-switch-migration.json'
    if not receipt.exists():
        _write(receipt, json.dumps({str(path): value.decode() if value is not None else None for path, value in original.items()}))
    try:
        for path, text in planned.items():
            _write(path, text)
        subprocess.run(['nginx', '-t', '-q'], check=True, timeout=30)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
        subprocess.run(['systemctl', 'start', 'dyfire-web-route.service'], check=True, timeout=60)
        _write(root / 'deploy/web-switch.json', json.dumps({'version': 1, 'canonical_port': CANONICAL_PORT, 'candidate_port': CANDIDATE_PORT}))
    except Exception:
        for path, value in original.items():
            if value is None:
                path.unlink(missing_ok=True)
            else:
                _write(path, value.decode())
        subprocess.run(['nginx', '-t', '-q'], check=True, timeout=30)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=30)
        raise
    return {'changed_paths': [str(path) for path in planned], 'applied': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/opt/dyfire')
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    print(json.dumps(install(options.root, apply=options.apply)))
