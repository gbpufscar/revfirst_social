from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.core.runtime import RuntimeConfig, load_runtime_config, reset_runtime_config_cache


def _clear_caches() -> None:
    get_settings.cache_clear()
    reset_runtime_config_cache()


def test_runtime_config_defaults_when_file_missing(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "runtime-missing.yaml"
    monkeypatch.setenv("RUNTIME_FILE_PATH", str(runtime_path))
    _clear_caches()

    config = load_runtime_config()
    assert config == RuntimeConfig(single_workspace_mode=False, primary_workspace_id=None)

    _clear_caches()


def test_runtime_config_loads_single_workspace_mode(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "runtime.yaml"
    runtime_path.write_text(
        "single_workspace_mode: true\nprimary_workspace_id: 11111111-1111-1111-1111-111111111111\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNTIME_FILE_PATH", str(runtime_path))
    _clear_caches()

    config = load_runtime_config()
    assert config.single_workspace_mode is True
    assert config.primary_workspace_id == "11111111-1111-1111-1111-111111111111"

    _clear_caches()


def test_runtime_config_rejects_single_workspace_without_primary(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "runtime-invalid.yaml"
    runtime_path.write_text("single_workspace_mode: true\nprimary_workspace_id: ''\n", encoding="utf-8")
    monkeypatch.setenv("RUNTIME_FILE_PATH", str(runtime_path))
    _clear_caches()

    with pytest.raises(ValueError):
        load_runtime_config()

    _clear_caches()
