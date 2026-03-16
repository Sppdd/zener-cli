"""
server/auth.py
Firebase token verification via Application Default Credentials (ADC).
No API keys in code — Workload Identity on Cloud Run handles auth.
"""
from __future__ import annotations

import os
import logging
from functools import lru_cache

import firebase_admin
from firebase_admin import auth, credentials
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _init_firebase() -> None:
    """Initialize firebase-admin.
    
    Supports two methods:
    1. Application Default Credentials (ADC) - for Workload Identity on Cloud Run
    2. Service Account JSON file - set GOOGLE_APPLICATION_CREDENTIALS path
    """
    if firebase_admin._apps:
        return
    
    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    
    # Check for service account file
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        cred = credentials.Certificate(creds_path)
        logger.info("Firebase Admin initialized with service account: %s", creds_path)
    else:
        # Use ADC (Workload Identity)
        cred = credentials.ApplicationDefault()
        logger.info("Firebase Admin initialized with ADC")
    
    firebase_admin.initialize_app(cred, {"projectId": project_id})
    logger.info("Firebase project: %s", project_id)


async def verify_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims.

    Args:
        id_token: Firebase ID token from the client.

    Returns:
        Decoded token claims dict (uid, email, etc.)

    Raises:
        HTTPException 401 if the token is invalid or revoked.
    """
    _init_firebase()
    try:
        decoded = auth.verify_id_token(id_token, check_revoked=True)
        return decoded
    except auth.RevokedIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please sign in again.",
        )
    except auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please sign in again.",
        )
    except Exception as exc:
        logger.warning("Token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
