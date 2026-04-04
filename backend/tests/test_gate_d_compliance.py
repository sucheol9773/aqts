"""
Gate D — 컴플라이언스 테스트

테스트 범위:
  1. AuditIntegrityStore: 해시 체인 무결성 (추가/검증/변조 탐지)
  2. AuditEntry: 항목 생성, 직렬화, 해시 계산
  3. RetentionStore: 보존 정책/등록/만료/아카이브/삭제/위반 탐지
  4. PIIDetector: 주민번호/전화번호/이메일/계좌/카드/IP/API키 탐지
  5. PIIMaskingEngine: 텍스트/딕셔너리 마스킹, Settings 민감 필드 검증
  6. 통합: 전체 워크플로 (기록→보존→무결성→PII 검사)
"""

from datetime import datetime, timedelta, timezone

from core.compliance.audit_integrity import (
    AuditActionType,
    AuditEntry,
    AuditIntegrityStore,
    IntegrityResult,
)
from core.compliance.pii_masking import (
    PIIDetector,
    PIIMaskingEngine,
    PIIPattern,
)
from core.compliance.retention_policy import (
    DEFAULT_RETENTION_DAYS,
    RetentionCategory,
    RetentionPolicy,
    RetentionStatus,
    RetentionStore,
)


# ══════════════════════════════════════════════════════════════
# 1. AuditIntegrityStore 해시 체인 무결성
# ══════════════════════════════════════════════════════════════
class TestAuditIntegrityStore:
    """감사 로그 무결성 검증"""

    def _make_store_with_entries(self, n: int = 3) -> AuditIntegrityStore:
        store = AuditIntegrityStore()
        for i in range(n):
            store.append(
                action_type=AuditActionType.ORDER_PLACED,
                module="order_executor",
                description=f"Order #{i} placed",
                before_state={"position": i},
                after_state={"position": i + 1},
            )
        return store

    def test_empty_store_is_valid(self):
        """빈 저장소 무결성 검증"""
        store = AuditIntegrityStore()
        result = store.verify_integrity()
        assert result.valid is True
        assert result.total_entries == 0

    def test_single_entry_valid(self):
        """단일 항목 해시 체인"""
        store = self._make_store_with_entries(1)
        result = store.verify_integrity()
        assert result.valid is True
        assert result.total_entries == 1

    def test_multiple_entries_valid(self):
        """다수 항목 해시 체인 무결성"""
        store = self._make_store_with_entries(10)
        result = store.verify_integrity()
        assert result.valid is True
        assert result.total_entries == 10

    def test_first_entry_has_genesis_hash(self):
        """첫 항목의 previous_hash는 GENESIS_HASH"""
        store = self._make_store_with_entries(1)
        assert store._entries[0].previous_hash == AuditIntegrityStore.GENESIS_HASH

    def test_chain_links_correctly(self):
        """해시 체인이 올바르게 연결됨"""
        store = self._make_store_with_entries(3)
        assert store._entries[1].previous_hash == store._entries[0].entry_hash
        assert store._entries[2].previous_hash == store._entries[1].entry_hash

    def test_tampered_entry_detected(self):
        """항목 내용 변조 탐지"""
        store = self._make_store_with_entries(5)

        # 중간 항목 변조
        store._entries[2].description = "TAMPERED CONTENT"

        result = store.verify_integrity()
        assert result.valid is False
        assert result.broken_at_index == 2
        assert "Hash mismatch" in result.details

    def test_tampered_chain_link_detected(self):
        """체인 연결 변조 탐지"""
        store = self._make_store_with_entries(3)

        # previous_hash 변조
        store._entries[1].previous_hash = "0" * 64

        result = store.verify_integrity()
        assert result.valid is False
        assert result.broken_at_index == 1
        assert "Chain broken" in result.details

    def test_entry_hash_deterministic(self):
        """같은 내용은 같은 해시"""
        hash1 = AuditEntry.compute_hash(
            entry_id="test",
            timestamp="2026-01-01T00:00:00",
            action_type="ORDER_PLACED",
            module="test",
            description="test",
            before_state=None,
            after_state=None,
            previous_hash="0" * 64,
        )
        hash2 = AuditEntry.compute_hash(
            entry_id="test",
            timestamp="2026-01-01T00:00:00",
            action_type="ORDER_PLACED",
            module="test",
            description="test",
            before_state=None,
            after_state=None,
            previous_hash="0" * 64,
        )
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """다른 내용은 다른 해시"""
        base_args = {
            "entry_id": "test",
            "timestamp": "2026-01-01T00:00:00",
            "action_type": "ORDER_PLACED",
            "module": "test",
            "description": "test",
            "before_state": None,
            "after_state": None,
            "previous_hash": "0" * 64,
        }
        hash1 = AuditEntry.compute_hash(**base_args)
        modified = {**base_args, "description": "modified"}
        hash2 = AuditEntry.compute_hash(**modified)
        assert hash1 != hash2

    def test_query_by_module(self):
        """모듈별 필터 조회"""
        store = AuditIntegrityStore()
        store.append(action_type="ORDER_PLACED", module="order_executor", description="order 1")
        store.append(action_type="MODE_CHANGED", module="mode_manager", description="mode change")
        store.append(action_type="ORDER_PLACED", module="order_executor", description="order 2")

        results = store.query(module="order_executor")
        assert len(results) == 2
        assert all(e.module == "order_executor" for e in results)

    def test_query_by_action_type(self):
        """액션 유형별 필터 조회"""
        store = AuditIntegrityStore()
        store.append(action_type="ORDER_PLACED", module="test", description="placed")
        store.append(action_type="ORDER_EXECUTED", module="test", description="executed")

        results = store.query(action_type="ORDER_PLACED")
        assert len(results) == 1

    def test_get_stats(self):
        """통계 반환"""
        store = self._make_store_with_entries(5)
        stats = store.get_stats()
        assert stats["total_entries"] == 5
        assert stats["chain_valid"] is True
        assert "by_module" in stats
        assert "by_action" in stats

    def test_last_hash_property(self):
        """마지막 해시 추적"""
        store = AuditIntegrityStore()
        assert store.last_hash == AuditIntegrityStore.GENESIS_HASH

        entry = store.append(action_type="TEST", module="test", description="test")
        assert store.last_hash == entry.entry_hash


