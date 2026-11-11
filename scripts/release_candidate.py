"""Promote the exact images validated by a trusted main push, without rebuilding."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from scripts.release_runtime_image import IMAGE, resolve

REPO = 'dh666i/dyfire'
COMPONENTS = ('web', 'browser')


def output(command, **kwargs):
    return subprocess.check_output(command, text=True, encoding='utf-8', **kwargs)


def gh_json(*args):
    return json.loads(output(['gh', *args]))


def immutable(value):
    if not isinstance(value, str) or not re.fullmatch(re.escape(IMAGE) + r'@sha256:[a-f0-9]{64}', value):
        raise ValueError('Candidate image must use this repository and an immutable digest')
    return value


def validate_component(value, *, commit, version, run_id):
    if value.get('commit') != commit or value.get('version') != version:
        raise ValueError('Candidate belongs to another commit or version')
    if str(value.get('run_id')) != str(run_id):
        raise ValueError('Candidate belongs to another workflow run')
    if value.get('component') not in COMPONENTS:
        raise ValueError('Unknown release component')
    immutable(value.get('image'))
    immutable(value.get('index_image'))
    return value


def validate_candidate(value, *, commit, version, run_id):
    if value.get('manifest_version') != 2 or value.get('compatibility') != {'api': 1, 'browser': 1, 'database': 1}:
        raise ValueError('Unsupported candidate manifest or component compatibility')
    if value.get('commit') != commit or value.get('version') != version or str(value.get('run_id')) != str(run_id):
        raise ValueError('Candidate provenance does not match the successful run')
    if value.get('repository') != REPO or value.get('workflow') != '.github/workflows/build.yml':
        raise ValueError('Candidate workflow identity is invalid')
    if value.get('event') != 'push' or value.get('branch') != 'main':
        raise ValueError('Only a trusted main push can produce a release candidate')
    if set(value.get('components', {})) != set(COMPONENTS):
        raise ValueError('Candidate must include both component images')
    for component in COMPONENTS:
        record = value['components'][component]
        validate_component(record, commit=commit, version=version, run_id=run_id)
        if record['component'] != component:
            raise ValueError('Candidate components were interchanged')
    return value


def trusted_run(run, commit):
    repository = run.get('head_repository') or run.get('repository') or {}
    return (run.get('head_sha') == commit and run.get('head_branch') == 'main'
            and run.get('event') == 'push' and repository.get('full_name') == REPO
            and run.get('path') == '.github/workflows/build.yml')


def wait_for_candidate(commit, directory, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = gh_json('api', f'repos/{REPO}/actions/workflows/build.yml/runs?event=push&head_sha={commit}&per_page=50')['workflow_runs']
        matches = sorted((run for run in runs if trusted_run(run, commit)), key=lambda run: run['id'], reverse=True)
        success = next((run for run in matches if run.get('conclusion') == 'success'), None)
        if success:
            run_id = success['id']
            directory.mkdir(parents=True, exist_ok=True)
            subprocess.run(['gh', 'run', 'download', str(run_id), '--repo', REPO,
                            '--name', 'release-candidate', '--dir', str(directory)], check=True)
            return run_id
        if matches and all(run.get('status') == 'completed' for run in matches):
            raise RuntimeError('The exact main commit did not pass validation; refusing to publish')
        print('Waiting for the exact main commit to finish validation and candidate checks.', flush=True)
        time.sleep(20)
    raise TimeoutError('The exact main commit has no successful candidate workflow yet')


def component_record(component, index_image, commit, version, run_id):
    immutable(index_image)
    return {'component': component, 'commit': commit, 'version': version,
            'run_id': str(run_id), 'index_image': index_image, 'image': resolve(index_image)}


def assemble(records, commit, version, run_id):
    components = {}
    for record in records:
        validate_component(record, commit=commit, version=version, run_id=run_id)
        if record['component'] in components:
            raise ValueError('Duplicate candidate component')
        components[record['component']] = record
    result = {'manifest_version': 2, 'repository': REPO, 'workflow': '.github/workflows/build.yml',
              'event': 'push', 'branch': 'main', 'commit': commit, 'version': version,
              'run_id': str(run_id), 'components': components,
              'compatibility': {'api': 1, 'browser': 1, 'database': 1}}
    return validate_candidate(result, commit=commit, version=version, run_id=run_id)


def manifest_from_candidate(candidate):
    components = {name: {key: record[key] for key in ('image', 'index_image')}
                  for name, record in candidate['components'].items()}
    # Old hosts compare schema with their installed schema=1 before pulling.
    # schema=2 makes them fail closed instead of deploying the Web image to
    # browser workers. New hosts read database compatibility separately.
    return {'manifest_version': 2, 'version': candidate['version'], 'schema': 2,
            'image': components['web']['image'], 'index_image': components['web']['index_image'],
            'components': components, 'compatibility': candidate['compatibility'],
            'commit': candidate['commit'], 'build_run_id': candidate['run_id']}


def promote(candidate):
    for name, record in candidate['components'].items():
        # A single existing index is copied unchanged, retaining its SBOM and
        # provenance. No build command is allowed in this promotion path.
        tags = [f"{IMAGE}:{name}-{candidate['version']}"]
        if name == 'web':
            tags.append(f"{IMAGE}:{candidate['version']}")
        for tag in tags:
            previous = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', tag, '--raw'], capture_output=True)
            if previous.returncode == 0:
                if not same_manifest(previous.stdout, record['index_image']):
                    raise RuntimeError('Existing version tag points to a different manifest; refusing to overwrite')
            else:
                error = (previous.stderr + previous.stdout).decode('utf-8', errors='replace').strip()
                # A network, proxy or authorization failure is not proof that
                # a version tag is absent. Accept only Buildx's explicit
                # missing-manifest response for this exact target reference.
                missing = re.fullmatch(
                    r'(?:ERROR:\s*)?' + re.escape(tag) + r':\s*(?:not found|manifest unknown(?::\s*manifest unknown)?)',
                    error, flags=re.IGNORECASE,
                )
                if not missing:
                    raise RuntimeError('Cannot verify existing version tag; refusing to publish: ' + error[:500])
            command = ['docker', 'buildx', 'imagetools', 'create', '--prefer-index=false', '--tag', tag, record['index_image']]
            subprocess.run(command, check=True)
            raw = subprocess.check_output(['docker', 'buildx', 'imagetools', 'inspect', tag, '--raw'])
            # --raw prints the manifest with one CLI newline. Validate against
            # either exact bytes or only that final newline stripped.
            if not same_manifest(raw, record['index_image']):
                raise RuntimeError('Promotion changed the verified manifest digest')
        if resolve(record['index_image']) != record['image']:
            raise RuntimeError('Runtime child no longer matches verified candidate')


def same_manifest(raw, image):
    wanted = immutable(image).split('@sha256:')[1]
    return any(hashlib.sha256(data).hexdigest() == wanted for data in (raw, raw.removesuffix(b'\n')))


def existing_release_matches(candidate):
    manifest = manifest_from_candidate(candidate)
    tag = 'v' + candidate['version']
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO, '--json', 'body,isDraft,isPrerelease'], capture_output=True, text=True)
    if existing.returncode == 0:
        data = json.loads(existing.stdout)
        found = re.search(r'<!-- dyfire-manifest (.*?) -->', data['body'], re.S)
        if not found or json.loads(found.group(1)) != manifest or data['isDraft'] or data['isPrerelease']:
            raise RuntimeError('Existing release differs; immutable release will not be overwritten')
        return True
    return False


def publish(candidate, notes_file):
    manifest = manifest_from_candidate(candidate)
    tag = 'v' + candidate['version']
    source = Path('docs') / f"release-{candidate['version']}.md"
    if source.exists():
        notes = source.read_text(encoding='utf-8').rstrip() + '\n\n'
        if 'dyfire-manifest' in notes:
            raise ValueError('Release notes must not contain a deployment manifest')
    else:
        notes = f"Dyfire {candidate['version']}\n\n同一提交的完整测试、容器验收与安全扫描通过后，直接发布已验证的 Web 和浏览器镜像；发行阶段不重新构建。\n\n"
    notes += '<!-- dyfire-manifest ' + json.dumps(manifest) + ' -->\n'
    notes_file.write_text(notes, encoding='utf-8')
    subprocess.run(['gh', 'release', 'create', tag, '--repo', REPO, '--verify-tag',
                    '--title', 'Dyfire ' + candidate['version'], '--notes-file', str(notes_file)], check=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    record = sub.add_parser('record')
    record.add_argument('--component', choices=COMPONENTS, required=True)
    record.add_argument('--index-image', required=True)
    record.add_argument('--output', type=Path, required=True)
    collect = sub.add_parser('assemble')
    collect.add_argument('--directory', type=Path, required=True)
    collect.add_argument('--output', type=Path, required=True)
    release = sub.add_parser('release')
    release.add_argument('--directory', type=Path, default=Path('.artifacts/release-candidate'))
    release.add_argument('--timeout', type=int, default=1500)
    args = parser.parse_args()
    commit = os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Commit must be an exact SHA')
    version = Path('VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version')
    if args.action == 'record':
        value = component_record(args.component, args.index_image, commit, version, os.environ['GITHUB_RUN_ID'])
    elif args.action == 'assemble':
        records = [json.loads(path.read_text()) for path in args.directory.glob('**/component.json')]
        value = assemble(records, commit, version, os.environ['GITHUB_RUN_ID'])
    else:
        if os.environ['GITHUB_REF_NAME'] != 'v' + version:
            raise ValueError('Release requires the version tag, including manual workflow runs')
        run_id = wait_for_candidate(commit, args.directory, args.timeout)
        candidate = json.loads((args.directory / 'candidate.json').read_text())
        validate_candidate(candidate, commit=commit, version=version, run_id=run_id)
        if existing_release_matches(candidate):
            print('The exact verified release is already published.')
            return
        promote(candidate)
        publish(candidate, args.directory / 'release-notes.md')
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
