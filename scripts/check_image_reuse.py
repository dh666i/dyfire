"""Build an unpublished code-only probe and prove heavy layers are unchanged.

Runs on the isolated CI runner after the validation image was built. The probe
never starts workers or publishes an image; source bytes are restored in finally.
"""
import argparse
import json
from pathlib import Path
import subprocess


def layers(tag):
    return json.loads(subprocess.check_output(['docker','image','inspect',tag,'--format','{{json .RootFS.Layers}}'],text=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime-refresh',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    baseline=layers('dyfire:validation')
    source=root/'spark_console/version.py'
    original=source.read_bytes()
    try:
        source.write_bytes(original+b'\n# Isolated CI cache-reuse probe.\n')
        result=subprocess.run(['docker','buildx','build','--load','--file','Dockerfile.console',
            '--build-arg','RUNTIME_REFRESH='+args.runtime_refresh,'--tag','dyfire:incremental-probe','.'],
            cwd=root,text=True,capture_output=True,timeout=600)
        if result.returncode:
            raise RuntimeError('Incremental image probe failed: '+result.stderr[-2000:])
        probe=layers('dyfire:incremental-probe')
        # Dockerfile ends in core / spark_console / utils copies. Only the
        # spark_console copy is changed; everything before it must be shared.
        if len(baseline)!=len(probe) or baseline[:-2]!=probe[:-2]:
            raise RuntimeError('Code-only update rebuilt a runtime, font or unrelated application layer')
        if baseline==probe:
            raise RuntimeError('Probe did not produce a changed application layer')
        evidence={'schema':1,'protected_layers_reused':len(baseline)-2,
                  'changed_layers':sum(a!=b for a,b in zip(baseline,probe)),
                  'browser_runtime_reused':True,'font_assets_reused':True,'probe_published':False}
        (root/'image-layer-reuse.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence))
    finally:
        source.write_bytes(original)


if __name__=='__main__':main()