# ══════════════════════════════════════════════════════════════
# 2. AuditEntry 테스트
# ══════════════════════════════════════════════════════════════
class TestAuditEntry:
    """AuditEntry 데이터 클래스"""

    def test_to_dict(self):
        """직렬화"""
        entry = AuditEntry(
            entry_id="test-id",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            action_type="ORDER_PLACED",
            module="order_executor",
            description="Test order",
            entry_hash="abc123",
            previous_hash="000000",
        )
        d = entry.to_dict()
        assert d["entry_id"] == "test-id"
        assert d["action_type"] == "ORDER_PLACED"
        assert d["entry_hash"] == "abc123"

    def test_hash_includes_before_after_state(self):
        """before/after state가 해시에 포함됨"""
        hash_without = AuditEntry.compute_hash("id", "ts", "act", "mod", "desc", None, None, "0" * 64)
        hash_with = AuditEntry.compute_hash("id", "ts", "act", "mod", "desc", {"old": 1}, {"new": 2}, "0" * 64)
        assert hash_without != hash_with


# ══════════════════════════════════════════════════════════════
# 3. IntegrityResult 테스트
# ══════════════════════════════════════════════════════════════
class TestIntegrityResult:
    """IntegrityResult 테스트"""

    def test_valid_result(self):
        result = IntegrityResult(valid=True, total_entries=10)
        d = result.to_dict()
        assert d["valid"] is True
        assert d["total_entries"] == 10
        assert d["broken_at_index"] is None

    def test_invalid_result(self):
        result = IntegrityResult(valid=False, total_entries=5, broken_at_index=3, details="Chain broken")
        d = result.to_dict()
        assert d["valid"] is False
        assert d["broken_at_index"] == 3


