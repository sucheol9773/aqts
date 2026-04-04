"""
감사 추적 시각화 테스트

테스트 범위:
  - TimelineNode: 노드 생성, 직렬화
  - DecisionTimeline: 타임라인 생성, completed_steps
  - AuditTrailVisualizer.build_timeline: 7단계 파이프라인 타임라인
  - AuditTrailVisualizer.build_gate_heatmap: 게이트 히트맵
  - AuditTrailVisualizer.aggregate_by_day: 일별 집계
  - AuditTrailVisualizer.aggregate_by_hour: 시간별 집계
  - AuditTrailVisualizer.build_status_summary: 상태 분포
  - AuditTrailVisualizer.build_decision_detail: 상세 뷰
  - AuditTrailVisualizer.generate_dashboard_data: 전체 대시보드
"""

from datetime import datetime, timedelta, timezone

from core.audit.visualization import (
    AggregationBucket,
    AuditTrailVisualizer,
    ChartType,
    DecisionTimeline,
    GateHeatmapCell,
    TimelineNode,
    TimelineNodeStatus,
    VisualizationData,
)


def _make_record(
    decision_id: str = "test-001",
    status: str = "COMPLETE",
    timestamp: datetime | None = None,
    steps: dict | None = None,
    gate_results: list | None = None,
) -> dict:
    """테스트용 DecisionRecord dict 생성"""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    record = {
        "decision_id": decision_id,
        "timestamp": timestamp,
        "status": status,
        "step1_input_snapshot": {"prices": [100], "news": []},
        "step2_features": {"momentum": 0.5, "value": 0.3},
        "step3_signals": [{"ticker": "AAPL", "action": "BUY"}],
        "step4_ensemble": {"weight": 0.7, "confidence": 0.8},
        "step5_portfolio": {"AAPL": 0.4, "MSFT": 0.3},
        "step6_risk_check": {"result": "PASS", "daily_loss": False},
        "step7_execution": [{"ticker": "AAPL", "filled": 10}],
        "gate_results": (
            gate_results
            if gate_results is not None
            else [
                {"gate_name": "DataGate", "result": "PASS"},
                {"gate_name": "SignalGate", "result": "PASS"},
            ]
        ),
    }
    if steps:
        record.update(steps)
    return record


# ============================================================
# TimelineNode 테스트
# ============================================================
class TestTimelineNode:
    def test_basic_node(self):
        node = TimelineNode(step="step1", label="데이터 수집", status=TimelineNodeStatus.PASS)
        assert node.step == "step1"
        assert node.status == TimelineNodeStatus.PASS

    def test_to_dict_minimal(self):
        node = TimelineNode(step="step1", label="수집", status=TimelineNodeStatus.SKIP)
        d = node.to_dict()
        assert d["step"] == "step1"
        assert d["status"] == "SKIP"
        assert "duration_ms" not in d
        assert "detail" not in d

    def test_to_dict_with_detail(self):
        node = TimelineNode(
            step="step1", label="수집", status=TimelineNodeStatus.PASS, duration_ms=15.5, detail={"keys": ["a"]}
        )
        d = node.to_dict()
        assert d["duration_ms"] == 15.5
        assert d["detail"]["keys"] == ["a"]


# ============================================================
# DecisionTimeline 테스트
# ============================================================
class TestDecisionTimeline:
    def test_completed_steps(self):
        nodes = [
            TimelineNode(step="s1", label="L1", status=TimelineNodeStatus.PASS),
            TimelineNode(step="s2", label="L2", status=TimelineNodeStatus.PASS),
            TimelineNode(step="s3", label="L3", status=TimelineNodeStatus.SKIP),
        ]
        tl = DecisionTimeline(decision_id="d1", timestamp="2026-01-01", status="PARTIAL", nodes=nodes)
        assert tl.completed_steps == 2
        assert tl.total_steps == 3

    def test_to_dict(self):
        nodes = [TimelineNode(step="s1", label="L1", status=TimelineNodeStatus.PASS)]
        tl = DecisionTimeline(decision_id="d1", timestamp="2026-01-01", status="COMPLETE", nodes=nodes)
        d = tl.to_dict()
        assert d["decision_id"] == "d1"
        assert d["completed_steps"] == 1
        assert len(d["nodes"]) == 1


# ============================================================
# GateHeatmapCell 테스트
# ============================================================
class TestGateHeatmapCell:
    def test_basic_cell(self):
        cell = GateHeatmapCell(gate_name="DataGate", decision_id="d1", result="PASS")
        d = cell.to_dict()
        assert d["gate_name"] == "DataGate"
        assert d["result"] == "PASS"
        assert "severity" not in d

    def test_cell_with_severity(self):
        cell = GateHeatmapCell(gate_name="DataGate", decision_id="d1", result="BLOCK", severity="HIGH")
        d = cell.to_dict()
        assert d["severity"] == "HIGH"


# ============================================================
# AggregationBucket 테스트
# ============================================================
class TestAggregationBucket:
    def test_to_dict(self):
        b = AggregationBucket(period="2026-04-05", total=10, passed=7, failed=2, partial=1)
        d = b.to_dict()
        assert d["period"] == "2026-04-05"
        assert d["total"] == 10
        assert d["passed"] == 7


