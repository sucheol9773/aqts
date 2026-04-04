"""
개인정보 마스킹 검증 (PII Masking & Detection)

Gate D: 민감 데이터 암호화/마스킹 점검

기능:
  - PII 패턴 탐지: 주민번호, 전화번호, 이메일, 계좌번호, 카드번호, IP 주소
  - 자동 마스킹: 탐지된 PII를 마스킹 처리
  - Settings 민감 필드 마스킹 검증
  - 로그/응답 내 PII 유출 검사
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from config.logging import logger


class PIIPattern(str, Enum):
    """PII 패턴 유형"""

    RESIDENT_NUMBER = "RESIDENT_NUMBER"  # 주민등록번호 (000000-0000000)
    PHONE_NUMBER = "PHONE_NUMBER"  # 전화번호 (010-0000-0000)
    EMAIL = "EMAIL"  # 이메일 주소
    ACCOUNT_NUMBER = "ACCOUNT_NUMBER"  # 계좌번호 (숫자 10-14자리 + 하이픈)
    CARD_NUMBER = "CARD_NUMBER"  # 카드번호 (16자리)
    IP_ADDRESS = "IP_ADDRESS"  # IP 주소
    API_KEY = "API_KEY"  # API 키 패턴


# 정규표현식 패턴 (한국 기준)
PII_PATTERNS: dict[PIIPattern, re.Pattern] = {
    PIIPattern.RESIDENT_NUMBER: re.compile(r"\d{6}\s*-\s*[1-4]\d{6}"),
    PIIPattern.PHONE_NUMBER: re.compile(r"01[016789]-?\d{3,4}-?\d{4}"),
    PIIPattern.EMAIL: re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    PIIPattern.ACCOUNT_NUMBER: re.compile(r"\d{3,4}-?\d{2,4}-?\d{4,6}"),
    PIIPattern.CARD_NUMBER: re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"),
    PIIPattern.IP_ADDRESS: re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    PIIPattern.API_KEY: re.compile(r"(?:sk|pk|api|key|token|secret)[_-]?[a-zA-Z0-9_-]{16,}"),
}

# 마스킹 규칙 (패턴별 마스킹 함수)
MASKING_RULES: dict[PIIPattern, str] = {
    PIIPattern.RESIDENT_NUMBER: "******-*******",
    PIIPattern.PHONE_NUMBER: "***-****-****",
    PIIPattern.EMAIL: "***@***",
    PIIPattern.ACCOUNT_NUMBER: "****-**-******",
    PIIPattern.CARD_NUMBER: "****-****-****-****",
    PIIPattern.IP_ADDRESS: "***.***.***.***",
    PIIPattern.API_KEY: "***MASKED***",
}


@dataclass
class PIIDetection:
    """PII 탐지 결과"""

    pattern_type: PIIPattern
    original_value: str
    position: tuple[int, int]  # (start, end) in text
    masked_value: str

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type.value,
            "original_value": self.original_value[:4] + "***",  # 탐지 결과도 부분 마스킹
            "position": list(self.position),
            "masked_value": self.masked_value,
        }


class PIIDetector:
    """PII 패턴 탐지기"""

    def __init__(self, patterns: Optional[dict[PIIPattern, re.Pattern]] = None):
        self._patterns = patterns or PII_PATTERNS

    def detect(self, text: str) -> list[PIIDetection]:
        """텍스트에서 PII 패턴 탐지"""
        detections = []

        for pattern_type, regex in self._patterns.items():
            for match in regex.finditer(text):
                masked = MASKING_RULES.get(pattern_type, "***MASKED***")
                detections.append(
                    PIIDetection(
                        pattern_type=pattern_type,
                        original_value=match.group(),
                        position=(match.start(), match.end()),
                        masked_value=masked,
                    )
                )

        return detections

    def has_pii(self, text: str) -> bool:
        """텍스트에 PII가 포함되어 있는지 여부"""
        for regex in self._patterns.values():
            if regex.search(text):
                return True
        return False

    def detect_in_dict(self, data: dict, path: str = "") -> list[PIIDetection]:
        """딕셔너리 내 모든 문자열 값에서 PII 탐지 (재귀)"""
        detections = []

        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(value, str):
                found = self.detect(value)
                for d in found:
                    d.metadata_path = current_path  # type: ignore
                detections.extend(found)
            elif isinstance(value, dict):
                detections.extend(self.detect_in_dict(value, current_path))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str):
                        found = self.detect(item)
                        detections.extend(found)
                    elif isinstance(item, dict):
                        detections.extend(self.detect_in_dict(item, f"{current_path}[{i}]"))

        return detections


class PIIMaskingEngine:
    """PII 마스킹 엔진"""

    def __init__(self, detector: Optional[PIIDetector] = None):
        self._detector = detector or PIIDetector()

    def mask_text(self, text: str) -> tuple[str, list[PIIDetection]]:
        """텍스트 내 PII를 마스킹 처리"""
        detections = self._detector.detect(text)

        if not detections:
            return text, []

        # 뒤에서부터 치환 (인덱스 유지)
        masked_text = text
        for detection in sorted(detections, key=lambda d: d.position[0], reverse=True):
            start, end = detection.position
            masked_text = masked_text[:start] + detection.masked_value + masked_text[end:]

        logger.debug(f"PII masked: {len(detections)} patterns found and masked")
        return masked_text, detections

    def mask_dict(self, data: dict) -> tuple[dict, list[PIIDetection]]:
        """딕셔너리 내 모든 문자열 값의 PII 마스킹"""
        all_detections = []
        masked_data = self._mask_dict_recursive(data, all_detections)
        return masked_data, all_detections

    def _mask_dict_recursive(self, data: dict, detections: list) -> dict:
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                masked, found = self.mask_text(value)
                result[key] = masked
                detections.extend(found)
            elif isinstance(value, dict):
                result[key] = self._mask_dict_recursive(value, detections)
            elif isinstance(value, list):
                result[key] = self._mask_list_recursive(value, detections)
            else:
                result[key] = value
        return result

    def _mask_list_recursive(self, data: list, detections: list) -> list:
        result = []
        for item in data:
            if isinstance(item, str):
                masked, found = self.mask_text(item)
                result.append(masked)
                detections.extend(found)
            elif isinstance(item, dict):
                result.append(self._mask_dict_recursive(item, detections))
            else:
                result.append(item)
        return result

    def validate_settings_masked(self, settings_dict: dict) -> list[dict]:
        """
        Settings 딕셔너리에서 민감 필드가 마스킹되었는지 검증

        민감 필드 키워드: password, secret, key, token
        """
        SENSITIVE_KEYWORDS = {"password", "secret", "key", "token", "api_key", "app_key", "app_secret"}

        violations = []
        self._check_sensitive_fields(settings_dict, SENSITIVE_KEYWORDS, violations, path="")
        return violations

    def _check_sensitive_fields(self, data: dict, keywords: set, violations: list, path: str) -> None:
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            key_lower = key.lower()

            # 민감 키워드 매칭
            is_sensitive = any(kw in key_lower for kw in keywords)

            if is_sensitive and isinstance(value, str):
                # 빈 문자열, 마스킹 패턴, 또는 매우 짧은 값은 OK
                if value and not self._is_masked(value) and len(value) > 3:
                    violations.append(
                        {
                            "field": current_path,
                            "violation": "SENSITIVE_FIELD_EXPOSED",
                            "value_preview": value[:4] + "***" if len(value) > 4 else "***",
                        }
                    )

            if isinstance(value, dict):
                self._check_sensitive_fields(value, keywords, violations, current_path)

    @staticmethod
    def _is_masked(value: str) -> bool:
        """값이 이미 마스킹되었는지 확인"""
        masked_indicators = ["***", "MASKED", "****", "xxxx", "XXXX", "hidden"]
        return any(indicator in value for indicator in masked_indicators)
