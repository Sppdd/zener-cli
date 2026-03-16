import logging
from typing import Optional
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import auth, credentials, firestore

from . import config

logger = logging.getLogger(__name__)

_firebase_app: Optional[firebase_admin.App] = None
_db: Optional[firestore.Client] = None


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


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        init_firebase()
    return _db


def verify_token(id_token: str) -> auth.UserRecord:
    return auth.verify_id_token(id_token)


def login_with_google(id_token: str) -> config.User:
    user_info = verify_token(id_token)
    
    user = config.User(
        uid=user_info.uid,
        email=user_info.email,
        display_name=user_info.display_name,
    )
    config.set_user(user)
    
    _ensure_user_document(user_info.uid, user_info.email, user_info.display_name)
    
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
    
    db = get_db()
    user_ref = db.collection("users").document(user.uid)
    doc = user_ref.get()
    
    if doc.exists:
        return doc.to_dict().get("usageMinutes", 0.0)
    return 0.0


def update_usage(minutes: float) -> None:
    user = config.get_user()
    if user is None:
        return
    
    db = get_db()
    user_ref = db.collection("users").document(user.uid)
    
    db.run_transaction(lambda tx: {
        tx.update(user_ref, {"usageMinutes": firestore.Increment(minutes)})
    })


def log_action(action_type: str, details: dict) -> str:
    user = config.get_user()
    if user is None:
        return ""
    
    db = get_db()
    session_ref = db.collection("sessions").document()
    
    session_ref.set({
        "uid": user.uid,
        "actionType": action_type,
        "details": details,
        "timestamp": datetime.now(timezone.utc),
    })
    
    return session_ref.id


def logout() -> None:
    config.set_user(None)
    logger.info("User logged out")
