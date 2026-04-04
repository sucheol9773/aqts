"""
감사 추적 시각화 (Audit Trail Visualization)

Stage 4 잔여 항목: 감사 추적 시각화 대시보드

주요 기능:
  1. 의사결정 타임라인 생성 (7단계 파이프라인 흐름)
  2. 게이트 통과/차단 히트맵 데이터
  3. 일별/시간별 감사 이벤트 집계
  4. 의사결정 상세 뷰 데이터 생성
  5. 리스크 체크 시각화 데이터

통합 대상:
  - DecisionRecord/DecisionRecordStore: 7단계 의사결정 기록
  - AuditIntegrityStore: 감사 로그 무결성 이력
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class TimelineNodeStatus(str, Enum):
    """타임라인 노드 상태"""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    PENDING = "PENDING"


class ChartType(str, Enum):
    """차트 유형"""

    TIMELINE = "TIMELINE"
    HEATMAP = "HEATMAP"
    BAR = "BAR"
    PIE = "PIE"
    TREND = "TREND"


PIPELINE_STEPS = [
    ("step1_input_snapshot", "데이터 수집"),
    ("step2_features", "팩터 분석"),
    ("step3_signals", "시그널 생성"),
    ("step4_ensemble", "앙상블"),
    ("step5_portfolio", "포트폴리오"),
    ("step6_risk_check", "리스크 검증"),
    ("step7_execution", "주문 집행"),
]

GATE_NAMES = [
    "DataGate",
    "FactorGate",
    "SignalGate",
    "EnsembleGate",
    "PortfolioGate",
    "TradingGuardGate",
    "ReconGate",
    "ExecutionGate",
    "FillGate",
]


@dataclass
class TimelineNode:
    """타임라인 단일 노드"""

    step: str
    label: str
    status: TimelineNodeStatus
    duration_ms: Optional[float] = None
    detail: Optional[dict] = None

    def to_dict(self) -> dict:
        result = {
            "step": self.step,
            "label": self.label,
            "status": self.status.value,
        }
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass
class DecisionTimeline:
    """단일 의사결정의 타임라인"""

    decision_id: str
    timestamp: str
    status: str
    nodes: list[TimelineNode] = field(default_factory=list)

    @property
    def completed_steps(self) -> int:
        return sum(1 for n in self.nodes if n.status == TimelineNodeStatus.PASS)

    @property
    def total_steps(self) -> int:
        return len(self.nodes)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "nodes": [n.to_dict() for n in self.nodes],
            "completed_steps": self.completed_steps,
            "total_steps": self.total_steps,
        }


@dataclass
class GateHeatmapCell:
    """게이트 히트맵 단일 셀"""

    gate_name: str
    decision_id: str
    result: str  # PASS / BLOCK
    severity: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "gate_name": self.gate_name,
            "decision_id": self.decision_id,
            "result": self.result,
        }
        if self.severity:
            result["severity"] = self.severity
        return result


@dataclass
class AggregationBucket:
    """시간 기반 집계 버킷"""

    period: str  # ISO 날짜 또는 시간
    total: int = 0
    passed: int = 0
    failed: int = 0
    partial: int = 0

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "partial": self.partial,
        }


@dataclass
class VisualizationData:
    """시각화 데이터 컨테이너"""

    chart_type: ChartType
    title: str
    data: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chart_type": self.chart_type.value,
            "title": self.title,
            "data": self.data,
            "metadata": self.metadata,
        }


class AuditTrailVisualizer:
    """
    감사 추적 시각화 엔진

    DecisionRecord 목록을 받아 다양한 시각화 데이터를 생성합니다.
    """

    def build_timeline(self, record: dict) -> DecisionTimeline:
        """
        단일 DecisionRecord → 7단계 타임라인 생성

        Args:
            record: DecisionRecord.model_dump() 또는 동등한 dict

        Returns:
            DecisionTimeline with 7 nodes
        """
        nodes = []
        for step_key, label in PIPELINE_STEPS:
            step_data = record.get(step_key)
            if step_data is not None:
                status = TimelineNodeStatus.PASS
                detail = {"keys": list(step_data.keys()) if isinstance(step_data, dict) else {"count": len(step_data)}}
            else:
                # PENDING if record is still in progress, SKIP otherwise
                status = TimelineNodeStatus.PENDING if record.get("status") == "PENDING" else TimelineNodeStatus.SKIP
                detail = None

            nodes.append(TimelineNode(step=step_key, label=label, status=status, detail=detail))

        # 리스크 체크 실패 시 FAIL로 표시
        risk_data = record.get("step6_risk_check")
        if risk_data and isinstance(risk_data, dict):
            if risk_data.get("blocked") or risk_data.get("result") == "BLOCK":
                # 리스크에서 블록된 경우 해당 노드를 FAIL로
                for node in nodes:
                    if node.step == "step6_risk_check":
                        node.status = TimelineNodeStatus.FAIL
                        break

        timestamp = record.get("timestamp", "")
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()

        return DecisionTimeline(
            decision_id=record.get("decision_id", "unknown"),
            timestamp=str(timestamp),
            status=record.get("status", "UNKNOWN"),
            nodes=nodes,
        )

    def build_gate_heatmap(self, records: list[dict]) -> VisualizationData:
        """
        다수 DecisionRecord → 게이트 통과/차단 히트맵

        Args:
            records: DecisionRecord dict 목록

        Returns:
            VisualizationData(HEATMAP) with gate cells
        """
        cells = []
        gate_stats: dict[str, dict[str, int]] = {g: {"PASS": 0, "BLOCK": 0} for g in GATE_NAMES}

        for record in records:
            gate_results = record.get("gate_results") or []
            decision_id = record.get("decision_id", "unknown")

            for gate_result in gate_results:
                gate_name = gate_result.get("gate_name", "")
                result = gate_result.get("result", "PASS")
                severity = gate_result.get("severity")

                if gate_name in gate_stats:
                    gate_stats[gate_name][result] = gate_stats[gate_name].get(result, 0) + 1

                cells.append(
                    GateHeatmapCell(
                        gate_name=gate_name,
                        decision_id=decision_id,
                        result=result,
                        severity=severity,
                    ).to_dict()
                )

        return VisualizationData(
            chart_type=ChartType.HEATMAP,
            title="게이트 통과/차단 히트맵",
            data=cells,
            metadata={"gate_stats": gate_stats, "total_records": len(records)},
        )

    def aggregate_by_day(self, records: list[dict], days: int = 7) -> VisualizationData:
        """
        일별 감사 이벤트 집계

        Args:
            records: DecisionRecord dict 목록
            days: 집계 기간 (최근 N일)

        Returns:
            VisualizationData(BAR) with daily buckets
        """
        now = datetime.now(timezone.utc)
        buckets: dict[str, AggregationBucket] = {}

        # 빈 버킷 생성
        for i in range(days):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            buckets[day] = AggregationBucket(period=day)

        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, datetime):
                day_key = ts.strftime("%Y-%m-%d")
            elif isinstance(ts, str):
                day_key = ts[:10]  # YYYY-MM-DD
            else:
                continue

            if day_key not in buckets:
                continue

            bucket = buckets[day_key]
            bucket.total += 1
            status = record.get("status", "")
            if status == "COMPLETE":
                bucket.passed += 1
            elif status == "PARTIAL":
                bucket.partial += 1
            else:
                bucket.failed += 1

        # 날짜순 정렬
        sorted_buckets = sorted(buckets.values(), key=lambda b: b.period)

        return VisualizationData(
            chart_type=ChartType.BAR,
            title=f"최근 {days}일 감사 이벤트 추이",
            data=[b.to_dict() for b in sorted_buckets],
            metadata={"days": days, "total_events": sum(b.total for b in sorted_buckets)},
        )

    def aggregate_by_hour(self, records: list[dict], hours: int = 24) -> VisualizationData:
        """
        시간별 감사 이벤트 집계

        Args:
            records: DecisionRecord dict 목록
            hours: 집계 기간 (최근 N시간)

        Returns:
            VisualizationData(TREND) with hourly buckets
        """
        now = datetime.now(timezone.utc)
        buckets: dict[str, AggregationBucket] = {}

        for i in range(hours):
            hour = (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:00")
            buckets[hour] = AggregationBucket(period=hour)

        for record in records:
            ts = record.get("timestamp")
            if isinstance(ts, datetime):
                hour_key = ts.strftime("%Y-%m-%dT%H:00")
            elif isinstance(ts, str) and len(ts) >= 13:
                hour_key = ts[:13] + ":00"
            else:
                continue

            if hour_key not in buckets:
                continue

            bucket = buckets[hour_key]
            bucket.total += 1
            status = record.get("status", "")
            if status == "COMPLETE":
                bucket.passed += 1
            elif status == "PARTIAL":
                bucket.partial += 1
            else:
                bucket.failed += 1

        sorted_buckets = sorted(buckets.values(), key=lambda b: b.period)

        return VisualizationData(
            chart_type=ChartType.TREND,
            title=f"최근 {hours}시간 감사 이벤트 추이",
            data=[b.to_dict() for b in sorted_buckets],
            metadata={"hours": hours, "total_events": sum(b.total for b in sorted_buckets)},
        )

    def build_status_summary(self, records: list[dict]) -> VisualizationData:
        """
        상태별 분포 (파이 차트 데이터)

        Args:
            records: DecisionRecord dict 목록

        Returns:
            VisualizationData(PIE) with status distribution
        """
        status_counts: dict[str, int] = {}
        for record in records:
            status = record.get("status", "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1

        data = [{"status": status, "count": count} for status, count in sorted(status_counts.items())]

        return VisualizationData(
            chart_type=ChartType.PIE,
            title="의사결정 상태 분포",
            data=data,
            metadata={"total": len(records)},
        )

    def build_decision_detail(self, record: dict) -> dict:
        """
        단일 의사결정 상세 뷰

        Args:
            record: DecisionRecord dict

        Returns:
            Structured detail view dict
        """
        timeline = self.build_timeline(record)

        # 게이트 결과 요약
        gate_results = record.get("gate_results") or []
        gate_summary = {
            "total": len(gate_results),
            "passed": sum(1 for g in gate_results if g.get("result") == "PASS"),
            "blocked": sum(1 for g in gate_results if g.get("result") == "BLOCK"),
        }

        # 리스크 체크 요약
        risk_data = record.get("step6_risk_check")
        risk_summary = None
        if risk_data and isinstance(risk_data, dict):
            risk_summary = {
                "result": risk_data.get("result", "UNKNOWN"),
                "checks_count": len(risk_data) - 1 if "result" in risk_data else len(risk_data),
                "blocked": risk_data.get("blocked", False),
            }

        return {
            "decision_id": record.get("decision_id", "unknown"),
            "timestamp": str(record.get("timestamp", "")),
            "status": record.get("status", "UNKNOWN"),
            "timeline": timeline.to_dict(),
            "gate_summary": gate_summary,
            "risk_summary": risk_summary,
            "has_execution": record.get("step7_execution") is not None,
        }

    def generate_dashboard_data(self, records: list[dict], days: int = 7) -> dict:
        """
        전체 감사 추적 대시보드 데이터 생성

        Args:
            records: DecisionRecord dict 목록
            days: 일별 집계 기간

        Returns:
            Complete dashboard data dict
        """
        return {
            "status_summary": self.build_status_summary(records).to_dict(),
            "daily_trend": self.aggregate_by_day(records, days=days).to_dict(),
            "hourly_trend": self.aggregate_by_hour(records, hours=24).to_dict(),
            "gate_heatmap": self.build_gate_heatmap(records).to_dict(),
            "total_decisions": len(records),
            "recent_timelines": [
                self.build_timeline(r).to_dict()
                for r in sorted(records, key=lambda x: str(x.get("timestamp", "")), reverse=True)[:10]
            ],
        }
