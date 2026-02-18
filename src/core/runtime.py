"""Runtime configuration loader (workspace execution mode)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from src.core.config import get_settings


class RuntimeConfig(BaseModel):
    single_workspace_mode: bool = False
    primary_workspace_id: Optional[str] = Field(default=None)

    @field_validator("primary_workspace_id", mode="before")
    @classmethod
    def _normalize_primary_workspace_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("primary_workspace_id")
    @classmethod
    def _validate_single_workspace_requirements(cls, value: Optional[str], info) -> Optional[str]:
        single_mode = bool(info.data.get("single_workspace_mode"))
        if single_mode and not value:
            raise ValueError("primary_workspace_id is required when single_workspace_mode=true")
        return value


def _resolve_runtime_path() -> Path:
    settings = get_settings()
    configured = Path(settings.runtime_file_path)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


@lru_cache(maxsize=1)
def load_runtime_config() -> RuntimeConfig:
    path = _resolve_runtime_path()
    if not path.exists():
        return RuntimeConfig()

    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Runtime config must be a YAML object")

    data: Dict[str, Any] = dict(parsed)
    return RuntimeConfig.model_validate(data)


def reset_runtime_config_cache() -> None:
    load_runtime_config.cache_clear()
