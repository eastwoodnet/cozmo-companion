"""Configuration for Cozmo Companion.

Values can be overridden with environment variables (COZMO_*) or CLI flags
in run.py. CLI flags win over environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Places to look for a Vosk model, in priority order.
_VOSK_SEARCH_DIRS = [
    PROJECT_ROOT / "models",
    PROJECT_ROOT.parent / "Cozmo-Voice-Commands" / "cvc" / "models" / "vosk",
]


def find_vosk_model() -> Optional[Path]:
    """Return the first Vosk model directory found, or None."""
    env = os.environ.get("COZMO_VOSK_MODEL")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    for base in _VOSK_SEARCH_DIRS:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            # A Vosk model dir always contains an "am" or "conf" subfolder.
            if child.is_dir() and ((child / "am").exists() or (child / "conf").exists()):
                return child
    return None


@dataclass
class Config:
    """Runtime configuration."""

    host: str = field(default_factory=lambda: os.environ.get("COZMO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("COZMO_PORT", "8000")))
    language: str = field(default_factory=lambda: os.environ.get("COZMO_LANG", "es"))

    ollama_url: str = field(
        default_factory=lambda: os.environ.get("COZMO_OLLAMA_URL", "http://localhost:11434")
    )
    ollama_model: str = field(default_factory=lambda: os.environ.get("COZMO_OLLAMA_MODEL", "phi3"))

    vosk_model: Optional[Path] = field(default_factory=find_vosk_model)

    photos_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "photos")
    camera_fps: float = 5.0
    camera_quality: int = 60
    status_interval: float = 2.0

    @property
    def ollama_generate_url(self) -> str:
        return self.ollama_url.rstrip("/") + "/api/generate"