# ============================================================
# VisualizationData 테스트
# ============================================================
class TestVisualizationData:
    def test_to_dict(self):
        vd = VisualizationData(chart_type=ChartType.BAR, title="Test", data=[{"x": 1}], metadata={"k": "v"})
        d = vd.to_dict()
        assert d["chart_type"] == "BAR"
        assert d["title"] == "Test"
        assert len(d["data"]) == 1


# ============================================================
# AuditTrailVisualizer.build_timeline 테스트
# ============================================================
class TestBuildTimeline:
    def test_complete_record_all_pass(self):
        viz = AuditTrailVisualizer()
        record = _make_record(status="COMPLETE")
        tl = viz.build_timeline(record)
        assert tl.completed_steps == 7
        assert tl.status == "COMPLETE"

    def test_partial_record(self):
        viz = AuditTrailVisualizer()
        record = _make_record(status="PARTIAL", steps={"step6_risk_check": None, "step7_execution": None})
        tl = viz.build_timeline(record)
        assert tl.completed_steps == 5
        # Partial → SKIP for missing steps
        skip_nodes = [n for n in tl.nodes if n.status == TimelineNodeStatus.SKIP]
        assert len(skip_nodes) == 2

    def test_pending_record(self):
        viz = AuditTrailVisualizer()
        record = _make_record(
            status="PENDING",
            steps={
                "step3_signals": None,
                "step4_ensemble": None,
                "step5_portfolio": None,
                "step6_risk_check": None,
                "step7_execution": None,
            },
        )
        tl = viz.build_timeline(record)
        pending_nodes = [n for n in tl.nodes if n.status == TimelineNodeStatus.PENDING]
        assert len(pending_nodes) == 5

    def test_risk_block_shows_fail(self):
        viz = AuditTrailVisualizer()
        record = _make_record(steps={"step6_risk_check": {"result": "BLOCK", "blocked": True}})
        tl = viz.build_timeline(record)
        risk_node = next(n for n in tl.nodes if n.step == "step6_risk_check")
        assert risk_node.status == TimelineNodeStatus.FAIL

    def test_timeline_has_7_nodes(self):
        viz = AuditTrailVisualizer()
        record = _make_record()
        tl = viz.build_timeline(record)
        assert tl.total_steps == 7

    def test_timeline_to_dict(self):
        viz = AuditTrailVisualizer()
        record = _make_record()
        d = viz.build_timeline(record).to_dict()
        assert "nodes" in d
        assert "completed_steps" in d
        assert len(d["nodes"]) == 7


# ============================================================
# AuditTrailVisualizer.build_gate_heatmap 테스트
# ============================================================
class TestBuildGateHeatmap:
    def test_heatmap_with_records(self):
        viz = AuditTrailVisualizer()
        records = [
            _make_record(
                decision_id="d1",
                gate_results=[
                    {"gate_name": "DataGate", "result": "PASS"},
                    {"gate_name": "SignalGate", "result": "BLOCK", "severity": "HIGH"},
                ],
            ),
            _make_record(
                decision_id="d2",
                gate_results=[
                    {"gate_name": "DataGate", "result": "PASS"},
                    {"gate_name": "SignalGate", "result": "PASS"},
                ],
            ),
        ]
        vd = viz.build_gate_heatmap(records)
        assert vd.chart_type == ChartType.HEATMAP
        assert len(vd.data) == 4  # 2 records * 2 gates
        assert vd.metadata["total_records"] == 2
        # DataGate: 2 PASS, SignalGate: 1 PASS + 1 BLOCK
        stats = vd.metadata["gate_stats"]
        assert stats["DataGate"]["PASS"] == 2
        assert stats["SignalGate"]["BLOCK"] == 1

    def test_heatmap_empty(self):
        viz = AuditTrailVisualizer()
        vd = viz.build_gate_heatmap([])
        assert vd.data == []
        assert vd.metadata["total_records"] == 0

    def test_heatmap_no_gate_results(self):
        viz = AuditTrailVisualizer()
        records = [_make_record(gate_results=[])]
        vd = viz.build_gate_heatmap(records)
        assert len(vd.data) == 0


# ============================================================
# AuditTrailVisualizer.aggregate_by_day 테스트
# ============================================================
class TestAggregateByDay:
    def test_daily_aggregation(self):
        viz = AuditTrailVisualizer()
        now = datetime.now(timezone.utc)
        records = [
            _make_record(decision_id="d1", status="COMPLETE", timestamp=now),
            _make_record(decision_id="d2", status="COMPLETE", timestamp=now),
            _make_record(decision_id="d3", status="PARTIAL", timestamp=now),
        ]
        vd = viz.aggregate_by_day(records, days=3)
        assert vd.chart_type == ChartType.BAR
        assert len(vd.data) == 3  # 3 days

        today_bucket = next(b for b in vd.data if b["period"] == now.strftime("%Y-%m-%d"))
        assert today_bucket["total"] == 3
        assert today_bucket["passed"] == 2
        assert today_bucket["partial"] == 1

    def test_daily_empty(self):
        viz = AuditTrailVisualizer()
        vd = viz.aggregate_by_day([], days=7)
        assert len(vd.data) == 7
        assert all(b["total"] == 0 for b in vd.data)

    def test_daily_string_timestamp(self):
        viz = AuditTrailVisualizer()
        now = datetime.now(timezone.utc)
        records = [{"decision_id": "d1", "status": "COMPLETE", "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S")}]
        vd = viz.aggregate_by_day(records, days=1)
        today = now.strftime("%Y-%m-%d")
        today_bucket = next(b for b in vd.data if b["period"] == today)
        assert today_bucket["total"] == 1


