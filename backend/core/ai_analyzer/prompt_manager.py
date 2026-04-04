"""
프롬프트 DB 버전 관리 모듈 (Prompt Version Manager)

Phase 3 확장 - 프롬프트 템플릿의 변경 이력을 관리합니다.

주요 기능:
- MongoDB 기반 프롬프트 템플릿 CRUD 및 버전 관리
- 활성(active) 버전 자동 관리 (동시에 하나만 활성)
- 이전 버전 롤백 지원
- 성능 메트릭 연결 (A/B 테스트용 기반)
- Redis 캐싱으로 빈번한 프롬프트 조회 최적화

사용 패턴:
  pm = PromptManager()
  prompt = await pm.get_active_prompt("sentiment_system")
  await pm.create_version("sentiment_system", new_content, author="auto-optimizer")
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config.logging import logger
from db.database import MongoDBManager, RedisManager

# ══════════════════════════════════════
# 프롬프트 유형 정의
# ══════════════════════════════════════
PROMPT_TYPES = {
    "sentiment_system": "감성 분석 시스템 프롬프트",
    "sentiment_user": "감성 분석 사용자 프롬프트 템플릿",
    "opinion_system": "투자 의견 시스템 프롬프트",
    "opinion_stock": "개별 종목 투자 의견 템플릿",
    "opinion_sector": "섹터 분석 투자 의견 템플릿",
    "opinion_macro": "거시경제 분석 투자 의견 템플릿",
}


@dataclass
class PromptVersion:
    """프롬프트 버전 데이터 컨테이너"""

    prompt_type: str  # PROMPT_TYPES 키
    version: int  # 자동 증가 버전 번호
    content: str  # 프롬프트 내용 전문
    content_hash: str = ""  # SHA-256 해시 (중복 방지)
    is_active: bool = True  # 현재 활성 버전 여부
    author: str = "system"  # 변경 주체 (system, user, auto-optimizer)
    change_note: str = ""  # 변경 사유
    created_at: Optional[datetime] = None

    # 성능 메트릭 (A/B 테스트용)
    metrics: dict = field(default_factory=dict)
    # 예: {"avg_score_accuracy": 0.82, "usage_count": 150}

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """MongoDB 저장용 딕셔너리"""
        return {
            "prompt_type": self.prompt_type,
            "version": self.version,
            "content": self.content,
            "content_hash": self.content_hash,
            "is_active": self.is_active,
            "author": self.author,
            "change_note": self.change_note,
            "created_at": self.created_at,
            "metrics": self.metrics,
        }


# ══════════════════════════════════════
# 프롬프트 관리 서비스
# ══════════════════════════════════════
class PromptManager:
    """
    프롬프트 DB 버전 관리 서비스

    MongoDB 컬렉션: prompt_versions
    Redis 캐시 키: aqts:prompt:{prompt_type}:active
    """

    COLLECTION_NAME = "prompt_versions"
    CACHE_PREFIX = "aqts:prompt:"
    CACHE_TTL = 3600  # 1시간

    async def get_active_prompt(self, prompt_type: str) -> Optional[PromptVersion]:
        """
        활성 프롬프트 조회 (캐시 우선)

        Args:
            prompt_type: PROMPT_TYPES 키

        Returns:
            PromptVersion 또는 None
        """
        # Redis 캐시 확인
        cached = await self._get_cached(prompt_type)
        if cached:
            return cached

        # MongoDB 조회
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        doc = await collection.find_one(
            {"prompt_type": prompt_type, "is_active": True},
            {"_id": 0},
        )

        if not doc:
            return None

        version = self._doc_to_version(doc)

        # 캐시 저장
        await self._set_cached(prompt_type, version)
        return version

    async def get_active_content(self, prompt_type: str) -> Optional[str]:
        """
        활성 프롬프트의 content만 반환 (간편 조회)

        Returns:
            프롬프트 content 문자열 또는 None (미등록 시)
        """
        version = await self.get_active_prompt(prompt_type)
        return version.content if version else None

    async def create_version(
        self,
        prompt_type: str,
        content: str,
        author: str = "system",
        change_note: str = "",
    ) -> PromptVersion:
        """
        새 프롬프트 버전 생성 및 활성화

        기존 활성 버전은 자동으로 비활성화됩니다.
        동일 content_hash가 이미 존재하면 중복 생성을 방지합니다.

        Args:
            prompt_type: PROMPT_TYPES 키
            content: 프롬프트 전문
            author: 변경 주체
            change_note: 변경 사유

        Returns:
            생성된 PromptVersion

        Raises:
            ValueError: prompt_type이 유효하지 않을 때
        """
        if prompt_type not in PROMPT_TYPES:
            raise ValueError(f"Invalid prompt_type: {prompt_type}. " f"Valid types: {list(PROMPT_TYPES.keys())}")

        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)

        # content 해시 중복 확인
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        existing = await collection.find_one({"prompt_type": prompt_type, "content_hash": content_hash})
        if existing:
            logger.info(f"Prompt version duplicate skipped: {prompt_type} " f"hash={content_hash}")
            return self._doc_to_version(existing)

        # 현재 최고 버전 번호 조회
        latest = await collection.find_one(
            {"prompt_type": prompt_type},
            sort=[("version", -1)],
        )
        next_version = (latest["version"] + 1) if latest else 1

        # 기존 활성 버전 비활성화
        await collection.update_many(
            {"prompt_type": prompt_type, "is_active": True},
            {"$set": {"is_active": False}},
        )

        # 새 버전 생성
        new_version = PromptVersion(
            prompt_type=prompt_type,
            version=next_version,
            content=content,
            content_hash=content_hash,
            is_active=True,
            author=author,
            change_note=change_note,
        )

        await collection.insert_one(new_version.to_dict())

        # 캐시 갱신
        await self._set_cached(prompt_type, new_version)

        # 인덱스 확보 (최초 1회)
        await collection.create_index([("prompt_type", 1), ("is_active", 1)], background=True)
        await collection.create_index([("prompt_type", 1), ("version", -1)], background=True)

        logger.info(f"Prompt version created: {prompt_type} v{next_version} " f"by {author} (hash={content_hash})")
        return new_version

    async def rollback(self, prompt_type: str, target_version: int) -> PromptVersion:
        """
        특정 버전으로 롤백 (해당 버전을 활성화)

        Args:
            prompt_type: 프롬프트 유형
            target_version: 롤백 대상 버전 번호

        Returns:
            활성화된 PromptVersion

        Raises:
            ValueError: 대상 버전이 존재하지 않을 때
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)

        target = await collection.find_one({"prompt_type": prompt_type, "version": target_version})
        if not target:
            raise ValueError(f"Version {target_version} not found for {prompt_type}")

        # 현재 활성 버전 비활성화
        await collection.update_many(
            {"prompt_type": prompt_type, "is_active": True},
            {"$set": {"is_active": False}},
        )

        # 대상 버전 활성화
        await collection.update_one(
            {"prompt_type": prompt_type, "version": target_version},
            {"$set": {"is_active": True}},
        )

        # 캐시 갱신
        target["is_active"] = True
        version = self._doc_to_version(target)
        await self._set_cached(prompt_type, version)

        logger.info(f"Prompt rollback: {prompt_type} → v{target_version}")
        return version

    async def get_version_history(
        self,
        prompt_type: str,
        limit: int = 10,
    ) -> list[PromptVersion]:
        """
        프롬프트 버전 이력 조회 (최신순)

        Args:
            prompt_type: 프롬프트 유형
            limit: 최대 조회 건수

        Returns:
            PromptVersion 리스트 (최신순)
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cursor = (
            collection.find(
                {"prompt_type": prompt_type},
                {"_id": 0},
            )
            .sort("version", -1)
            .limit(limit)
        )

        docs = await cursor.to_list(length=limit)
        return [self._doc_to_version(doc) for doc in docs]

    async def update_metrics(
        self,
        prompt_type: str,
        version: int,
        metrics: dict,
    ) -> None:
        """
        특정 버전의 성능 메트릭 업데이트 (A/B 테스트용)

        Args:
            prompt_type: 프롬프트 유형
            version: 버전 번호
            metrics: 업데이트할 메트릭 딕셔너리
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)

        await collection.update_one(
            {"prompt_type": prompt_type, "version": version},
            {"$set": {f"metrics.{k}": v for k, v in metrics.items()}},
        )
        logger.debug(f"Prompt metrics updated: {prompt_type} v{version} → {metrics}")

    async def initialize_defaults(self) -> int:
        """
        기본 프롬프트를 DB에 초기 등록 (이미 존재하면 스킵)

        opinion.py / sentiment.py의 하드코딩된 프롬프트를
        DB에 등록하여 버전 관리 시작점을 제공합니다.

        Returns:
            신규 등록된 프롬프트 수
        """
        from core.ai_analyzer.opinion import (
            _MACRO_OPINION_TEMPLATE,
            _OPINION_SYSTEM_PROMPT,
            _SECTOR_OPINION_TEMPLATE,
            _STOCK_OPINION_TEMPLATE,
        )
        from core.ai_analyzer.sentiment import (
            _SENTIMENT_SYSTEM_PROMPT,
            _SENTIMENT_USER_TEMPLATE,
        )

        defaults = {
            "sentiment_system": _SENTIMENT_SYSTEM_PROMPT,
            "sentiment_user": _SENTIMENT_USER_TEMPLATE,
            "opinion_system": _OPINION_SYSTEM_PROMPT,
            "opinion_stock": _STOCK_OPINION_TEMPLATE,
            "opinion_sector": _SECTOR_OPINION_TEMPLATE,
            "opinion_macro": _MACRO_OPINION_TEMPLATE,
        }

        created = 0
        for prompt_type, content in defaults.items():
            existing = await self.get_active_prompt(prompt_type)
            if not existing:
                await self.create_version(
                    prompt_type=prompt_type,
                    content=content,
                    author="system",
                    change_note="Initial default prompt registration",
                )
                created += 1

        logger.info(f"Prompt defaults initialized: {created} new registrations")
        return created

    # ══════════════════════════════════════
    # 캐시 관리
    # ══════════════════════════════════════
    async def _get_cached(self, prompt_type: str) -> Optional[PromptVersion]:
        """Redis 캐시 조회"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{prompt_type}:active"
            data = await redis.get(key)
            if data:
                doc = json.loads(data)
                return self._doc_to_version(doc)
        except Exception:
            pass
        return None

    async def _set_cached(self, prompt_type: str, version: PromptVersion) -> None:
        """Redis 캐시 저장"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{prompt_type}:active"
            cache_data = version.to_dict()
            cache_data["created_at"] = cache_data["created_at"].isoformat()
            await redis.setex(
                key,
                self.CACHE_TTL,
                json.dumps(cache_data, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"Prompt cache set failed: {e}")

    @staticmethod
    def _doc_to_version(doc: dict) -> PromptVersion:
        """MongoDB 문서 → PromptVersion 변환"""
        created_at = doc.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return PromptVersion(
            prompt_type=doc["prompt_type"],
            version=doc["version"],
            content=doc["content"],
            content_hash=doc.get("content_hash", ""),
            is_active=doc.get("is_active", False),
            author=doc.get("author", "system"),
            change_note=doc.get("change_note", ""),
            created_at=created_at,
            metrics=doc.get("metrics", {}),
        )
