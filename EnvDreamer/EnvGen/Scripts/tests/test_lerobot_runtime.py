import pytest

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