# ══════════════════════════════════════════════════════════════
# 4. RetentionStore 보존 정책
# ══════════════════════════════════════════════════════════════
class TestRetentionStore:
    """거래 기록 보존 정책"""

    def test_default_policies_initialized(self):
        """기본 보존 정책 초기화"""
        store = RetentionStore()
        policies = store.get_policies()
        assert len(policies) == len(DEFAULT_RETENTION_DAYS)
        # 거래 기록은 5년
        trade_policy = next(p for p in policies if p["category"] == "TRADE_ORDER")
        assert trade_policy["retention_days"] == 5 * 365

    def test_custom_policy_override(self):
        """커스텀 보존 기간 오버라이드"""
        store = RetentionStore(custom_policies={RetentionCategory.TRADE_ORDER: 7 * 365})
        policies = store.get_policies()
        trade_policy = next(p for p in policies if p["category"] == "TRADE_ORDER")
        assert trade_policy["retention_days"] == 7 * 365

    def test_register_record(self):
        """기록 등록"""
        store = RetentionStore()
        record = store.register_record(
            category=RetentionCategory.TRADE_ORDER,
            source_table="orders",
            source_id="order-001",
        )
        assert record.status == RetentionStatus.ACTIVE
        assert record.category == RetentionCategory.TRADE_ORDER
        assert record.days_until_expiry > 0
        assert store.count == 1

    def test_expiry_date_calculated(self):
        """보존 만료일 자동 계산"""
        store = RetentionStore()
        now = datetime.now(timezone.utc)
        record = store.register_record(
            category=RetentionCategory.TRADE_ORDER,
            created_at=now,
        )
        expected_expiry = now + timedelta(days=5 * 365)
        assert abs((record.expires_at - expected_expiry).total_seconds()) < 1

    def test_record_not_expired_within_retention(self):
        """보존 기간 내 기록은 만료되지 않음"""
        store = RetentionStore()
        record = store.register_record(category=RetentionCategory.TRADE_ORDER)
        assert record.is_expired is False

    def test_record_expired_after_retention(self):
        """보존 기간 후 기록은 만료됨"""
        store = RetentionStore()
        past = datetime.now(timezone.utc) - timedelta(days=6 * 365)
        record = store.register_record(
            category=RetentionCategory.TRADE_ORDER,
            created_at=past,
        )
        assert record.is_expired is True

    def test_get_expired_records(self):
        """만료 기록 목록 조회"""
        store = RetentionStore()
        # 만료된 기록
        past = datetime.now(timezone.utc) - timedelta(days=6 * 365)
        store.register_record(category=RetentionCategory.TRADE_ORDER, created_at=past)
        # 활성 기록
        store.register_record(category=RetentionCategory.TRADE_ORDER)

        expired = store.get_expired_records()
        assert len(expired) == 1

    def test_archive_record(self):
        """기록 아카이브"""
        store = RetentionStore()
        record = store.register_record(category=RetentionCategory.TRADE_ORDER)
        archived = store.archive_record(record.record_id)
        assert archived is not None
        assert archived.status == RetentionStatus.ARCHIVED
        assert archived.archived_at is not None

    def test_delete_expired_archived_record(self):
        """만료+아카이브 기록 삭제 성공"""
        store = RetentionStore()
        past = datetime.now(timezone.utc) - timedelta(days=6 * 365)
        record = store.register_record(category=RetentionCategory.TRADE_ORDER, created_at=past)
        store.archive_record(record.record_id)
        deleted = store.delete_record(record.record_id)
        assert deleted is not None
        assert deleted.status == RetentionStatus.DELETED

    def test_cannot_delete_before_expiry(self):
        """보존 기간 전 삭제 차단"""
        store = RetentionStore()
        record = store.register_record(category=RetentionCategory.TRADE_ORDER)
        result = store.delete_record(record.record_id)
        assert result is None
        assert record.status == RetentionStatus.ACTIVE

    def test_cannot_delete_without_archive(self):
        """아카이브 없이 삭제 차단"""
        store = RetentionStore()
        past = datetime.now(timezone.utc) - timedelta(days=6 * 365)
        record = store.register_record(category=RetentionCategory.TRADE_ORDER, created_at=past)
        result = store.delete_record(record.record_id)
        assert result is None  # 아카이브 먼저 필요

    def test_audit_log_10_year_retention(self):
        """감사 로그는 10년 보존"""
        store = RetentionStore()
        policies = store.get_policies()
        audit_policy = next(p for p in policies if p["category"] == "AUDIT_LOG")
        assert audit_policy["retention_days"] == 10 * 365

    def test_get_stats(self):
        """통계"""
        store = RetentionStore()
        store.register_record(category=RetentionCategory.TRADE_ORDER)
        store.register_record(category=RetentionCategory.DECISION_RECORD)
        stats = store.get_stats()
        assert stats["total_records"] == 2
        assert "TRADE_ORDER" in stats["by_category"]

    def test_retention_policy_to_dict(self):
        """RetentionPolicy 직렬화"""
        policy = RetentionPolicy(
            category=RetentionCategory.TRADE_ORDER,
            retention_days=1825,
            description="5년 보존",
        )
        d = policy.to_dict()
        assert d["retention_years"] == 5.0

    def test_retention_record_to_dict(self):
        """RetentionRecord 직렬화"""
        store = RetentionStore()
        record = store.register_record(category=RetentionCategory.TRADE_ORDER)
        d = record.to_dict()
        assert "record_id" in d
        assert "expires_at" in d
        assert d["is_expired"] is False


