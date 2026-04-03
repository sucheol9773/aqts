"""JWT Authentication middleware for AQTS dashboard."""

from datetime import datetime, timedelta, timezone
from typing import Tuple

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from config.settings import get_settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer scheme for extracting token from header
security = HTTPBearer()


class AuthService:
    """Service for handling JWT authentication and password operations."""

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """
        Verify a plain password against a hashed password.

        Args:
            plain: Plain text password
            hashed: Hashed password

        Returns:
            True if password matches, False otherwise
        """
        return pwd_context.verify(plain, hashed)

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using bcrypt.

        Args:
            password: Plain text password to hash

        Returns:
            Hashed password
        """
        return pwd_context.hash(password)

    @staticmethod
    def create_access_token(data: dict) -> str:
        """
        Create a JWT access token.

        Args:
            data: Payload data to encode in token

        Returns:
            Encoded JWT token
        """
        settings = get_settings()
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(
            hours=settings.dashboard.access_token_expire_hours
        )
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(
            to_encode,
            settings.dashboard.secret_key,
            algorithm="HS256",
        )
        return encoded_jwt

    @staticmethod
    def create_refresh_token(data: dict) -> str:
        """
        Create a JWT refresh token with longer expiry.

        Args:
            data: Payload data to encode in token

        Returns:
            Encoded JWT refresh token
        """
        settings = get_settings()
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.dashboard.refresh_token_expire_days
        )
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(
            to_encode,
            settings.dashboard.secret_key,
            algorithm="HS256",
        )
        return encoded_jwt

    @staticmethod
    def verify_token(token: str) -> dict:
        """
        Verify and decode a JWT token.

        Args:
            token: JWT token to verify

        Returns:
            Decoded token payload

        Raises:
            HTTPException: If token is invalid or expired
        """
        settings = get_settings()
        try:
            payload = jwt.decode(
                token,
                settings.dashboard.secret_key,
                algorithms=["HS256"],
            )
            return payload
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    @staticmethod
    def authenticate(password: str) -> Tuple[str, str]:
        """
        Authenticate a user with password and return access and refresh tokens.

        단일 사용자 시스템이므로 settings.dashboard.password와 직접 비교합니다.
        .env에 bcrypt 해시가 저장된 경우 verify_password를 사용하고,
        평문이 저장된 경우 직접 비교합니다.

        Args:
            password: Password to verify

        Returns:
            Tuple of (access_token, refresh_token)

        Raises:
            HTTPException: If password is incorrect
        """
        settings = get_settings()
        stored = settings.dashboard.password

        # bcrypt 해시 여부 확인 ($2b$ 또는 $2a$ 접두사)
        if stored.startswith(("$2b$", "$2a$")):
            valid = AuthService.verify_password(password, stored)
        else:
            valid = (password == stored)

        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect password",
            )

        # Single-user system, subject is "admin"
        data = {"sub": "admin"}
        access_token = AuthService.create_access_token(data)
        refresh_token = AuthService.create_refresh_token(data)

        return access_token, refresh_token


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    FastAPI dependency to verify JWT token from Authorization header.

    Args:
        credentials: HTTP Bearer credentials from Authorization header

    Returns:
        Username/subject from token payload

    Raises:
        HTTPException: If token is missing, invalid, or expired
    """
    token = credentials.credentials
    payload = AuthService.verify_token(token)
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
