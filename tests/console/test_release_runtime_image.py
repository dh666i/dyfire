import pytest
from scripts.release_runtime_image import IMAGE,runtime_image


def test_runtime_is_selected_from_same_verified_index_and_keeps_attestation_separate():
    index=IMAGE+'@sha256:'+'a'*64
    manifest={'schemaVersion':2,'manifests':[
        {'platform':{'os':'linux','architecture':'amd64'},'digest':'sha256:'+'b'*64},
        {'platform':{'os':'unknown','architecture':'unknown'},'digest':'sha256:'+'c'*64,
         'annotations':{'vnd.docker.reference.type':'attestation-manifest'}}]}
    assert runtime_image(index,manifest)==IMAGE+'@sha256:'+'b'*64
    assert len(manifest['manifests'])==2
    manifest['manifests'].append(manifest['manifests'][0])
    with pytest.raises(ValueError):runtime_image(index,manifest)


def test_missing_platform_and_foreign_registry_are_rejected():
    with pytest.raises(ValueError):runtime_image(IMAGE+'@sha256:'+'a'*64,{'schemaVersion':2,'manifests':[]})
    with pytest.raises(ValueError):runtime_image('foreign.example/image@sha256:'+'a'*64,{'schemaVersion':2,'layers':[],'config':{}})


def test_single_platform_manifest_keeps_immutable_digest():
    index=IMAGE+'@sha256:'+'a'*64
    assert runtime_image(index,{'schemaVersion':2,'layers':[],'config':{}})==index