# ══════════════════════════════════════════════════════════════
# 5. PIIDetector PII 탐지
# ══════════════════════════════════════════════════════════════
class TestPIIDetector:
    """개인정보 탐지"""

    def test_detect_resident_number(self):
        """주민등록번호 탐지"""
        detector = PIIDetector()
        detections = detector.detect("주민번호는 901215-1234567입니다")
        assert any(d.pattern_type == PIIPattern.RESIDENT_NUMBER for d in detections)

    def test_detect_phone_number(self):
        """전화번호 탐지"""
        detector = PIIDetector()
        detections = detector.detect("연락처: 010-1234-5678")
        assert any(d.pattern_type == PIIPattern.PHONE_NUMBER for d in detections)

    def test_detect_email(self):
        """이메일 탐지"""
        detector = PIIDetector()
        detections = detector.detect("이메일: user@example.com")
        assert any(d.pattern_type == PIIPattern.EMAIL for d in detections)

    def test_detect_card_number(self):
        """카드번호 탐지"""
        detector = PIIDetector()
        detections = detector.detect("카드: 1234-5678-9012-3456")
        assert any(d.pattern_type == PIIPattern.CARD_NUMBER for d in detections)

    def test_detect_ip_address(self):
        """IP 주소 탐지"""
        detector = PIIDetector()
        detections = detector.detect("서버 IP: 192.168.1.100")
        assert any(d.pattern_type == PIIPattern.IP_ADDRESS for d in detections)

    def test_detect_api_key(self):
        """API 키 탐지"""
        detector = PIIDetector()
        detections = detector.detect("key=sk_live_abcdefghijklmnop1234")
        assert any(d.pattern_type == PIIPattern.API_KEY for d in detections)

    def test_no_pii_in_clean_text(self):
        """PII 없는 텍스트"""
        detector = PIIDetector()
        assert detector.has_pii("안녕하세요. 좋은 하루입니다.") is False

    def test_has_pii_returns_true(self):
        """PII 존재 여부 확인"""
        detector = PIIDetector()
        assert detector.has_pii("이메일: test@test.com") is True

    def test_detect_multiple_pii(self):
        """여러 PII 동시 탐지"""
        detector = PIIDetector()
        text = "이메일 user@test.com, 전화 010-1234-5678"
        detections = detector.detect(text)
        types = {d.pattern_type for d in detections}
        assert PIIPattern.EMAIL in types
        assert PIIPattern.PHONE_NUMBER in types

    def test_detect_in_dict(self):
        """딕셔너리 내 PII 탐지"""
        detector = PIIDetector()
        data = {
            "name": "홍길동",
            "contact": {"email": "hong@test.com", "phone": "010-9876-5432"},
        }
        detections = detector.detect_in_dict(data)
        assert len(detections) >= 2


