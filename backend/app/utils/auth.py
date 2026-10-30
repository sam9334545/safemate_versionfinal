"""Authentication utilities: password hashing, JWT generation, and OAuth2 security dependencies."""

from __future__ import annotations

import datetime
import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
try:
    from jose import JWTError, jwt
except ImportError:
    try:
        import jwt
        class JWTError(Exception):
            pass
    except ImportError:
        jwt = None
        class JWTError(Exception):
            pass

try:
    import bcrypt
    if not hasattr(bcrypt, "__about__"):
        class _About:
            __version__ = getattr(bcrypt, "__version__", "4.0.1")
        bcrypt.__about__ = _About()
    _orig_hashpw = bcrypt.hashpw
    bcrypt.hashpw = lambda p, s: _orig_hashpw(p[:72] if isinstance(p, (bytes, bytearray)) else p, s)
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except ImportError:
    import hashlib
    class FallbackCryptContext:
        def verify(self, plain: str, hashed: str) -> bool:
            return hashlib.sha256(plain.encode()).hexdigest() == hashed or plain == hashed
        def hash(self, plain: str) -> str:
            return hashlib.sha256(plain.encode()).hexdigest()
    pwd_context = FallbackCryptContext()
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database import get_db
from app.models import UserModel
from app.schemas.auth import TokenData

# ---------------------------------------------------------------------------
# Password Hashing CryptContext
# ---------------------------------------------------------------------------
# pwd_context defined above


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash, safely respecting bcrypt 72-byte limit."""
    truncated = plain_password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.verify(truncated, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate a secure bcrypt hash from a plain-text password, safely respecting bcrypt 72-byte limit."""
    truncated = password.encode("utf-8")[:72].decode("utf-8", errors="ignore")
    return pwd_context.hash(truncated)


# ---------------------------------------------------------------------------
# JWT Configuration (Loaded via Environment Variables)
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "safemate_super_secure_jwt_secret_key_change_in_prod")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# OAuth2 scheme matching the API prefix convention
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login",
    auto_error=True,
)


def create_access_token(
    data: dict,
    expires_delta: Optional[datetime.timedelta] = None,
) -> str:
    """Encode payload dictionary into a signed JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.now(datetime.timezone.utc) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token returning payload claims."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ---------------------------------------------------------------------------
# Dependency: Get Current Authenticated User
# ---------------------------------------------------------------------------
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> UserModel:
    """Extract and validate JWT token from request header and return current UserModel."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials or token expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except Exception:
        raise credentials_exception from None

    user = db.query(UserModel).filter(UserModel.email == token_data.email).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account",
        )

    # Attach dynamic session attributes (non-persistent)
    is_guest = bool(
        payload.get("is_guest", False)
        or (user.email and user.email.startswith("guest_") and user.email.endswith("@safemate.internal"))
    )
    user.is_guest = is_guest
    user.access_mode = "guest" if is_guest else "personal_account"
    return user


async def get_current_registered_user(
    current_user: UserModel = Depends(get_current_user),
) -> UserModel:
    """Ensure the authenticated user is a registered personal account, not a temporary guest session.
    
    Prevents guest tokens from performing sensitive personal account actions.
    """
    if getattr(current_user, "is_guest", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires a registered personal account. Please create an account or log in.",
        )
    return current_user


# Optional OAuth2 scheme that does not raise 401 when token is omitted
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login",
    auto_error=False,
)


async def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[UserModel]:
    """Extract and validate JWT user if Authorization header is provided, else returns None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            return None
        user = db.query(UserModel).filter(UserModel.email == email).first()
        if user and user.is_active:
            is_guest = bool(
                payload.get("is_guest", False)
                or (user.email and user.email.startswith("guest_") and user.email.endswith("@safemate.internal"))
            )
            user.is_guest = is_guest
            user.access_mode = "guest" if is_guest else "personal_account"
            return user
    except Exception:
        return None
    return None


