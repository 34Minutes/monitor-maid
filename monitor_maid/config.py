"""Persisted settings. The environment variable XAI_API_KEY wins over the stored key."""

from __future__ import annotations

import os
from dataclasses import dataclass

from monitor_maid.policy import DEFAULT_DENYLIST

# Documented on https://docs.x.ai (models page, fetched 2026-11-08).
DEFAULT_XAI_MODEL = "grok-4.7"
DEFAULT_XAI_BASE = "https://api.x.ai/v1"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


@dataclass
class Settings:
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout: int = 180
    xai_api_key: str = ""
    xai_model: str = DEFAULT_XAI_MODEL
    xai_base_url: str = DEFAULT_XAI_BASE
    xai_timeout: int = 120
    default_execution_mode: str = "manual"
    default_auto_limit: int = 4
    default_grok_relay_mode: str = "manual"
    default_grok_relay_limit: int = 4
    command_timeout: int = 30
    summary_timeout: int = 90
    context_budget: int = 60_000
    denylist: str = DEFAULT_DENYLIST
    grok_review_before_exec: bool = False
    monitor_default: bool = False

    @classmethod
    def load(cls, db) -> "Settings":
        def integer(key: str, default: int) -> int:
            raw = db.get_setting(key, str(default)).strip()
            try:
                return int(raw)
            except ValueError:
                return default

        def flag(key: str, default: bool) -> bool:
            raw = db.get_setting(key, "1" if default else "0").strip()
            return raw in {"1", "true", "True", "yes"}

        return cls(
            ollama_url=db.get_setting("ollama_url", DEFAULT_OLLAMA_URL).strip() or DEFAULT_OLLAMA_URL,
            ollama_model=db.get_setting("ollama_model", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL,
            ollama_timeout=max(10, integer("ollama_timeout", 180)),
            xai_api_key=db.get_setting("xai_api_key", ""),
            xai_model=db.get_setting("xai_model", DEFAULT_XAI_MODEL).strip() or DEFAULT_XAI_MODEL,
            xai_base_url=db.get_setting("xai_base_url", DEFAULT_XAI_BASE).strip() or DEFAULT_XAI_BASE,
            xai_timeout=max(10, integer("xai_timeout", 120)),
            default_execution_mode=db.get_setting("default_execution_mode", "manual") or "manual",
            default_auto_limit=max(0, integer("default_auto_limit", 4)),
            default_grok_relay_mode=db.get_setting("default_grok_relay_mode", "manual") or "manual",
            default_grok_relay_limit=max(0, integer("default_grok_relay_limit", 4)),
            command_timeout=max(1, integer("command_timeout", 30)),
            summary_timeout=max(5, integer("summary_timeout", 90)),
            context_budget=max(2000, integer("context_budget", 60_000)),
            denylist=db.get_setting("denylist", DEFAULT_DENYLIST),
            grok_review_before_exec=flag("grok_review_before_exec", False),
            monitor_default=flag("monitor_default", False),
        )

    def save(self, db) -> None:
        pairs = {
            "ollama_url": self.ollama_url.strip(),
            "ollama_model": self.ollama_model.strip(),
            "ollama_timeout": str(self.ollama_timeout),
            "xai_api_key": self.xai_api_key,
            "xai_model": self.xai_model.strip(),
            "xai_base_url": self.xai_base_url.strip(),
            "xai_timeout": str(self.xai_timeout),
            "default_execution_mode": self.default_execution_mode,
            "default_auto_limit": str(self.default_auto_limit),
            "default_grok_relay_mode": self.default_grok_relay_mode,
            "default_grok_relay_limit": str(self.default_grok_relay_limit),
            "command_timeout": str(self.command_timeout),
            "summary_timeout": str(self.summary_timeout),
            "context_budget": str(self.context_budget),
            "denylist": self.denylist,
            "grok_review_before_exec": "1" if self.grok_review_before_exec else "0",
            "monitor_default": "1" if self.monitor_default else "0",
        }
        for key, value in pairs.items():
            db.set_setting(key, value)


def effective_api_key(settings: Settings) -> str:
    env = os.environ.get("XAI_API_KEY", "").strip()
    if env:
        return env
    return settings.xai_api_key.strip()


def env_key_set() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())