# ══════════════════════════════════════════════════════════════
# 6. PIIMaskingEngine 마스킹
# ══════════════════════════════════════════════════════════════
class TestPIIMaskingEngine:
    """PII 마스킹 엔진"""

    def test_mask_email(self):
        """이메일 마스킹"""
        engine = PIIMaskingEngine()
        masked, detections = engine.mask_text("이메일: user@example.com")
        assert "user@example.com" not in masked
        assert "***@***" in masked
        assert len(detections) >= 1

    def test_mask_phone(self):
        """전화번호 마스킹"""
        engine = PIIMaskingEngine()
        masked, _ = engine.mask_text("전화: 010-1234-5678")
        assert "010-1234-5678" not in masked

    def test_mask_dict(self):
        """딕셔너리 마스킹"""
        engine = PIIMaskingEngine()
        data = {
            "user": "홍길동",
            "email": "hong@test.com",
            "nested": {"phone": "010-1111-2222"},
        }
        masked_data, detections = engine.mask_dict(data)
        assert "hong@test.com" not in str(masked_data)
        assert "010-1111-2222" not in str(masked_data)

    def test_clean_text_unchanged(self):
        """PII 없는 텍스트는 변경 없음"""
        engine = PIIMaskingEngine()
        text = "안녕하세요. 좋은 하루입니다."
        masked, detections = engine.mask_text(text)
        assert masked == text
        assert len(detections) == 0

    def test_validate_settings_exposed_password(self):
        """Settings에 노출된 비밀번호 감지"""
        engine = PIIMaskingEngine()
        settings = {
            "db_password": "my_secret_password_123",
            "api_key": "PSbIoQr9PmKIS_APP_KEY_1234567890abcdef",
            "log_level": "INFO",
        }
        violations = engine.validate_settings_masked(settings)
        assert len(violations) >= 1
        assert any(v["field"] == "db_password" for v in violations)

    def test_validate_settings_masked_ok(self):
        """Settings에 마스킹된 값은 위반 없음"""
        engine = PIIMaskingEngine()
        settings = {
            "db_password": "***MASKED***",
            "api_key": "****hidden****",
            "log_level": "INFO",
        }
        violations = engine.validate_settings_masked(settings)
        assert len(violations) == 0

    def test_validate_settings_empty_password_ok(self):
        """빈 비밀번호는 위반 아님"""
        engine = PIIMaskingEngine()
        settings = {"db_password": "", "api_key": ""}
        violations = engine.validate_settings_masked(settings)
        assert len(violations) == 0

    def test_validate_nested_settings(self):
        """중첩 Settings 검증"""
        engine = PIIMaskingEngine()
        settings = {
            "database": {
                "password": "real_password_here",
                "host": "localhost",
            },
            "telegram": {
                "bot_token": "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ",
            },
        }
        violations = engine.validate_settings_masked(settings)
        assert len(violations) >= 1

    def test_mask_list_in_dict(self):
        """딕셔너리 내 리스트 마스킹"""
        engine = PIIMaskingEngine()
        data = {
            "contacts": ["user1@test.com", "user2@test.com"],
        }
        masked_data, detections = engine.mask_dict(data)
        assert "user1@test.com" not in str(masked_data)
        assert len(detections) >= 2

    def test_detection_to_dict(self):
        """PIIDetection 직렬화 (원본도 부분 마스킹)"""
        detector = PIIDetector()
        detections = detector.detect("이메일: user@example.com")
        if detections:
            d = detections[0].to_dict()
            assert "***" in d["original_value"]  # 원본도 부분 마스킹
            assert "pattern_type" in d


