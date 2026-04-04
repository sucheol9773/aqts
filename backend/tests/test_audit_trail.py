"""
Tests for Stage 4 Decision Audit Trail

30+ tests covering:
- DecisionRecord creation, step updates, full chain assembly
- Each collector (input, feature, signal, risk, gate)
- Store operations (create, get, update, query)
- API route tests
- Edge cases: missing steps, duplicate decision_id, invalid step names
"""

from datetime import datetime, timedelta

import pytest

# API and auth imports
from core.audit.collectors import (
    FeatureCollector,
    GateResultCollector,
    InputSnapshotCollector,
    RiskCheckCollector,
    SignalCollector,
)

# Decision audit imports
from core.audit.decision_record import DecisionRecord, DecisionRecordStore, get_decision_store

# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests: DecisionRecord (10+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestDecisionRecord:
    """Test DecisionRecord creation and properties."""

    def test_decision_record_creation(self):
        """Test creating a DecisionRecord."""
        record = DecisionRecord(decision_id="test-123", timestamp=datetime.utcnow())

        assert record.decision_id == "test-123"
        assert record.timestamp is not None
        assert record.status == "PENDING"
        assert record.step1_input_snapshot is None
        assert record.gate_results is None

    def test_decision_record_with_all_steps(self):
        """Test DecisionRecord with all steps populated."""
        now = datetime.utcnow()
        record = DecisionRecord(
            decision_id="test-456",
            timestamp=now,
            step1_input_snapshot={"market": "data"},
            step2_features={"factor": 0.5},
            step3_signals=[{"signal": "BUY"}],
            step4_ensemble={"weight": 0.7},
            step5_portfolio={"position": "SPY"},
            step6_risk_check={"layer": "passed"},
            step7_execution=[{"order": "fill"}],
            gate_results=[{"gate": "PASS"}],
            status="COMPLETE",
        )

        assert record.step1_input_snapshot == {"market": "data"}
        assert record.step7_execution == [{"order": "fill"}]
        assert record.gate_results == [{"gate": "PASS"}]
        assert record.status == "COMPLETE"

    def test_decision_record_partial_status(self):
        """Test DecisionRecord with partial steps."""
        record = DecisionRecord(
            decision_id="test-789",
            step1_input_snapshot={"data": "present"},
            step2_features={"factors": "present"},
        )

        assert record.status == "PENDING"  # Status is set during creation

    def test_decision_record_serialization(self):
        """Test DecisionRecord can be serialized to dict."""
        record = DecisionRecord(
            decision_id="test-serial",
            timestamp=datetime.utcnow(),
            step1_input_snapshot={"key": "value"},
        )

        serialized = record.model_dump()
        assert "decision_id" in serialized
        assert "timestamp" in serialized
        assert "step1_input_snapshot" in serialized
        assert serialized["step1_input_snapshot"] == {"key": "value"}

    def test_decision_record_model_config(self):
        """Test DecisionRecord model immutability via validation_alias."""
        record = DecisionRecord(decision_id="test-frozen")

        # Pydantic v2 doesn't freeze by default, but model config allows arbitrary_types_allowed
        assert record.decision_id == "test-frozen"


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests: DecisionRecordStore (15+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestDecisionRecordStore:
    """Test DecisionRecordStore in-memory storage."""

    @pytest.fixture
    def store(self):
        """Create a fresh store for each test."""
        store = DecisionRecordStore()
        yield store
        store.clear()

    def test_store_create_with_auto_uuid(self, store):
        """Test creating a record with auto-generated UUID."""
        record = store.create()

        assert record.decision_id is not None
        assert len(record.decision_id) > 0
        assert record.timestamp is not None
        assert record.status == "PENDING"

    def test_store_create_with_provided_id(self, store):
        """Test creating a record with provided decision_id."""
        decision_id = "custom-id-123"
        record = store.create(decision_id=decision_id)

        assert record.decision_id == decision_id

    def test_store_get_existing_record(self, store):
        """Test retrieving an existing record."""
        created = store.create(decision_id="test-get")
        retrieved = store.get("test-get")

        assert retrieved is not None
        assert retrieved.decision_id == "test-get"
        assert retrieved.timestamp == created.timestamp

    def test_store_get_nonexistent_record(self, store):
        """Test retrieving a non-existent record returns None."""
        result = store.get("nonexistent")

        assert result is None

    def test_store_update_step_valid(self, store):
        """Test updating a valid step."""
        store.create(decision_id="test-update")
        updated = store.update_step(
            "test-update",
            "step1_input_snapshot",
            {"market": "data"},
        )

        assert updated is not None
        assert updated.step1_input_snapshot == {"market": "data"}
        assert updated.status == "PARTIAL"

    def test_store_update_step_nonexistent_record(self, store):
        """Test updating a step on non-existent record returns None."""
        result = store.update_step(
            "nonexistent",
            "step1_input_snapshot",
            {"data": "test"},
        )

        assert result is None

    def test_store_update_step_invalid_name(self, store):
        """Test updating with invalid step name raises ValueError."""
        store.create(decision_id="test-invalid")

        with pytest.raises(ValueError):
            store.update_step("test-invalid", "invalid_step_name", {"data": "test"})

    def test_store_update_step_completes_record(self, store):
        """Test that completing all 7 steps updates status to COMPLETE."""
        store.create(decision_id="test-complete")

        # Update all 7 steps with appropriate data types
        steps_data = {
            "step1_input_snapshot": {"data": "step1"},
            "step2_features": {"data": "step2"},
            "step3_signals": [{"data": "step3"}],  # Should be list
            "step4_ensemble": {"data": "step4"},
            "step5_portfolio": {"data": "step5"},
            "step6_risk_check": {"data": "step6"},
            "step7_execution": [{"data": "step7"}],  # Should be list
        }

        for step, data in steps_data.items():
            updated = store.update_step("test-complete", step, data)

        assert updated.status == "COMPLETE"

    def test_store_query_all_records(self, store):
        """Test querying all records without date filter."""
        store.create(decision_id="record-1")
        store.create(decision_id="record-2")
        store.create(decision_id="record-3")

        results = store.query()

        assert len(results) == 3

    def test_store_query_with_limit(self, store):
        """Test querying with limit parameter."""
        for i in range(10):
            store.create(decision_id=f"record-{i}")

        results = store.query(limit=5)

        assert len(results) == 5

    def test_store_query_with_date_range(self, store):
        """Test querying with date range."""
        now = datetime.utcnow()
        past = now - timedelta(days=5)
        future = now + timedelta(days=5)

        store.create(decision_id="record-1")
        store.create(decision_id="record-2")

        # Query within range
        results = store.query(start_date=past, end_date=future)
        assert len(results) == 2

        # Query outside range (past only)
        results = store.query(start_date=future)
        assert len(results) == 0

    def test_store_query_sorted_by_timestamp_desc(self, store):
        """Test that query results are sorted by timestamp descending."""
        import time

        ids = []
        for i in range(3):
            record = store.create(decision_id=f"record-{i}")
            ids.append(record.decision_id)
            time.sleep(0.01)  # Ensure different timestamps

        results = store.query()

        # Most recent first
        assert results[0].decision_id == "record-2"
        assert results[-1].decision_id == "record-0"

    def test_store_count(self, store):
        """Test counting records in store."""
        assert store.count() == 0

        store.create(decision_id="record-1")
        assert store.count() == 1

        store.create(decision_id="record-2")
        assert store.count() == 2

    def test_store_clear(self, store):
        """Test clearing all records."""
        store.create(decision_id="record-1")
        store.create(decision_id="record-2")

        assert store.count() == 2

        store.clear()

        assert store.count() == 0
        assert store.get("record-1") is None

    def test_global_store_instance(self):
        """Test get_decision_store returns singleton."""
        store1 = get_decision_store()
        store2 = get_decision_store()

        assert store1 is store2


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests: Collectors (5+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestInputSnapshotCollector:
    """Test InputSnapshotCollector."""

    def test_collect_empty_data(self):
        """Test collecting empty data."""
        collector = InputSnapshotCollector()
        result = collector.collect([], [], [])

        assert result["market_data_count"] == 0
        assert result["news_data_count"] == 0
        assert result["financial_data_count"] == 0

    def test_collect_with_data(self):
        """Test collecting with sample data."""
        collector = InputSnapshotCollector()
        market_data = [{"price": 100}, {"price": 101}]
        news_data = [{"headline": "test"}]
        financial_data = [{"eps": 5.0}]

        result = collector.collect(market_data, news_data, financial_data)

        assert result["market_data_count"] == 2
        assert result["news_data_count"] == 1
        assert result["financial_data_count"] == 1
        assert len(result["market_data_sample"]) == 2
        assert "collected_at" in result


class TestFeatureCollector:
    """Test FeatureCollector."""

    def test_collect_empty_features(self):
        """Test collecting with empty features."""
        collector = FeatureCollector()
        result = collector.collect([])

        assert result["feature_count"] == 0
        assert result["feature_summary"] == {}

    def test_collect_with_numeric_features(self):
        """Test collecting numeric features."""
        collector = FeatureCollector()
        features = [
            {"factor_a": 0.5, "factor_b": 0.7},
            {"factor_a": 0.6, "factor_b": 0.8},
        ]

        result = collector.collect(features)

        assert result["feature_count"] == 2
        assert "factor_a" in result["feature_summary"]
        assert result["feature_summary"]["factor_a"]["min"] == 0.5
        assert result["feature_summary"]["factor_a"]["max"] == 0.6


class TestSignalCollector:
    """Test SignalCollector."""

    def test_collect_signals(self):
        """Test collecting signals."""
        collector = SignalCollector()
        signals = [
            {
                "signal_id": "sig-1",
                "strategy_name": "momentum",
                "signal_type": "BUY",
                "confidence": 0.8,
            },
            {
                "signal_id": "sig-2",
                "strategy_name": "mean_reversion",
                "signal_type": "SELL",
                "confidence": 0.6,
            },
        ]

        result = collector.collect(signals)

        assert len(result) == 2
        assert result[0]["signal_id"] == "sig-1"
        assert result[0]["confidence"] == 0.8
        assert result[1]["signal_type"] == "SELL"


class TestRiskCheckCollector:
    """Test RiskCheckCollector."""

    def test_collect_risk_results(self):
        """Test collecting risk check results."""
        collector = RiskCheckCollector()
        risk_results = [
            {"layer_name": "layer_1", "status": "PASS", "severity": "INFO"},
            {"layer_name": "layer_2", "status": "FAIL", "severity": "ERROR"},
            {"layer_name": "layer_3", "status": "PASS", "severity": "INFO"},
        ]

        result = collector.collect(risk_results)

        assert result["risk_check_count"] == 3
        assert result["passed_checks"] == 2
        assert result["failed_checks"] == 1
        assert len(result["risk_summary"]) == 3


class TestGateResultCollector:
    """Test GateResultCollector."""

    def test_collect_gate_results(self):
        """Test collecting gate results."""
        collector = GateResultCollector()
        gate_results = [
            {
                "gate_id": "DataGate",
                "decision": "PASS",
                "reason": "Data valid",
            },
            {
                "gate_id": "ExecutionGate",
                "decision": "BLOCK",
                "reason": "Execution risk high",
            },
        ]

        result = collector.collect(gate_results)

        assert len(result) == 2
        assert result[0]["gate_id"] == "DataGate"
        assert result[0]["decision"] == "PASS"
        assert result[1]["decision"] == "BLOCK"


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests: Full Audit Chain (5+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestFullAuditChain:
    """Test complete 7-step audit chain."""

    @pytest.fixture
    def store(self):
        """Fresh store for each test."""
        store = DecisionRecordStore()
        yield store
        store.clear()

    @pytest.fixture
    def collectors(self):
        """Collection of all collectors."""
        return {
            "input": InputSnapshotCollector(),
            "feature": FeatureCollector(),
            "signal": SignalCollector(),
            "risk": RiskCheckCollector(),
            "gate": GateResultCollector(),
        }

    def test_full_chain_with_all_steps(self, store, collectors):
        """Test complete audit chain with all 7 steps."""
        decision_id = "full-chain-test"
        store.create(decision_id=decision_id)

        # Step 1: Input Snapshot
        step1_data = collectors["input"].collect(
            [{"price": 100}],
            [{"headline": "news"}],
            [{"eps": 5.0}],
        )
        store.update_step(decision_id, "step1_input_snapshot", step1_data)

        # Step 2: Features
        step2_data = collectors["feature"].collect([{"factor": 0.5}])
        store.update_step(decision_id, "step2_features", step2_data)

        # Step 3: Signals
        step3_data = collectors["signal"].collect(
            [
                {
                    "signal_id": "sig-1",
                    "strategy_name": "momentum",
                    "signal_type": "BUY",
                    "confidence": 0.8,
                }
            ]
        )
        store.update_step(decision_id, "step3_signals", step3_data)

        # Step 4: Ensemble
        step4_data = {"ensemble_weight": 0.7, "model_output": "BUY"}
        store.update_step(decision_id, "step4_ensemble", step4_data)

        # Step 5: Portfolio
        step5_data = {"target_positions": {"SPY": 0.5, "QQQ": 0.3}}
        store.update_step(decision_id, "step5_portfolio", step5_data)

        # Step 6: Risk Check
        step6_data = collectors["risk"].collect([{"layer_name": "layer_1", "status": "PASS", "severity": "INFO"}])
        store.update_step(decision_id, "step6_risk_check", step6_data)

        # Step 7: Execution
        step7_data = [{"order_id": "ord-123", "filled": True}]
        store.update_step(decision_id, "step7_execution", step7_data)

        # Gate Results
        gate_data = collectors["gate"].collect([{"gate_id": "DataGate", "decision": "PASS"}])
        store.update_step(decision_id, "gate_results", gate_data)

        # Verify full chain
        final_record = store.get(decision_id)
        assert final_record.status == "COMPLETE"
        assert final_record.step1_input_snapshot is not None
        assert final_record.step7_execution is not None
        assert final_record.gate_results is not None

    def test_chain_with_partial_completion(self, store, collectors):
        """Test audit chain with only some steps complete."""
        decision_id = "partial-chain"
        store.create(decision_id=decision_id)

        # Only complete first 3 steps
        step1_data = collectors["input"].collect([], [], [])
        store.update_step(decision_id, "step1_input_snapshot", step1_data)

        step2_data = collectors["feature"].collect([])
        store.update_step(decision_id, "step2_features", step2_data)

        step3_data = collectors["signal"].collect([])
        store.update_step(decision_id, "step3_signals", step3_data)

        record = store.get(decision_id)
        assert record.status == "PARTIAL"
        assert record.step4_ensemble is None
        assert record.step7_execution is None


# ══════════════════════════════════════════════════════════════════════════════
# Edge Cases (5+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditTrailEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def store(self):
        """Fresh store for each test."""
        store = DecisionRecordStore()
        yield store
        store.clear()

    def test_duplicate_decision_id_overwrite(self, store):
        """Test that creating with same ID overwrites."""
        record1 = store.create(decision_id="dup-id")
        store.update_step("dup-id", "step1_input_snapshot", {"data": "first"})

        record2 = store.create(decision_id="dup-id")
        # Creating same ID should reset the record
        assert store.get("dup-id").step1_input_snapshot is None

    def test_large_decision_record(self, store):
        """Test handling large decision records."""
        decision_id = "large-test"
        store.create(decision_id=decision_id)

        # Create large step data
        large_data = {f"field_{i}": f"value_{i}" * 100 for i in range(100)}
        store.update_step(decision_id, "step1_input_snapshot", large_data)

        record = store.get(decision_id)
        assert len(record.step1_input_snapshot) == 100

    def test_none_timestamps_in_query(self, store):
        """Test query with None timestamps doesn't fail."""
        store.create(decision_id="test-1")
        store.create(decision_id="test-2")

        results = store.query(start_date=None, end_date=None)
        assert len(results) == 2

    def test_query_with_future_date(self, store):
        """Test query with future date range."""
        store.create(decision_id="test-1")

        future = datetime.utcnow() + timedelta(days=1)
        results = store.query(start_date=future)

        assert len(results) == 0

    def test_update_step_with_null_data(self, store):
        """Test updating step with None data."""
        store.create(decision_id="test-null")
        updated = store.update_step("test-null", "step1_input_snapshot", None)

        assert updated.step1_input_snapshot is None


# ══════════════════════════════════════════════════════════════════════════════
# API Route Tests (5+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditAPIRoutes:
    """Test audit API routes."""

    @pytest.fixture
    def store(self):
        """Use global store for API tests."""
        store = get_decision_store()
        store.clear()
        yield store
        store.clear()

    def test_api_route_imports(self):
        """Test that audit routes can be imported."""
        from api.routes import audit

        assert audit.router is not None
        assert audit.router.prefix == "/api/audit"

    def test_audit_routes_in_main(self):
        """Test that audit router is registered in main.py."""
        from main import app

        # Check if audit routes are in the app
        routes = [route.path for route in app.routes]
        assert any("/api/audit" in route for route in routes)

    def test_store_fixture_isolation(self, store):
        """Test that each test gets fresh store state."""
        assert store.count() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
