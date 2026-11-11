"""Deterministic isolated pytest shards, with complete collection evidence.

Load with ``pytest -p scripts.ci_shard``. Each GitHub job owns its database,
Redis, ports and artifacts; no production test is selected by this plugin.
"""
import hashlib
import json
import os
from pathlib import Path


def shard_number(nodeid, count):
    if count < 1:
        raise ValueError('Shard count must be positive')
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8], 'big') % count


def pytest_collection_modifyitems(config, items):
    count = int(os.environ.get('DYFIRE_TEST_SHARDS', '1'))
    index = int(os.environ.get('DYFIRE_TEST_SHARD', '0'))
    if not 0 <= index < count:
        raise ValueError('Invalid pytest shard index')
    collection = [item.nodeid for item in items]
    selected = [item for item in items if shard_number(item.nodeid, count) == index]
    deselected = [item for item in items if shard_number(item.nodeid, count) != index]
    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    target = os.environ.get('DYFIRE_SHARD_EVIDENCE')
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'commit': os.environ.get('GITHUB_SHA', ''),
            'shard': index, 'shards': count,
            'collected': collection,
            'selected': [item.nodeid for item in selected],
        }, ensure_ascii=False, indent=2), encoding='utf-8')


def verify_shards(records, commit, expected_shards):
    if len(records) != expected_shards:
        raise ValueError('Missing shard evidence')
    indices = {record['shard'] for record in records}
    if indices != set(range(expected_shards)):
        raise ValueError('Duplicate or missing shard indices')
    collection = records[0]['collected']
    if not collection or len(set(collection)) != len(collection):
        raise ValueError('Invalid test collection')
    seen = []
    for record in records:
        if record['commit'] != commit or record['shards'] != expected_shards:
            raise ValueError('Shard commit or count mismatch')
        if record['collected'] != collection:
            raise ValueError('Test collections differ between runners')
        expected = [nodeid for nodeid in collection
                    if shard_number(nodeid, expected_shards) == record['shard']]
        if record['selected'] != expected:
            raise ValueError('Shard omitted or added tests')
        seen.extend(record['selected'])
    if len(seen) != len(collection) or set(seen) != set(collection):
        raise ValueError('Shards do not cover the suite exactly once')
    return {'commit': commit, 'shards': expected_shards, 'tests': len(collection),
            'coverage': 'all collected tests exactly once'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--shards', type=int, required=True)
    args = parser.parse_args()
    records = [json.loads(path.read_text(encoding='utf-8'))
               for path in sorted(args.directory.glob('**/collection.json'))]
    print(json.dumps(verify_shards(records, args.commit, args.shards)))
