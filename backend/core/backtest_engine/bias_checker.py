"""
백테스트 편향 제거 모듈 (Bias Checker)

Stage 3-A: Minimum Realism (편향 제거)

주요 기능:
- Look-ahead bias 탐지: 미래 정보를 현재에 사용하는 오류 제거
- Point-in-time 컴플라이언스: 공시 기준 정보만 사용 가능
- Survivorship bias 탐지: 상장 폐지된 종목 누락 확인
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List
import pandas as pd


@dataclass
class BiasViolation:
    """편향 위반 기록"""
    violation_type: str          # "lookahead", "point_in_time", "survivorship"
    date: datetime
    description: str
    severity: str = "high"       # "high", "medium", "low"


class BiasChecker:
    """백테스트 편향 검사기"""

    def __init__(self):
        """BiasChecker 초기화"""
        self.violations: List[BiasViolation] = []

    def check_point_in_time(
        self,
        data_date: datetime,
        filing_date: datetime,
    ) -> bool:
        """
        Point-in-time 컴플라이언스 확인

        데이터를 사용할 수 있는 시점인지 확인합니다.
        공시 기준 정보는 공시일 이후에만 사용 가능해야 합니다.

        Args:
            data_date: 데이터의 기준일 (데이터를 사용하려는 날짜)
            filing_date: 정보의 공시일 (정보가 공개된 날짜)

        Returns:
            True if point-in-time 컴플라이언스, False otherwise
        """
        # data_date >= filing_date 이면 공시 정보 사용 가능
        is_compliant = data_date >= filing_date
        return is_compliant

    def detect_lookahead(
        self,
        data_records: List[dict],
        reference_date: datetime,
    ) -> List[BiasViolation]:
        """
        Look-ahead bias 탐지

        미래 데이터를 현재에 사용하는 경우를 탐지합니다.
        각 record는 'date' 필드를 포함해야 합니다.

        Args:
            data_records: 데이터 레코드 리스트 (각 record는 'date' 필드 필수)
            reference_date: 기준 날짜 (이 날짜 기준으로 미래 데이터 탐지)

        Returns:
            Look-ahead bias violation 리스트
        """
        violations = []

        for record in data_records:
            if 'date' not in record:
                continue

            record_date = record['date']
            # datetime이 아니면 변환 시도
            if isinstance(record_date, str):
                try:
                    record_date = pd.to_datetime(record_date).to_pydatetime()
                except (ValueError, TypeError):
                    continue
            elif not isinstance(record_date, datetime):
                try:
                    record_date = pd.to_datetime(record_date).to_pydatetime()
                except (ValueError, TypeError):
                    continue

            # record_date > reference_date이면 미래 데이터 사용 (bias)
            if record_date > reference_date:
                violation = BiasViolation(
                    violation_type="lookahead",
                    date=reference_date,
                    description=f"Future data used: record date {record_date} > reference date {reference_date}",
                    severity="high",
                )
                violations.append(violation)

        return violations

    def check_survivorship(
        self,
        universe_dates: pd.Series,
        delisted_tickers: List[str],
    ) -> List[str]:
        """
        Survivorship bias 확인

        백테스트 기간 중 상장 폐지된 종목이 universe에서 누락되었는지 확인합니다.

        Args:
            universe_dates: 백테스트 기간 중 universe에 포함된 날짜별 종목 정보
                            (형식: Series or dict with dates as index/keys)
            delisted_tickers: 상장 폐지된 종목 리스트

        Returns:
            universe에서 누락된 상장 폐지 종목 리스트
        """
        missing_delisted = []

        # delisted_tickers가 universe_dates에 없으면 누락됨
        for ticker in delisted_tickers:
            found = False

            if isinstance(universe_dates, pd.Series):
                # Series의 경우
                if ticker in universe_dates.values or ticker in universe_dates.index:
                    found = True
            elif isinstance(universe_dates, dict):
                # dict의 경우
                for v in universe_dates.values():
                    if isinstance(v, list):
                        if ticker in v:
                            found = True
                            break
                    elif ticker == v:
                        found = True
                        break
            elif isinstance(universe_dates, list):
                # list의 경우
                if ticker in universe_dates:
                    found = True

            if not found:
                missing_delisted.append(ticker)

        return missing_delisted

    def add_violation(self, violation: BiasViolation) -> None:
        """편향 위반 기록 추가"""
        self.violations.append(violation)

    def get_violations(self) -> List[BiasViolation]:
        """편향 위반 기록 반환"""
        return self.violations

    def clear_violations(self) -> None:
        """편향 위반 기록 초기화"""
        self.violations = []

    def has_violations(self) -> bool:
        """편향 위반 여부"""
        return len(self.violations) > 0

    def get_violation_summary(self) -> dict:
        """편향 위반 요약"""
        summary = {
            "total_violations": len(self.violations),
            "high_severity": sum(1 for v in self.violations if v.severity == "high"),
            "medium_severity": sum(1 for v in self.violations if v.severity == "medium"),
            "low_severity": sum(1 for v in self.violations if v.severity == "low"),
            "by_type": {},
        }

        for violation in self.violations:
            if violation.violation_type not in summary["by_type"]:
                summary["by_type"][violation.violation_type] = 0
            summary["by_type"][violation.violation_type] += 1

        return summary
