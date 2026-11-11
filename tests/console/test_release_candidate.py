import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.release_candidate import (
    IMAGE, assemble, existing_release_matches, manifest_from_candidate,
    promote, publish, same_manifest, trusted_run, validate_candidate,
)
from scripts.ci_shard import shard_number, verify_shards


SHA = 'a' * 40
VERSION = '0.4.6'


def candidate():
    records = [{'component': name, 'commit': SHA, 'version': VERSION, 'run_id': '123',
                'image': IMAGE + '@sha256:' + str(i) * 64,
                'index_image': IMAGE + '@sha256:' + str(i + 2) * 64}
               for i, name in enumerate(('web', 'browser'), start=1)]
    return assemble(records, SHA, VERSION, 123)


def test_candidate_keeps_tested_runtime_and_attestations_for_both_components():
    value = candidate()
    manifest = manifest_from_candidate(value)
    assert manifest['manifest_version'] == 2
    assert manifest['schema'] == 2
    assert manifest['compatibility']['database'] == 1
    assert manifest['image'] == value['components']['web']['image']
    assert manifest['components']['browser']['index_image'] == value['components']['browser']['index_image']
    assert manifest['build_run_id'] == '123'


@pytest.mark.parametrize('field,bad', [
    ('commit', 'b' * 40), ('version', '0.0.1'), ('run_id', '456'),
    ('event', 'pull_request'), ('branch', 'feature'), ('repository', 'someone/dyfire'),
    ('workflow', '.github/workflows/untrusted.yml'),
    ('compatibility', {'api': 999, 'browser': 1}), ('manifest_version', 1),
])
def test_candidate_rejects_unrelated_or_untrusted_provenance(field, bad):
    value = candidate()
    value[field] = bad
    with pytest.raises(ValueError):
        validate_candidate(value, commit=SHA, version=VERSION, run_id=123)


def test_candidate_rejects_mutable_foreign_missing_or_swapped_images():
    for bad in [IMAGE + ':latest', 'foreign.example/dyfire@sha256:' + 'b' * 64]:
        value = candidate()
        value['components']['web']['image'] = bad
        with pytest.raises(ValueError):
            validate_candidate(value, commit=SHA, version=VERSION, run_id=123)
    value = candidate()
    del value['components']['browser']
    with pytest.raises(ValueError):
        validate_candidate(value, commit=SHA, version=VERSION, run_id=123)
    value = candidate()
    value['components']['web']['component'] = 'browser'
    with pytest.raises(ValueError):
        validate_candidate(value, commit=SHA, version=VERSION, run_id=123)


def test_workflow_run_must_be_exact_main_push_from_our_repository():
    value = {'head_sha': SHA, 'head_branch': 'main', 'event': 'push',
             'head_repository': {'full_name': 'dh666i/dyfire'}, 'path': '.github/workflows/build.yml'}
    assert trusted_run(value, SHA)
    for field, bad in [('head_sha', 'b' * 40), ('event', 'pull_request'), ('head_branch', 'feature'),
                       ('head_repository', {'full_name': 'other/dyfire'})]:
        changed = {**value, field: bad}
        assert not trusted_run(changed, SHA)


def test_registry_output_allows_only_exact_manifest_bytes_and_cli_final_newline():
    raw = b'{"schemaVersion":2,"manifests":[]}'
    image = IMAGE + '@sha256:' + hashlib.sha256(raw).hexdigest()
    assert same_manifest(raw, image)
    assert same_manifest(raw + b'\n', image)
    assert not same_manifest(raw + b' ', image)


def test_existing_release_cannot_be_replaced_with_different_images():
    value = candidate()
    different = manifest_from_candidate(value)
    different['image'] = IMAGE + '@sha256:' + 'f' * 64
    result = SimpleNamespace(returncode=0, stdout=json.dumps({
        'body': '<!-- dyfire-manifest ' + json.dumps(different) + ' -->',
        'isDraft': False, 'isPrerelease': False,
    }))
    with patch('scripts.release_candidate.subprocess.run', return_value=result):
        with pytest.raises(RuntimeError, match='Existing release differs'):
            existing_release_matches(value)


@pytest.mark.parametrize('error', [
    'ERROR: failed to authorize: 401 Unauthorized',
    'ERROR: connection reset by peer',
    'ERROR: context deadline exceeded',
    'ERROR: unexpected status from HEAD request: 404 Not Found',
    'ERROR: proxy.example.invalid: not found',
    'ERROR: ghcr.io/dh666i/dyfire:another-tag: not found',
])
def test_promote_never_treats_registry_errors_as_absent_version_tags(error):
    result = SimpleNamespace(returncode=1, stdout=b'', stderr=error.encode())
    with patch('scripts.release_candidate.subprocess.run', return_value=result) as run:
        with pytest.raises(RuntimeError, match='Cannot verify existing version tag'):
            promote(candidate())
    assert run.call_count == 1
    assert run.call_args.args[0][3] == 'inspect'


