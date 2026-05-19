"""
Authentication helpers: admin JWT + device PSK token validation.
"""
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Depends, Header
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jose import jwt, JWTError
import secrets

from config import ADMIN_USER, ADMIN_PASS, JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES, DEVICE_PSK

security = HTTPBasic(auto_error=False)


def create_admin_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": ADMIN_USER, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_admin(credentials: HTTPBasicCredentials = Depends(security),
                 authorization: str = Header(default=None)):
    """Verify admin via Basic auth or Bearer JWT."""
    # Try Bearer JWT first
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            if payload.get("sub") == ADMIN_USER:
                return ADMIN_USER
        except JWTError:
            pass
        raise HTTPException(status_code=401, detail="Invalid token")

    # Try Basic auth
    if credentials:
        if secrets.compare_digest(credentials.username, ADMIN_USER) and \
           secrets.compare_digest(credentials.password, ADMIN_PASS):
            return ADMIN_USER
    raise HTTPException(status_code=401, detail="Unauthorized",
                        headers={"WWW-Authenticate": "Basic"})


def verify_device(x_device_psk: str = Header(default=None)):
    """Verify device request via pre-shared key header."""
    if not x_device_psk or not secrets.compare_digest(x_device_psk, DEVICE_PSK):
        raise HTTPException(status_code=403, detail="Invalid device PSK")
    return True
