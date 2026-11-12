from pathlib import Path
import re


version = Path('VERSION').read_text(encoding='utf-8').strip()
source = Path('docs') / f'release-{version}.md'
if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not source.is_file():
    raise SystemExit(f'Missing versioned release notes for {version}.')
notes = source.read_text(encoding='utf-8').strip()
if 'dyfire-manifest' in notes:
    raise SystemExit('Release notes must not contain a deployment manifest.')
required = ('## 新增', '## 修复', '## 兼容性与升级')
missing = [heading for heading in required if heading not in notes]
if missing:
    raise SystemExit('Release notes must include sections: ' + ', '.join(missing))
print(f'Release notes for {version} are complete ({len(notes)} characters).')
