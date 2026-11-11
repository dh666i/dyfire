"""Verify a candidate reuses its locked runtime; never rebuild a probe image."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.runtime_images import LOCK, validate


def layers(tag):
    return json.loads(subprocess.check_output(['docker','image','inspect',tag,'--format','{{json .RootFS.Layers}}'],text=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--component', choices=['web', 'browser'], required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    lock = validate(json.loads(LOCK.read_text(encoding='utf-8')))
    runtime = lock[args.component]['image']
    # The candidate has already pulled these blobs. This pull only establishes
    # the base's reference for inspection; Docker reuses the same stored layers.
    subprocess.run(['docker', 'pull', runtime], check=True, timeout=300)
    # A smoke run may have pulled the OCI index reference only. Register its
    # runtime child as well before inspecting it on classic Docker stores.
    subprocess.run(['docker', 'pull', args.image], check=True, timeout=300)
    base = layers(runtime)
    candidate = layers(args.image)
    if not base or candidate[:len(base)] != base:
        raise RuntimeError('Candidate does not reuse its verified runtime layers')
    evidence = {'schema': 2, 'component': args.component, 'runtime': runtime,
                'candidate': args.image, 'runtime_layers_reused': len(base),
                'application_layers': len(candidate) - len(base), 'additional_builds': 0}
    output = args.output or Path('image-layer-reuse-' + args.component + '.json')
    output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence))


if __name__=='__main__':main()
