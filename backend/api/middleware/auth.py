"""
JWT 인증 미들웨어 (강화 버전)

보안 기능:
  1. Key Rotation: kid 헤더로 현재/이전 키 구분, 이전 키로 검증 후 현재 키로 재발급
  2. Token ID (jti): 고유 토큰 식별자로 revocation 지원
  3. Token Revocation: Redis 기반 블랙리스트 (로그아웃/강제 만료)
  4. bcrypt 전용: 평문 비밀번호 비교 제거, bcrypt 해시 필수
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Tuple

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from config.settings import get_settings

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer scheme
# auto_error=False: credentials 미제공 시 FastAPI 기본 403 대신
# 우리가 직접 401을 반환하여 HTTP 표준(RFC 7235)을 준수합니다.
security = HTTPBearer(auto_error=False)

# ── Key ID 생성 ──
_KID_CURRENT = "current"
_KID_PREVIOUS = "previous"


def _compute_kid(secret_key: str) -> str:
    """시크릿 키의 SHA256 앞 8자를 kid로 사용"""
    return hashlib.sha256(secret_key.encode()).hexdigest()[:8]


# ══════════════════════════════════════
# Token Revocation (인메모리 + Redis 전환 가능)
# ══════════════════════════════════════
class TokenRevocationStore:
    """토큰 무효화 저장소

    jti(토큰 고유 ID)를 블랙리스트에 등록하여 로그아웃/강제 만료를 구현한다.
    현재는 인메모리 구현이며, Redis 연동 시 _blacklist를 Redis SET으로 교체한다.
    """

    def __init__(self) -> None:
        self._blacklist: Set[str] = set()

    def revoke(self, jti: str) -> None:
        """토큰을 무효화한다."""
        self._blacklist.add(jti)

    def is_revoked(self, jti: str) -> bool:
        """토큰이 무효화되었는지 확인한다."""
        return jti in self._blacklist

    def clear_expired(self) -> None:
        """만료된 토큰 정리 (주기적 호출 권장)"""
        # 인메모리 구현에서는 수동 정리 필요
        # Redis 구현에서는 TTL로 자동 만료
        pass


# 싱글톤 인스턴스
_revocation_store = TokenRevocationStore()


def get_revocation_store() -> TokenRevocationStore:
    """TokenRevocationStore 인스턴스 반환"""
    return _revocation_store


# ══════════════════════════════════════
# AuthService
# ══════════════════════════════════════
class AuthService:
    """JWT 인증 및 비밀번호 처리 서비스"""

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        """bcrypt 해시 비밀번호 검증

        Args:
            plain: 평문 비밀번호
            hashed: bcrypt 해시 문자열 ($2b$ 또는 $2a$ 접두사)

        Returns:
            True if password matches, False otherwise
        """
        return pwd_context.verify(plain, hashed)

    @staticmethod
    def hash_password(password: str) -> str:
        """bcrypt로 비밀번호 해싱

        Args:
            password: 평문 비밀번호

        Returns:
            bcrypt 해시 문자열
        """
        return pwd_context.hash(password)

    @staticmethod
    def _get_signing_key() -> Tuple[str, str]:
        """현재 서명 키와 kid 반환

        Returns:
            Tuple of (secret_key, kid)
        """
        settings = get_settings()
        kid = _compute_kid(settings.dashboard.secret_key)
        return settings.dashboard.secret_key, kid

    @staticmethod
    def create_access_token(data: dict) -> str:
        """JWT Access Token 생성

        Args:
            data: 페이로드 데이터 (sub 필수)

        Returns:
            kid 헤더 + jti 포함 JWT 토큰
        """
        settings = get_settings()
        secret_key, kid = AuthService._get_signing_key()

        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.dashboard.access_token_expire_hours)
        to_encode.update(
            {
                "exp": expire,
                "iat": datetime.now(timezone.utc),
                "jti": str(uuid.uuid4()),
                "type": "access",
            }
        )

        encoded_jwt = jwt.encode(
            to_encode,
            secret_key,
            algorithm="HS256",
            headers={"kid": kid},
        )
        return encoded_jwt

    @staticmethod
    def create_refresh_token(data: dict) -> str:
        """JWT Refresh Token 생성

        Args:
            data: 페이로드 데이터 (sub 필수)

        Returns:
            kid 헤더 + jti 포함 JWT 토큰
        """
        settings = get_settings()
        secret_key, kid = AuthService._get_signing_key()

        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(days=settings.dashboard.refresh_token_expire_days)
        to_encode.update(
            {
                "exp": expire,
                "iat": datetime.now(timezone.utc),
                "jti": str(uuid.uuid4()),
                "type": "refresh",
            }
        )

        encoded_jwt = jwt.encode(
            to_encode,
            secret_key,
            algorithm="HS256",
            headers={"kid": kid},
        )
        return encoded_jwt

    @staticmethod
    def _resolve_key_for_verification(token: str) -> str:
        """토큰 헤더의 kid를 기반으로 검증 키 결정

        Key Rotation 로직:
          1. kid가 현재 키와 일치 → 현재 키로 검증
          2. kid가 이전 키와 일치 → 이전 키로 검증 (rotation 유예기간)
          3. kid 없음 (레거시 토큰) → 현재 키로 시도
          4. kid 불일치 → 현재 키로 시도 (호환성)

        Args:
            token: JWT 토큰 문자열

        Returns:
            검증에 사용할 secret_key
        """
        settings = get_settings()
        current_key = settings.dashboard.secret_key
        previous_key = settings.dashboard.previous_secret_key

        try:
            headers = jwt.get_unverified_headers(token)
            token_kid = headers.get("kid")
        except JWTError:
            return current_key

        if token_kid is None:
            # 레거시 토큰 (kid 없음) → 현재 키로 시도
            return current_key

        current_kid = _compute_kid(current_key)
        if token_kid == current_kid:
            return current_key

        if previous_key and token_kid == _compute_kid(previous_key):
            return previous_key

        # kid 불일치 → 현재 키로 시도 (실패하면 verify_token에서 예외 발생)
        return current_key

    @staticmethod
    def verify_token(token: str) -> dict:
        """JWT 토큰 검증 (kid + jti revocation 확인)

        Args:
            token: JWT 토큰 문자열

        Returns:
            디코딩된 페이로드

        Raises:
            HTTPException: 토큰 무효/만료/무효화
        """
        secret_key = AuthService._resolve_key_for_verification(token)

        try:
            payload = jwt.decode(
                token,
                secret_key,
                algorithms=["HS256"],
            )
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

        # jti revocation 확인
        jti = payload.get("jti")
        if jti and _revocation_store.is_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload

    @staticmethod
    def revoke_token(token: str) -> Optional[str]:
        """토큰을 무효화 (로그아웃)

        Args:
            token: 무효화할 JWT 토큰

        Returns:
            무효화된 jti, 또는 jti가 없으면 None
        """
        try:
            # 서명 검증 없이 페이로드만 추출 (이미 만료된 토큰도 revoke 가능)
            payload = jwt.get_unverified_claims(token)
            jti = payload.get("jti")
            if jti:
                _revocation_store.revoke(jti)
                return jti
        except JWTError:
            pass
        return None

    @staticmethod
    def authenticate(password: str) -> Tuple[str, str]:
        """비밀번호 인증 후 토큰 쌍 발급

        bcrypt 해시 비밀번호만 지원. 평문 비교는 보안 위험으로 제거됨.

        Args:
            password: 평문 비밀번호

        Returns:
            Tuple of (access_token, refresh_token)

        Raises:
            HTTPException: 비밀번호 불일치 또는 bcrypt 해시 미설정
        """
        settings = get_settings()
        stored = settings.dashboard.password

        # bcrypt 해시 필수 ($2b$ 또는 $2a$ 접두사)
        if stored.startswith(("$2b$", "$2a$")):
            valid = AuthService.verify_password(password, stored)
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Password must be stored as bcrypt hash. "
                "Generate with: python -c \"from passlib.hash import bcrypt; print(bcrypt.hash('your_password'))\"",
            )

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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """FastAPI 의존성: Authorization 헤더에서 JWT 토큰 검증

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        Username/subject from token payload

    Raises:
        HTTPException: 토큰 미제공, 무효, 만료
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
