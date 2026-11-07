"""Generate the single JSON Schema source consumed by frontend API checks."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from spark_console.web.contracts import CONTRACTS


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    destination=ROOT/'frontend/src/api-contracts.json'
    expected=json.dumps({name:model.model_json_schema() for name,model in CONTRACTS.items()},ensure_ascii=False,indent=2)+'\n'
    if args.check:
        if not destination.exists() or destination.read_text(encoding='utf-8')!=expected:
            raise SystemExit('API contract changed: run python scripts/export_contracts.py and include generated schema')
    else:destination.write_text(expected,encoding='utf-8')


if __name__=='__main__':main()
