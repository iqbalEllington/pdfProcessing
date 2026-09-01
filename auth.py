"""
Authentication for the PDF Overlay API.
-----------------------------------------
JWT-based auth with NO database. Users (username + bcrypt password hash) are
defined entirely via an environment variable (USERS_JSON). Clients log in
once with POST /token (username + password) to get a JWT, then send that
JWT as `Authorization: Bearer <token>` on every subsequent request.

Required environment variables (see .env.example):
  JWT_SECRET_KEY       - long random string used to sign tokens (REQUIRED)
  USERS_JSON           - JSON object mapping username -> bcrypt password hash
                          e.g. {"client1": "$2b$12$....", "client2": "$2b$12$...."}
  JWT_ALGORITHM         - optional, defaults to HS256
  JWT_EXPIRE_MINUTES    - optional, defaults to 120 (2 hours)

Use generate_password_hash.py to create the bcrypt hash for a plaintext
password before putting it in USERS_JSON.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

load_dotenv()  # reads a local .env file if present; real env vars still win

SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "120"))

if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is not set. Generate one with:\n"
        "  python3 -c \"import secrets; print(secrets.token_hex(32))\"\n"
        "and put it in your .env file or environment."
    )

try:
    USERS = json.loads(os.environ.get("USERS_JSON", "{}"))
except json.JSONDecodeError as e:
    raise RuntimeError(f"USERS_JSON is not valid JSON: {e}")

if not USERS:
    raise RuntimeError(
        "USERS_JSON environment variable is empty or not set. It must be a "
        'JSON object like {"client1": "<bcrypt-hash>"}. '
        "Use generate_password_hash.py to create a hash."
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        # malformed hash in USERS_JSON, etc. -> treat as auth failure, not a crash
        return False


def authenticate_user(username: str, password: str) -> bool:
    hashed = USERS.get(username)
    if not hashed:
        return False
    return verify_password(password, hashed)


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """Returns the username encoded in a valid token, or raises 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Optional but recommended: reject tokens for users that were since
    # removed from USERS_JSON (e.g. you revoked a client's access).
    if username not in USERS:
        raise credentials_exception

    return username


async def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency: use as `user: str = Depends(get_current_user)`."""
    return decode_token(token)