# ══════════════════════════════════════════════════════════════
# 7. 통합 시나리오
# ══════════════════════════════════════════════════════════════
class TestComplianceIntegration:
    """Gate D 통합 시나리오"""

    def test_full_audit_lifecycle(self):
        """감사 로그 전체 수명주기: 기록→검증→조회"""
        store = AuditIntegrityStore()

        # 주문 기록
        store.append(
            action_type=AuditActionType.ORDER_PLACED,
            module="order_executor",
            description="Buy AAPL 100 shares",
            before_state={"cash": 5000000},
            after_state={"cash": 4500000, "AAPL": 100},
        )

        # 체결 기록
        store.append(
            action_type=AuditActionType.ORDER_EXECUTED,
            module="order_executor",
            description="AAPL filled at $150",
            metadata={"fill_price": 150, "quantity": 100},
        )

        # 무결성 검증
        assert store.verify_integrity().valid is True
        assert store.count == 2

        # 조회
        orders = store.query(action_type=AuditActionType.ORDER_PLACED)
        assert len(orders) == 1

    def test_retention_with_audit(self):
        """보존 정책 + 감사 무결성 연동"""
        audit_store = AuditIntegrityStore()
        retention_store = RetentionStore()

        # 주문 감사 로그 기록
        entry = audit_store.append(
            action_type=AuditActionType.ORDER_PLACED,
            module="order_executor",
            description="Order placed",
        )

        # 보존 기록 등록
        record = retention_store.register_record(
            category=RetentionCategory.TRADE_ORDER,
            source_table="audit_logs",
            source_id=entry.entry_id,
        )

        assert record.days_until_expiry > 1800  # 5년 이상
        assert audit_store.verify_integrity().valid is True

    def test_pii_in_audit_detected(self):
        """감사 로그 내 PII 탐지"""
        store = AuditIntegrityStore()
        store.append(
            action_type=AuditActionType.PROFILE_UPDATED,
            module="profile",
            description="User profile updated with email user@test.com",
        )

        detector = PIIDetector()
        entry = store._entries[0]
        assert detector.has_pii(entry.description) is True

    def test_settings_masking_validation(self):
        """Settings 민감 필드 마스킹 검증 (실제 패턴)"""
        engine = PIIMaskingEngine()

        # 마스킹된 설정 (정상)
        masked_settings = {
            "kis": {"app_key": "***MASKED***", "app_secret": "***MASKED***"},
            "db": {"password": "***MASKED***"},
            "anthropic": {"api_key": "***MASKED***"},
            "telegram": {"bot_token": "***MASKED***"},
            "dashboard": {"secret_key": "***MASKED***", "password": "***MASKED***"},
            "redis": {"password": "***MASKED***"},
        }
        violations = engine.validate_settings_masked(masked_settings)
        assert len(violations) == 0

    def test_no_premature_deletion_clean(self):
        """정상 운영 시 조기 삭제 위반 없음"""
        store = RetentionStore()
        past = datetime.now(timezone.utc) - timedelta(days=6 * 365)
        record = store.register_record(
            category=RetentionCategory.TRADE_ORDER,
            created_at=past,
        )
        store.archive_record(record.record_id)
        store.delete_record(record.record_id)

        violations = store.validate_no_premature_deletion()
        assert len(violations) == 0
