import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# Default Cloud Run server URL
DEFAULT_SERVER_URL = "https://zener-server-902816427420.us-central1.run.app"


@dataclass
class Config:
    # ── Cloud server ───────────────────────────────────────────────────────────
    server_url: str = DEFAULT_SERVER_URL

    # ── Gemini / GCP (only used when running locally without the server) ───────
    gemini_api_key: str = ""
    gcp_project: str = "zener-ai-hackathon"
    gcp_location: str = "us-central1"

    # ── Per-agent model selection (informational — server chooses its own) ─────
    orchestrator_model: str = "gemini-2.5-pro"
    screen_model: str = "gemini-2.5-flash"
    input_model: str = "gemini-2.5-flash"
    window_model: str = "gemini-2.5-flash"
    shell_model: str = "gemini-2.5-flash"

    # ── Firebase (optional, unused by default) ────────────────────────────────
    firebase_api_key: str = ""
    firebase_project_id: str = "zener-ai-hackathon"
    firebase_auth_domain: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        firebase_project_id = os.getenv("FIREBASE_PROJECT_ID", "zener-ai-hackathon")

        return cls(
            server_url=os.getenv("ZENER_SERVER_URL", DEFAULT_SERVER_URL),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon"),
            gcp_location=os.getenv("GCP_LOCATION", "us-central1"),
            orchestrator_model=os.getenv("ZENER_ORCHESTRATOR_MODEL", "gemini-2.5-pro"),
            screen_model=os.getenv("ZENER_SCREEN_MODEL", "gemini-2.5-flash"),
            input_model=os.getenv("ZENER_INPUT_MODEL", "gemini-2.5-flash"),
            window_model=os.getenv("ZENER_WINDOW_MODEL", "gemini-2.5-flash"),
            shell_model=os.getenv("ZENER_SHELL_MODEL", "gemini-2.5-flash"),
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


def load_saved_config() -> None:
    """Load config from ~/.zener/config.json into the environment."""
    cfg_path = get_cache_dir() / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            for key, val in data.items():
                if key not in os.environ:
                    os.environ[key] = val
            global _config
            _config = None  # reset singleton so it re-reads env
        except Exception:
            pass


def save_config_value(key: str, value: str) -> None:
    """Persist a single key/value to ~/.zener/config.json."""
    cfg_path = get_cache_dir() / "config.json"
    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except Exception:
            pass
    existing[key] = value
    cfg_path.write_text(json.dumps(existing, indent=2))
    cfg_path.chmod(0o600)
    os.environ[key] = value
    global _config
    _config = None
