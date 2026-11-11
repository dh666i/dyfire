import json
from pathlib import Path

import pytest

from scripts.runtime_images import INPUTS, fingerprint, validate


def inputs(tmp_path):
    for name in INPUTS:
        (tmp_path / name).write_bytes(b'input\n')
    return tmp_path


def lock(root):
    return {'schema': 1, 'inputs_sha256': fingerprint(root),
            'web': {'image': 'ghcr.io/dh666i/dyfire@sha256:' + 'a' * 64, 'index_image': 'ghcr.io/dh666i/dyfire@sha256:' + 'b' * 64},
            'browser': {'image': 'ghcr.io/dh666i/dyfire@sha256:' + 'c' * 64, 'index_image': 'ghcr.io/dh666i/dyfire@sha256:' + 'd' * 64}}


def test_lock_rejects_changed_runtime_dependencies(tmp_path):
    root = inputs(tmp_path)
    record = lock(root)
    assert validate(record, root) is record
    (root / 'requirements-web.txt').write_text('changed\n')
    with pytest.raises(ValueError, match='inputs changed'):
        validate(record, root)


def test_lock_is_portable_across_checkout_line_endings(tmp_path):
    root = inputs(tmp_path)
    record = lock(root)
    for name in INPUTS:
        (root / name).write_bytes(b'input\r\n')
    assert validate(record, root) is record


@pytest.mark.parametrize('reference', ['ghcr.io/dh666i/dyfire:latest', 'evil.test/image@sha256:' + 'a' * 64, ''])
def test_lock_rejects_mutable_or_foreign_base(tmp_path, reference):
    root = inputs(tmp_path)
    record = lock(root)
    record['web']['image'] = reference
    with pytest.raises(ValueError, match='immutable'):
        validate(record, root)


def test_same_browser_base_cannot_masquerade_as_light_web(tmp_path):
    root = inputs(tmp_path)
    record = lock(root)
    record['web']['image'] = record['browser']['image']
    with pytest.raises(ValueError, match='distinct'):
        validate(record, root)
