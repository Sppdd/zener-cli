import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    # ── Gemini / GCP ──────────────────────────────────────────────────────────
    gemini_api_key: str
    gcp_project: str
    gcp_location: str

    # ── Per-agent model selection ──────────────────────────────────────────────
    # Orchestrator: complex reasoning and delegation
    orchestrator_model: str = "gemini-2.5-flash"
    # Screen agent: fast multimodal screen description
    screen_model: str = "gemini-2.5-flash-lite"
    # Input agent: mouse/keyboard action planning
    input_model: str = "gemini-2.5-flash-lite"
    # Window/yabai agent: space and window management
    window_model: str = "gemini-2.5-flash-lite"
    # Shell agent: file system and shell commands
    shell_model: str = "gemini-2.5-flash-lite"

    # ── Firebase (optional, unused by default) ────────────────────────────────
    firebase_api_key: str = ""
    firebase_project_id: str = "zener-ai-hackathon"
    firebase_auth_domain: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        firebase_project_id = os.getenv("FIREBASE_PROJECT_ID", "zener-ai-hackathon")

        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon"),
            gcp_location=os.getenv("GCP_LOCATION", "us-central1"),
            # Per-agent models (overridable via env)
            orchestrator_model=os.getenv("ZENER_ORCHESTRATOR_MODEL", "gemini-2.5-flash"),
            screen_model=os.getenv("ZENER_SCREEN_MODEL", "gemini-2.5-flash-lite"),
            input_model=os.getenv("ZENER_INPUT_MODEL", "gemini-2.5-flash-lite"),
            window_model=os.getenv("ZENER_WINDOW_MODEL", "gemini-2.5-flash-lite"),
            shell_model=os.getenv("ZENER_SHELL_MODEL", "gemini-2.5-flash-lite"),
            # Firebase (optional)
            firebase_api_key=os.getenv("FIREBASE_API_KEY", ""),
            firebase_project_id=firebase_project_id,
            firebase_auth_domain=os.getenv(
                "FIREBASE_AUTH_DOMAIN", f"{firebase_project_id}.firebaseapp.com"
            ),
        )


@dataclass
class User:
    uid: str
    email: str
    display_name: Optional[str] = None

    def __str__(self) -> str:
        return self.email


_config: Optional[Config] = None
_current_user: Optional[User] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_user(user: Optional[User]) -> None:
    global _current_user
    _current_user = user


def get_user() -> Optional[User]:
    return _current_user


def get_cache_dir() -> Path:
    cache_dir = Path.home() / ".zener"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


def get_temp_dir() -> Path:
    temp_dir = get_cache_dir() / "temp"
    temp_dir.mkdir(exist_ok=True)
    return temp_dir
