import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    firebase_api_key: str
    firebase_project_id: str
    firebase_auth_domain: str
    gcp_project: str
    gcp_location: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"
    
    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        
        firebase_api_key = os.getenv("FIREBASE_API_KEY", "")
        firebase_project_id = os.getenv("FIREBASE_PROJECT_ID", "zener-ai-hackathon")
        firebase_auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", f"{firebase_project_id}.firebaseapp.com")
        gcp_project = os.getenv("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon")
        gcp_location = os.getenv("GCP_LOCATION", "us-central1")
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        
        return cls(
            firebase_api_key=firebase_api_key,
            firebase_project_id=firebase_project_id,
            firebase_auth_domain=firebase_auth_domain,
            gcp_project=gcp_project,
            gcp_location=gcp_location,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
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