# ============================================================
# AuditTrailVisualizer.aggregate_by_hour 테스트
# ============================================================
class TestAggregateByHour:
    def test_hourly_aggregation(self):
        viz = AuditTrailVisualizer()
        now = datetime.now(timezone.utc)
        records = [
            _make_record(decision_id="d1", status="COMPLETE", timestamp=now),
            _make_record(decision_id="d2", status="PENDING", timestamp=now),
        ]
        vd = viz.aggregate_by_hour(records, hours=6)
        assert vd.chart_type == ChartType.TREND
        assert len(vd.data) == 6

    def test_hourly_empty(self):
        viz = AuditTrailVisualizer()
        vd = viz.aggregate_by_hour([], hours=24)
        assert len(vd.data) == 24
        assert vd.metadata["total_events"] == 0


# ============================================================
# AuditTrailVisualizer.build_status_summary 테스트
# ============================================================
class TestBuildStatusSummary:
    def test_status_distribution(self):
        viz = AuditTrailVisualizer()
        records = [
            _make_record(status="COMPLETE"),
            _make_record(status="COMPLETE"),
            _make_record(status="PARTIAL"),
            _make_record(status="PENDING"),
        ]
        vd = viz.build_status_summary(records)
        assert vd.chart_type == ChartType.PIE
        assert vd.metadata["total"] == 4

        data_by_status = {d["status"]: d["count"] for d in vd.data}
        assert data_by_status["COMPLETE"] == 2
        assert data_by_status["PARTIAL"] == 1
        assert data_by_status["PENDING"] == 1

    def test_status_empty(self):
        viz = AuditTrailVisualizer()
        vd = viz.build_status_summary([])
        assert vd.data == []
        assert vd.metadata["total"] == 0


# ============================================================
# AuditTrailVisualizer.build_decision_detail 테스트
# ============================================================
class TestBuildDecisionDetail:
    def test_complete_detail(self):
        viz = AuditTrailVisualizer()
        record = _make_record(
            gate_results=[
                {"gate_name": "DataGate", "result": "PASS"},
                {"gate_name": "SignalGate", "result": "BLOCK"},
            ]
        )
        detail = viz.build_decision_detail(record)
        assert detail["decision_id"] == "test-001"
        assert detail["status"] == "COMPLETE"
        assert detail["gate_summary"]["total"] == 2
        assert detail["gate_summary"]["passed"] == 1
        assert detail["gate_summary"]["blocked"] == 1
        assert detail["has_execution"] is True

    def test_detail_no_risk(self):
        viz = AuditTrailVisualizer()
        record = _make_record(steps={"step6_risk_check": None})
        detail = viz.build_decision_detail(record)
        assert detail["risk_summary"] is None

    def test_detail_risk_summary(self):
        viz = AuditTrailVisualizer()
        record = _make_record(steps={"step6_risk_check": {"result": "BLOCK", "blocked": True, "daily_loss": True}})
        detail = viz.build_decision_detail(record)
        assert detail["risk_summary"]["result"] == "BLOCK"
        assert detail["risk_summary"]["blocked"] is True


# ============================================================
# AuditTrailVisualizer.generate_dashboard_data 테스트
# ============================================================
class TestGenerateDashboardData:
    def test_full_dashboard(self):
        viz = AuditTrailVisualizer()
        now = datetime.now(timezone.utc)
        records = [
            _make_record(
                decision_id=f"d{i}", timestamp=now - timedelta(hours=i), status="COMPLETE" if i % 2 == 0 else "PARTIAL"
            )
            for i in range(5)
        ]
        dashboard = viz.generate_dashboard_data(records, days=3)

        assert "status_summary" in dashboard
        assert "daily_trend" in dashboard
        assert "hourly_trend" in dashboard
        assert "gate_heatmap" in dashboard
        assert dashboard["total_decisions"] == 5
        assert len(dashboard["recent_timelines"]) == 5

    def test_dashboard_empty(self):
        viz = AuditTrailVisualizer()
        dashboard = viz.generate_dashboard_data([])
        assert dashboard["total_decisions"] == 0
        assert len(dashboard["recent_timelines"]) == 0

    def test_dashboard_limits_recent_to_10(self):
        viz = AuditTrailVisualizer()
        now = datetime.now(timezone.utc)
        records = [_make_record(decision_id=f"d{i}", timestamp=now - timedelta(minutes=i)) for i in range(20)]
        dashboard = viz.generate_dashboard_data(records)
        assert len(dashboard["recent_timelines"]) == 10