@pytest.mark.parametrize('message', ['not found', 'manifest unknown', 'manifest unknown: manifest unknown'])
def test_promote_accepts_explicit_missing_target_and_preserves_verified_digests(message):
    value = candidate()
    manifests = {}
    runtimes = {}
    for name, record in value['components'].items():
        raw = json.dumps({'component': name, 'manifests': []}).encode()
        record['index_image'] = IMAGE + '@sha256:' + hashlib.sha256(raw).hexdigest()
        manifests[f'{IMAGE}:{name}-{VERSION}'] = raw
        if name == 'web':
            manifests[f'{IMAGE}:{VERSION}'] = raw
        runtimes[record['index_image']] = record['image']

    def command_result(command, **kwargs):
        if command[3] == 'inspect':
            return SimpleNamespace(returncode=1, stdout=b'', stderr=f'ERROR: {command[4]}: {message}\n'.encode())
        assert command[3] == 'create'
        return SimpleNamespace(returncode=0)

    with patch('scripts.release_candidate.subprocess.run', side_effect=command_result) as run, \
            patch('scripts.release_candidate.subprocess.check_output', side_effect=lambda command: manifests[command[4]]), \
            patch('scripts.release_candidate.resolve', side_effect=lambda image: runtimes[image]):
        promote(value)
    created = [call.args[0] for call in run.call_args_list if call.args[0][3] == 'create']
    assert len(created) == 3
    assert {command[-1] for command in created} == set(runtimes)


def test_promote_never_overwrites_an_existing_different_manifest():
    result = SimpleNamespace(returncode=0, stdout=b'another manifest', stderr=b'')
    with patch('scripts.release_candidate.subprocess.run', return_value=result) as run:
        with pytest.raises(RuntimeError, match='refusing to overwrite'):
            promote(candidate())
    assert run.call_count == 1


def test_publish_uses_versioned_release_notes_with_only_the_verified_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'docs').mkdir()
    prose = '# Dyfire 0.4.6\n\n只更新改变的组件。\n'
    (tmp_path / 'docs/release-0.4.6.md').write_text(prose, encoding='utf-8')
    destination = tmp_path / 'notes.md'
    with patch('scripts.release_candidate.subprocess.run') as run:
        publish(candidate(), destination)
    notes = destination.read_text(encoding='utf-8')
    assert notes.startswith(prose.rstrip() + '\n\n')
    assert notes.count('<!-- dyfire-manifest ') == 1
    embedded = notes.split('<!-- dyfire-manifest ', 1)[1].split(' -->', 1)[0]
    assert json.loads(embedded) == manifest_from_candidate(candidate())
    assert run.call_args.args[0][-2:] == ['--notes-file', str(destination)]


def test_publish_rejects_manifest_in_release_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/release-0.4.6.md').write_text('<!-- dyfire-manifest {} -->', encoding='utf-8')
    with patch('scripts.release_candidate.subprocess.run') as run:
        with pytest.raises(ValueError, match='must not contain a deployment manifest'):
            publish(candidate(), tmp_path / 'notes.md')
    run.assert_not_called()


def shard_records():
    nodes = [f'test_api.py::test_case[{i}]' for i in range(101)]
    return [{'commit': SHA, 'shard': index, 'shards': 4, 'collected': nodes,
             'selected': [node for node in nodes if shard_number(node, 4) == index]}
            for index in range(4)]


def test_parallel_shards_cover_all_collected_cases_once():
    records = shard_records()
    result = verify_shards(records, SHA, 4)
    assert result['tests'] == 101
    assert all(record['selected'] for record in records)


def test_shard_aggregation_rejects_omissions_duplicates_and_collection_drift():
    records = shard_records()
    for altered in [records[:-1], records + [records[0]]]:
        with pytest.raises(ValueError):
            verify_shards(altered, SHA, 4)
    for field in ['selected', 'collected']:
        changed = copy.deepcopy(records)
        changed[0][field].pop()
        with pytest.raises(ValueError):
            verify_shards(changed, SHA, 4)
    changed = copy.deepcopy(records)
    changed[0]['commit'] = 'b' * 40
    with pytest.raises(ValueError):
        verify_shards(changed, SHA, 4)
