"""Validate immutable runtime locks and produce reviewed maintenance artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'deploy/runtime-images.json'
INPUTS = ('Dockerfile.runtime', 'requirements.txt', 'requirements-console.txt',
          'requirements-web.txt', 'requirements-browser-runtime.txt')
IMAGE = re.compile(r'ghcr\.io/dh666i/dyfire@sha256:[a-f0-9]{64}')


def fingerprint(root=ROOT):
    digest = hashlib.sha256()
    for name in INPUTS:
        # A checkout's line endings must not invalidate an identical lock.
        value = (root / name).read_bytes().replace(b'\r\n', b'\n')
        digest.update(name.encode() + b'\0' + value + b'\0')
    return digest.hexdigest()


def validate(data, root=ROOT):
    if data.get('schema') != 1 or data.get('inputs_sha256') != fingerprint(root):
        raise ValueError('Runtime inputs changed: build and review a new runtime-images.json before publishing')
    for component in ('web', 'browser'):
        value = data.get(component, {})
        if not IMAGE.fullmatch(str(value.get('image', ''))) or not IMAGE.fullmatch(str(value.get('index_image', ''))):
            raise ValueError(f'{component} runtime must use this repository and an immutable sha256 digest')
    if data['web']['image'] == data['browser']['image']:
        raise ValueError('Web and browser runtimes must be distinct')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['fingerprint', 'validate', 'github-output', 'write', 'build'])
    parser.add_argument('--lock', type=Path, default=LOCK)
    parser.add_argument('--web')
    parser.add_argument('--browser')
    parser.add_argument('--web-index')
    parser.add_argument('--browser-index')
    parser.add_argument('--source-sha')
    args = parser.parse_args()
    if args.action == 'fingerprint':
        print(fingerprint())
        return
    if args.action == 'write':
        data = {'schema': 1, 'inputs_sha256': fingerprint(), 'source_sha': args.source_sha,
                'web': {'image': args.web, 'index_image': args.web_index},
                'browser': {'image': args.browser, 'index_image': args.browser_index}}
        validate(data)
        args.lock.parent.mkdir(parents=True, exist_ok=True)
        args.lock.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    else:
        data = validate(json.loads(args.lock.read_text(encoding='utf-8')))
    if args.action == 'github-output':
        output = 'web_runtime=' + data['web']['image'] + '\nbrowser_runtime=' + data['browser']['image'] + '\n'
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(output)
    if args.action == 'build':
        env = dict(os.environ, WEB_RUNTIME_IMAGE=data['web']['image'], BROWSER_RUNTIME_IMAGE=data['browser']['image'])
        subprocess.run(['docker', 'compose', '--env-file', '.env.console', '--file', 'compose.console.yml', 'build'], env=env, cwd=ROOT, check=True)
    print(json.dumps({'runtime_lock': 'valid', 'inputs_sha256': data['inputs_sha256']}))


if __name__ == '__main__':
    main()
