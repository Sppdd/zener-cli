import logging
from typing import Optional, Any
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import auth, credentials, firestore

from . import config

logger = logging.getLogger(__name__)

_firebase_app: Optional[firebase_admin.App] = None
_db: Optional[Any] = None  # firestore.Client — typed as Any to avoid stub issues


def init_firebase() -> None:
    global _firebase_app, _db

    if _firebase_app is not None:
        return

    cfg = config.get_config()

    cred = credentials.ApplicationDefault()
    _firebase_app = firebase_admin.initialize_app(cred, {
        "projectId": cfg.firebase_project_id,
    })

    _db = firestore.client()
    logger.info(f"Firebase initialized for project: {cfg.firebase_project_id}")


def get_db() -> Any:
    global _db
    if _db is None:
        init_firebase()
    return _db


def verify_token(id_token: str) -> Any:
    return auth.verify_id_token(id_token)


def login_with_google(id_token: str) -> config.User:
    user_info = verify_token(id_token)

    uid: str = str(user_info.get("uid", user_info.uid if hasattr(user_info, "uid") else ""))
    email: str = str(user_info.get("email", getattr(user_info, "email", "")))
    display_name: Optional[str] = user_info.get("name") or getattr(user_info, "display_name", None)

    user = config.User(uid=uid, email=email, display_name=display_name)
    config.set_user(user)

    _ensure_user_document(uid, email, display_name)

    return user


def _ensure_user_document(uid: str, email: str, display_name: Optional[str]) -> None:
    db = get_db()
    user_ref = db.collection("users").document(uid)

    if not user_ref.get().exists:
        user_ref.set({
            "email": email,
            "displayName": display_name or "",
            "usageMinutes": 0.0,
            "plan": "free",
            "createdAt": datetime.now(timezone.utc),
        })
        logger.info(f"Created user document for: {email}")


def get_usage() -> float:
    user = config.get_user()
    if user is None:
        return 0.0

    try:
        db = get_db()
        user_ref = db.collection("users").document(user.uid)
        doc = user_ref.get()

        if doc.exists:
            data = doc.to_dict() or {}
            return float(data.get("usageMinutes", 0.0))
    except Exception as e:
        logger.warning(f"Could not fetch usage: {e}")

    return 0.0


def update_usage(minutes: float) -> None:
    user = config.get_user()
    if user is None:
        return

    try:
        db = get_db()
        user_ref = db.collection("users").document(user.uid)
        # Increment via google-cloud-firestore transforms
        from google.cloud.firestore_v1 import transforms as _transforms
        user_ref.update({"usageMinutes": _transforms.Increment(minutes)})
    except Exception as e:
        logger.warning(f"Could not update usage: {e}")


def log_session(task: str, steps: int, success: bool) -> str:
    user = config.get_user()
    if user is None:
        return ""

    try:
        db = get_db()
        session_ref = db.collection("sessions").document()
        session_ref.set({
            "uid": user.uid,
            "task": task,
            "steps": steps,
            "success": success,
            "timestamp": datetime.now(timezone.utc),
        })
        return session_ref.id
    except Exception as e:
        logger.warning(f"Could not log session: {e}")
        return ""


def logout() -> None:
    config.set_user(None)
    logger.info("User logged out")
