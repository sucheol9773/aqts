"""
Phase 3 테스트: 프롬프트 DB 버전 관리 (PromptManager)

모든 외부 DB(MongoDB, Redis)는 Mock으로 대체합니다.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ai_analyzer.prompt_manager import (
    PROMPT_TYPES,
    PromptManager,
    PromptVersion,
)


@pytest.mark.smoke
class TestPromptVersion:
    """PromptVersion 데이터 구조 테스트"""

    def test_creation_basic(self):
        """기본 프롬프트 버전 생성"""
        content = "당신은 투자 분석 전문가입니다."
        version = PromptVersion(
            prompt_type="sentiment_system",
            version=1,
            content=content,
            author="system",
            change_note="Initial version",
        )

        assert version.prompt_type == "sentiment_system"
        assert version.version == 1
        assert version.content == content
        assert version.is_active is True
        assert version.author == "system"
        assert version.content_hash is not None
        assert len(version.content_hash) == 16  # SHA-256 첫 16자

    def test_content_hash_auto_generation(self):
        """content_hash 자동 생성"""
        content = "테스트 프롬프트"
        version = PromptVersion(
            prompt_type="opinion_system",
            version=1,
            content=content,
        )

        assert version.content_hash is not None
        # 동일한 content에 대해 항상 같은 hash 생성
        version2 = PromptVersion(
            prompt_type="opinion_system",
            version=1,
            content=content,
        )
        assert version.content_hash == version2.content_hash

    def test_content_hash_differs_for_different_content(self):
        """다른 content는 다른 hash 생성"""
        version1 = PromptVersion(
            prompt_type="sentiment_system",
            version=1,
            content="프롬프트 A",
        )
        version2 = PromptVersion(
            prompt_type="sentiment_system",
            version=1,
            content="프롬프트 B",
        )

        assert version1.content_hash != version2.content_hash

    def test_created_at_auto_timestamp(self):
        """created_at 자동 타임스탐프"""
        before = datetime.now(timezone.utc)
        version = PromptVersion(
            prompt_type="opinion_system",
            version=1,
            content="테스트",
        )
        after = datetime.now(timezone.utc)

        assert before <= version.created_at <= after

    def test_created_at_explicit(self):
        """명시적 created_at 설정"""
        now = datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc)
        version = PromptVersion(
            prompt_type="opinion_system",
            version=1,
            content="테스트",
            created_at=now,
        )

        assert version.created_at == now

    def test_metrics_initialization(self):
        """metrics 초기화"""
        version = PromptVersion(
            prompt_type="sentiment_system",
            version=1,
            content="테스트",
        )

        assert isinstance(version.metrics, dict)
        assert len(version.metrics) == 0

    def test_metrics_with_data(self):
        """metrics에 A/B 테스트 데이터 포함"""
        version = PromptVersion(
            prompt_type="sentiment_system",
            version=1,
            content="테스트",
            metrics={"accuracy": 0.85, "usage_count": 150},
        )

        assert version.metrics["accuracy"] == 0.85
        assert version.metrics["usage_count"] == 150

    def test_to_dict(self):
        """MongoDB 저장용 딕셔너리 변환"""
        now = datetime(2026, 4, 3, 10, 30, tzinfo=timezone.utc)
        version = PromptVersion(
            prompt_type="opinion_stock",
            version=3,
            content="종목 분석 템플릿",
            is_active=True,
            author="auto-optimizer",
            change_note="성능 개선",
            created_at=now,
            metrics={"accuracy": 0.82},
        )

        d = version.to_dict()

        assert d["prompt_type"] == "opinion_stock"
        assert d["version"] == 3
        assert d["content"] == "종목 분석 템플릿"
        assert d["is_active"] is True
        assert d["author"] == "auto-optimizer"
        assert d["change_note"] == "성능 개선"
        assert d["created_at"] == now
        assert d["metrics"]["accuracy"] == 0.82


@pytest.mark.smoke
class TestPromptManager:
    """PromptManager CRUD 및 버전 관리 테스트"""

    @pytest.fixture
    def _mock_mongodb(self):
        """MongoDB Mock 컨텍스트"""
        with patch("core.ai_analyzer.prompt_manager.MongoDBManager.get_collection") as mock_get_coll:
            mock_collection = AsyncMock()
            mock_get_coll.return_value = mock_collection
            yield mock_collection

    @pytest.fixture
    def _mock_redis(self):
        """Redis Mock 컨텍스트"""
        with patch("core.ai_analyzer.prompt_manager.RedisManager.get_client") as mock_get_client:
            mock_redis = AsyncMock()
            mock_get_client.return_value = mock_redis
            yield mock_redis

    # ══════════════════════════════════════
    # READ - get_active_prompt
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_get_active_prompt_cache_hit(self, _mock_redis):
        """활성 프롬프트 조회 - Redis 캐시 히트"""
        cached_doc = {
            "prompt_type": "sentiment_system",
            "version": 2,
            "content": "캐시된 감성 분석 프롬프트",
            "content_hash": "abc123def456",
            "is_active": True,
            "author": "system",
            "change_note": "캐시 버전",
            "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc).isoformat(),
            "metrics": {},
        }

        _mock_redis.get.return_value = json.dumps(cached_doc)

        manager = PromptManager()
        version = await manager.get_active_prompt("sentiment_system")

        assert version is not None
        assert version.prompt_type == "sentiment_system"
        assert version.version == 2
        assert version.content == "캐시된 감성 분석 프롬프트"
        _mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_prompt_mongodb_hit(self, _mock_mongodb, _mock_redis):
        """활성 프롬프트 조회 - MongoDB 조회 (캐시 미스)"""
        _mock_redis.get.return_value = None

        mongo_doc = {
            "prompt_type": "opinion_system",
            "version": 1,
            "content": "투자 의견 생성 시스템 프롬프트",
            "content_hash": "xyz789abc123",
            "is_active": True,
            "author": "system",
            "change_note": "Initial",
            "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
            "metrics": {},
        }

        _mock_mongodb.find_one.return_value = mongo_doc

        manager = PromptManager()
        version = await manager.get_active_prompt("opinion_system")

        assert version is not None
        assert version.prompt_type == "opinion_system"
        assert version.version == 1
        _mock_mongodb.find_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_prompt_not_found(self, _mock_mongodb, _mock_redis):
        """활성 프롬프트 조회 - 미등록 프롬프트"""
        _mock_redis.get.return_value = None
        _mock_mongodb.find_one.return_value = None

        manager = PromptManager()
        version = await manager.get_active_prompt("nonexistent_type")

        assert version is None

    @pytest.mark.asyncio
    async def test_get_active_content(self, _mock_mongodb, _mock_redis):
        """활성 프롬프트 content만 조회"""
        _mock_redis.get.return_value = None
        mongo_doc = {
            "prompt_type": "sentiment_user",
            "version": 1,
            "content": "종목 감성 분석을 위해...",
            "content_hash": "hash123",
            "is_active": True,
            "author": "system",
            "change_note": "",
            "created_at": datetime.now(timezone.utc),
            "metrics": {},
        }
        _mock_mongodb.find_one.return_value = mongo_doc

        manager = PromptManager()
        content = await manager.get_active_content("sentiment_user")

        assert content == "종목 감성 분석을 위해..."

    @pytest.mark.asyncio
    async def test_get_active_content_not_found(self, _mock_mongodb, _mock_redis):
        """활성 프롬프트 content 미조회"""
        _mock_redis.get.return_value = None
        _mock_mongodb.find_one.return_value = None

        manager = PromptManager()
        content = await manager.get_active_content("nonexistent")

        assert content is None

    # ══════════════════════════════════════
    # CREATE - create_version
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_create_version_first(self, _mock_mongodb, _mock_redis):
        """첫 프롬프트 버전 생성"""
        _mock_mongodb.find_one.return_value = None  # 기존 버전 없음
        _mock_mongodb.insert_one.return_value = MagicMock()
        _mock_mongodb.create_index.return_value = None

        manager = PromptManager()
        version = await manager.create_version(
            prompt_type="sentiment_system",
            content="새로운 감성 분석 프롬프트",
            author="system",
            change_note="Initial version",
        )

        assert version.prompt_type == "sentiment_system"
        assert version.version == 1
        assert version.is_active is True
        _mock_mongodb.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_version_increment(self, _mock_mongodb, _mock_redis):
        """버전 자동 증가"""
        latest_doc = {
            "prompt_type": "opinion_system",
            "version": 2,
            "content": "기존 내용",
            "content_hash": "old_hash",
            "is_active": True,
        }

        _mock_mongodb.find_one.side_effect = [
            None,  # content_hash 중복 확인
            latest_doc,  # 최고 버전 조회
        ]
        _mock_mongodb.insert_one.return_value = MagicMock()
        _mock_mongodb.create_index.return_value = None

        manager = PromptManager()
        version = await manager.create_version(
            prompt_type="opinion_system",
            content="새로운 의견 프롬프트",
            author="auto-optimizer",
            change_note="성능 개선",
        )

        assert version.version == 3  # 2 + 1

    @pytest.mark.asyncio
    async def test_create_version_invalid_type(self, _mock_mongodb, _mock_redis):
        """유효하지 않은 prompt_type 거절"""
        manager = PromptManager()

        with pytest.raises(ValueError) as exc_info:
            await manager.create_version(
                prompt_type="invalid_type",
                content="테스트",
            )

        assert "Invalid prompt_type" in str(exc_info.value)
        assert "Valid types:" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_version_duplicate_content(self, _mock_mongodb, _mock_redis):
        """동일한 content_hash는 중복 생성 방지"""
        existing_doc = {
            "prompt_type": "sentiment_system",
            "version": 1,
            "content": "감성 분석 프롬프트",
            "content_hash": "abc123def456",
            "is_active": True,
        }

        _mock_mongodb.find_one.return_value = existing_doc
        _mock_mongodb.insert_one.assert_not_called()

        manager = PromptManager()
        version = await manager.create_version(
            prompt_type="sentiment_system",
            content="감성 분석 프롬프트",  # 동일한 내용
            author="user",
        )

        # 기존 버전 반환
        assert version.version == 1
        assert version.is_active is True

    @pytest.mark.asyncio
    async def test_create_version_deactivates_previous(self, _mock_mongodb, _mock_redis):
        """새 버전 생성 시 기존 활성 버전 비활성화"""
        latest_doc = {
            "prompt_type": "opinion_sector",
            "version": 1,
            "content": "구 프롬프트",
            "content_hash": "old_hash",
            "is_active": True,
        }

        _mock_mongodb.find_one.side_effect = [
            None,  # content_hash 중복 확인
            latest_doc,  # 최고 버전 조회
        ]

        manager = PromptManager()
        version = await manager.create_version(
            prompt_type="opinion_sector",
            content="신규 프롬프트",
            author="user",
        )

        # update_many 호출 확인 (기존 활성 버전 비활성화)
        _mock_mongodb.update_many.assert_called()

    @pytest.mark.asyncio
    async def test_create_version_cache_updated(self, _mock_mongodb, _mock_redis):
        """새 버전 생성 후 Redis 캐시 갱신"""
        _mock_mongodb.find_one.side_effect = [
            None,  # content_hash 중복 확인
            None,  # 최고 버전 조회 (첫 버전)
        ]

        manager = PromptManager()
        version = await manager.create_version(
            prompt_type="sentiment_user",
            content="사용자 프롬프트",
        )

        _mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_version_all_prompt_types(self, _mock_mongodb, _mock_redis):
        """모든 정의된 prompt_type 지원"""
        _mock_mongodb.find_one.side_effect = [None, None]

        manager = PromptManager()

        for prompt_type in PROMPT_TYPES.keys():
            _mock_mongodb.find_one.side_effect = [None, None]

            version = await manager.create_version(
                prompt_type=prompt_type,
                content=f"테스트 - {prompt_type}",
            )

            assert version.prompt_type == prompt_type

    # ══════════════════════════════════════
    # UPDATE - rollback
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_rollback_success(self, _mock_mongodb, _mock_redis):
        """이전 버전으로 롤백"""
        target_doc = {
            "prompt_type": "opinion_system",
            "version": 1,
            "content": "v1 프롬프트",
            "content_hash": "v1_hash",
            "is_active": False,
            "author": "system",
            "change_note": "Initial",
            "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "metrics": {},
        }

        _mock_mongodb.find_one.return_value = target_doc
        _mock_mongodb.update_many.return_value = MagicMock()
        _mock_mongodb.update_one.return_value = MagicMock()

        manager = PromptManager()
        version = await manager.rollback("opinion_system", 1)

        assert version.version == 1
        assert version.is_active is True
        # 활성 버전 비활성화 후 대상 버전 활성화
        _mock_mongodb.update_many.assert_called()
        _mock_mongodb.update_one.assert_called()

    @pytest.mark.asyncio
    async def test_rollback_version_not_found(self, _mock_mongodb, _mock_redis):
        """존재하지 않는 버전으로 롤백 시도"""
        _mock_mongodb.find_one.return_value = None

        manager = PromptManager()

        with pytest.raises(ValueError) as exc_info:
            await manager.rollback("sentiment_system", 999)

        assert "Version 999 not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_rollback_cache_updated(self, _mock_mongodb, _mock_redis):
        """롤백 후 Redis 캐시 갱신"""
        target_doc = {
            "prompt_type": "opinion_macro",
            "version": 2,
            "content": "v2 콘텐츠",
            "content_hash": "v2_hash",
            "is_active": False,
            "author": "user",
            "change_note": "Better prompts",
            "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
            "metrics": {},
        }

        _mock_mongodb.find_one.return_value = target_doc

        manager = PromptManager()
        version = await manager.rollback("opinion_macro", 2)

        _mock_redis.setex.assert_called_once()

    # ══════════════════════════════════════
    # QUERY - get_version_history
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_get_version_history_multiple(self, _mock_mongodb):
        """버전 이력 조회 - 여러 버전"""
        docs = [
            {
                "prompt_type": "sentiment_system",
                "version": 3,
                "content": "v3",
                "content_hash": "v3_hash",
                "is_active": True,
                "author": "auto-optimizer",
                "change_note": "Latest",
                "created_at": datetime(2026, 4, 3, tzinfo=timezone.utc),
                "metrics": {"accuracy": 0.88},
            },
            {
                "prompt_type": "sentiment_system",
                "version": 2,
                "content": "v2",
                "content_hash": "v2_hash",
                "is_active": False,
                "author": "user",
                "change_note": "Improved",
                "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                "metrics": {"accuracy": 0.85},
            },
            {
                "prompt_type": "sentiment_system",
                "version": 1,
                "content": "v1",
                "content_hash": "v1_hash",
                "is_active": False,
                "author": "system",
                "change_note": "Initial",
                "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
                "metrics": {},
            },
        ]

        # Create a mock cursor with async to_list method
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=docs)

        # Chain: find().sort().limit() returns mock_cursor
        # find() must return a synchronous object, not a coroutine
        mock_sorted_cursor = MagicMock()
        mock_sorted_cursor.limit.return_value = mock_cursor
        mock_limited_cursor = MagicMock()
        mock_limited_cursor.sort.return_value = mock_sorted_cursor

        # Override find to return non-async mock
        _mock_mongodb.find = MagicMock(return_value=mock_limited_cursor)

        manager = PromptManager()
        history = await manager.get_version_history("sentiment_system")

        assert len(history) == 3
        assert history[0].version == 3  # 최신순
        assert history[1].version == 2
        assert history[2].version == 1

    @pytest.mark.asyncio
    async def test_get_version_history_limit(self, _mock_mongodb):
        """버전 이력 조회 - 제한"""
        docs = [{"prompt_type": "opinion_system", "version": i, "content": f"v{i}",
                 "content_hash": f"hash{i}", "is_active": i==1, "author": "system",
                 "change_note": "", "created_at": datetime.now(timezone.utc), "metrics": {}}
                for i in range(1, 6)]

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=docs[:3])

        mock_sorted_cursor = MagicMock()
        mock_sorted_cursor.limit.return_value = mock_cursor
        mock_limited_cursor = MagicMock()
        mock_limited_cursor.sort.return_value = mock_sorted_cursor

        _mock_mongodb.find = MagicMock(return_value=mock_limited_cursor)

        manager = PromptManager()
        history = await manager.get_version_history("opinion_system", limit=3)

        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_get_version_history_empty(self, _mock_mongodb):
        """버전 이력 조회 - 빈 결과"""
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])

        mock_sorted_cursor = MagicMock()
        mock_sorted_cursor.limit.return_value = mock_cursor
        mock_limited_cursor = MagicMock()
        mock_limited_cursor.sort.return_value = mock_sorted_cursor

        _mock_mongodb.find = MagicMock(return_value=mock_limited_cursor)

        manager = PromptManager()
        history = await manager.get_version_history("nonexistent")

        assert history == []

    # ══════════════════════════════════════
    # A/B TEST - update_metrics
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_update_metrics_single_metric(self, _mock_mongodb):
        """메트릭 업데이트 - 단일 메트릭"""
        manager = PromptManager()
        await manager.update_metrics(
            prompt_type="sentiment_system",
            version=2,
            metrics={"accuracy": 0.85},
        )

        _mock_mongodb.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_metrics_multiple(self, _mock_mongodb):
        """메트릭 업데이트 - 여러 메트릭"""
        manager = PromptManager()
        await manager.update_metrics(
            prompt_type="opinion_system",
            version=1,
            metrics={
                "avg_score_accuracy": 0.82,
                "usage_count": 150,
                "avg_response_time": 1.2,
            },
        )

        _mock_mongodb.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_metrics_overwrites_previous(self, _mock_mongodb):
        """메트릭 업데이트 - 기존 값 덮어쓰기"""
        manager = PromptManager()
        await manager.update_metrics(
            prompt_type="sentiment_user",
            version=3,
            metrics={"accuracy": 0.90},
        )

        _mock_mongodb.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_metrics_empty_metrics(self, _mock_mongodb):
        """메트릭 업데이트 - 빈 메트릭"""
        manager = PromptManager()
        await manager.update_metrics(
            prompt_type="opinion_macro",
            version=1,
            metrics={},
        )

        # 빈 메트릭도 호출됨
        _mock_mongodb.update_one.assert_called_once()

    # ══════════════════════════════════════
    # INITIALIZATION
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_initialize_defaults_first_run(self, _mock_mongodb, _mock_redis):
        """기본 프롬프트 초기화 - 첫 실행"""
        # 모든 기본 프롬프트가 미등록 상태
        _mock_mongodb.find_one.return_value = None
        _mock_redis.get.return_value = None

        def mock_update_many(*args, **kwargs):
            return MagicMock()

        def mock_update_one(*args, **kwargs):
            return MagicMock()

        _mock_mongodb.update_many.side_effect = mock_update_many
        _mock_mongodb.update_one.side_effect = mock_update_one
        _mock_mongodb.insert_one.return_value = MagicMock()
        _mock_mongodb.create_index.return_value = None

        manager = PromptManager()
        created_count = await manager.initialize_defaults()

        # 6개 기본 프롬프트 등록
        assert created_count == 6

    @pytest.mark.asyncio
    async def test_initialize_defaults_partial(self, _mock_mongodb, _mock_redis):
        """기본 프롬프트 초기화 - 일부만 미등록"""
        def side_effect(filter_dict, *args, **kwargs):
            # sentiment_system만 이미 등록됨
            if filter_dict.get("prompt_type") == "sentiment_system":
                return {
                    "prompt_type": "sentiment_system",
                    "version": 1,
                    "content": "existing",
                }
            return None

        _mock_mongodb.find_one.side_effect = side_effect
        _mock_redis.get.return_value = None
        _mock_mongodb.insert_one.return_value = MagicMock()
        _mock_mongodb.create_index.return_value = None

        manager = PromptManager()
        created_count = await manager.initialize_defaults()

        # 5개만 신규 등록
        assert created_count == 5

    @pytest.mark.asyncio
    async def test_initialize_defaults_all_exist(self, _mock_mongodb, _mock_redis):
        """기본 프롬프트 초기화 - 모두 이미 등록됨"""
        _mock_mongodb.find_one.return_value = {
            "prompt_type": "sentiment_system",
            "version": 1,
            "content": "existing",
        }

        manager = PromptManager()
        created_count = await manager.initialize_defaults()

        assert created_count == 0

    # ══════════════════════════════════════
    # CACHING
    # ══════════════════════════════════════
    @pytest.mark.asyncio
    async def test_get_cached_success(self, _mock_redis):
        """Redis 캐시 조회 - 성공"""
        cached_data = {
            "prompt_type": "opinion_system",
            "version": 2,
            "content": "캐시된 프롬프트",
            "content_hash": "cache_hash",
            "is_active": True,
            "author": "system",
            "change_note": "Cached",
            "created_at": datetime(2026, 4, 3, tzinfo=timezone.utc).isoformat(),
            "metrics": {},
        }

        _mock_redis.get.return_value = json.dumps(cached_data)

        manager = PromptManager()
        version = await manager._get_cached("opinion_system")

        assert version is not None
        assert version.version == 2
        assert version.content == "캐시된 프롬프트"

    @pytest.mark.asyncio
    async def test_get_cached_miss(self, _mock_redis):
        """Redis 캐시 조회 - 미스"""
        _mock_redis.get.return_value = None

        manager = PromptManager()
        version = await manager._get_cached("nonexistent")

        assert version is None

    @pytest.mark.asyncio
    async def test_get_cached_exception(self, _mock_redis):
        """Redis 캐시 조회 - 예외 처리"""
        _mock_redis.get.side_effect = Exception("Redis error")

        manager = PromptManager()
        version = await manager._get_cached("opinion_system")

        # 예외 발생해도 None 반환
        assert version is None

    @pytest.mark.asyncio
    async def test_set_cached_success(self, _mock_redis):
        """Redis 캐시 저장 - 성공"""
        version = PromptVersion(
            prompt_type="sentiment_user",
            version=1,
            content="사용자 프롬프트",
        )

        manager = PromptManager()
        await manager._set_cached("sentiment_user", version)

        _mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_cached_exception(self, _mock_redis):
        """Redis 캐시 저장 - 예외 처리"""
        _mock_redis.setex.side_effect = Exception("Redis error")

        version = PromptVersion(
            prompt_type="opinion_macro",
            version=1,
            content="거시 프롬프트",
        )

        manager = PromptManager()
        # 예외 발생해도 함수는 정상 완료
        await manager._set_cached("opinion_macro", version)

        assert True

    # ══════════════════════════════════════
    # DOCUMENT CONVERSION
    # ══════════════════════════════════════
    def test_doc_to_version_with_datetime_object(self):
        """MongoDB 문서 → PromptVersion 변환 - datetime 객체"""
        doc = {
            "prompt_type": "sentiment_system",
            "version": 2,
            "content": "프롬프트 콘텐츠",
            "content_hash": "doc_hash",
            "is_active": True,
            "author": "user",
            "change_note": "Updated",
            "created_at": datetime(2026, 4, 3, 14, 30, tzinfo=timezone.utc),
            "metrics": {"accuracy": 0.87},
        }

        version = PromptManager._doc_to_version(doc)

        assert version.prompt_type == "sentiment_system"
        assert version.version == 2
        assert version.is_active is True
        assert version.metrics["accuracy"] == 0.87

    def test_doc_to_version_with_string_datetime(self):
        """MongoDB 문서 → PromptVersion 변환 - datetime 문자열"""
        doc = {
            "prompt_type": "opinion_system",
            "version": 1,
            "content": "프롬프트",
            "content_hash": "hash",
            "is_active": True,
            "author": "system",
            "change_note": "",
            "created_at": "2026-04-03T14:30:00+00:00",
            "metrics": {},
        }

        version = PromptManager._doc_to_version(doc)

        assert version.created_at is not None
        assert version.created_at.year == 2026

    def test_doc_to_version_missing_optional_fields(self):
        """MongoDB 문서 → PromptVersion 변환 - 선택 필드 누락"""
        doc = {
            "prompt_type": "sentiment_user",
            "version": 3,
            "content": "콘텐츠",
        }

        version = PromptManager._doc_to_version(doc)

        assert version.prompt_type == "sentiment_user"
        # content_hash is computed in PromptVersion.__post_init__ from content
        assert version.content_hash == "62911dc4c82b39df"
        assert version.is_active is False
        assert version.author == "system"
        assert version.metrics == {}

    # ══════════════════════════════════════
    # CONSTANTS AND CONFIGURATION
    # ══════════════════════════════════════
    def test_prompt_types_defined(self):
        """PROMPT_TYPES 정의 확인"""
        assert len(PROMPT_TYPES) == 6
        assert "sentiment_system" in PROMPT_TYPES
        assert "sentiment_user" in PROMPT_TYPES
        assert "opinion_system" in PROMPT_TYPES
        assert "opinion_stock" in PROMPT_TYPES
        assert "opinion_sector" in PROMPT_TYPES
        assert "opinion_macro" in PROMPT_TYPES

    def test_manager_constants(self):
        """PromptManager 상수 확인"""
        manager = PromptManager()
        assert manager.COLLECTION_NAME == "prompt_versions"
        assert manager.CACHE_PREFIX == "aqts:prompt:"
        assert manager.CACHE_TTL == 3600
