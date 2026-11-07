"""Select a verified runnable image; retain provenance at the original index."""
import json
import re
import subprocess

IMAGE='ghcr.io/dh666i/dyfire'


def runtime_image(index_image,manifest):
    if not re.fullmatch(re.escape(IMAGE)+r'@sha256:[a-f0-9]{64}',index_image):
        raise ValueError('Release index digest is invalid')
    if manifest.get('schemaVersion')!=2:
        raise ValueError('Unsupported registry manifest')
    if 'layers' in manifest and 'config' in manifest:
        return index_image
    candidates=[item for item in manifest.get('manifests',[])
        if item.get('platform',{}).get('os')=='linux'
        and item.get('platform',{}).get('architecture')=='amd64'
        and item.get('annotations',{}).get('vnd.docker.reference.type')!='attestation-manifest']
    if len(candidates)!=1:
        raise ValueError('Expected exactly one Linux amd64 runtime image')
    digest=candidates[0].get('digest','')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):
        raise ValueError('Runtime image digest is invalid')
    return IMAGE+'@'+digest


def resolve(index_image):
    raw=subprocess.check_output(['docker','buildx','imagetools','inspect',index_image,'--raw'],text=True,timeout=60)
    return runtime_image(index_image,json.loads(raw))
