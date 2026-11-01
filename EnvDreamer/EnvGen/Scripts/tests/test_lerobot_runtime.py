import pytest
import sys

from EnvGen.Scripts import lerobot_export


def test_explicit_missing_runtime_is_not_silently_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("LEROBOT_PYTHON", str(tmp_path / "missing-python"))
    with pytest.raises(RuntimeError, match="LEROBOT_PYTHON does not exist"):
        lerobot_export.lerobot_python()


def test_posix_project_runtime_is_found(tmp_path, monkeypatch):
    executable = tmp_path / "bin" / "python"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.delenv("LEROBOT_PYTHON", raising=False)
    monkeypatch.setattr(lerobot_export, "DEFAULT_PYTHON", tmp_path / "missing.exe")
    monkeypatch.setattr(lerobot_export, "POSIX_PYTHON", executable)
    assert lerobot_export.lerobot_python() == executable.resolve()


def test_converter_subprocess_preserves_unicode_diagnostics(tmp_path, monkeypatch):
    converter = tmp_path / 'converter.py'
    converter.write_text('''import json, sys
from pathlib import Path
output = Path(sys.argv[2])
(output / 'meta').mkdir(parents=True)
(output / 'meta/info.json').write_text(json.dumps({'codebase_version': 'v3.0'}))
(output / 'meta/semantic_manifest.json').write_text('{}')
print('中文转换完成')
print('中文诊断', file=sys.stderr)
''', encoding='utf-8')
    monkeypatch.setattr(lerobot_export, 'CONVERTER', converter)
    monkeypatch.setenv('LEROBOT_PYTHON', sys.executable)
    monkeypatch.setenv('PYTHONIOENCODING', 'ascii')
    source = tmp_path / 'source.h5'
    source.touch()
    result = lerobot_export.convert_hdf5(source, tmp_path / 'output', repo_id='test/unicode')
    assert '中文转换完成' in result['stdout']
