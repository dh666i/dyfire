import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

@pytest.fixture
def updater(monkeypatch):
    if sys.platform == 'win32': monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    spec=importlib.util.spec_from_file_location('git_updater',Path(__file__).resolve().parents[2]/'deploy/git_web_updater.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize('path',['spark_console/models.py','core/send.py','requirements.txt','spark_console/auth_scanner.py'])
def test_rejects_execution_and_schema_changes(updater,path):
    updater.git=lambda *args: path if args[0]=='diff' else ''
    with pytest.raises(ValueError):updater.validate_scope('old','new')

def test_allows_web_changes(updater):
    updater.git=lambda *args:'frontend/dist/index.html\nspark_console/templates/accounts.html' if args[0]=='diff' else ''
    updater.validate_scope('old','new')

def test_rejects_symlink(updater):
    updater.git=lambda *args:'120000 blob hash\tfrontend/dist/index.html' if args[0]=='ls-tree' else ''
    with pytest.raises(ValueError):updater.validate_scope('old','new')

def test_rejects_tag_version_mismatch(updater):
    updater.status=lambda **kw:None
    updater.git=lambda *args: {'tag':'v0.1.5','rev-parse':'abcdef','show':'0.1.4'}.get(args[0],'')
    with pytest.raises(ValueError):updater.latest()
