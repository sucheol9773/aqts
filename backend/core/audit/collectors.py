"""
Step collectors for capturing data at each stage of the 7-step decision chain
"""

from typing import Any, Dict, List
from datetime import datetime


class InputSnapshotCollector:
    """Collects raw market data, news data, and financial data from Step 1."""

    def collect(
        self,
        market_data: List[Dict[str, Any]],
        news_data: List[Dict[str, Any]],
        financial_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Collect and summarize input data.

        Args:
            market_data: List of market data records
            news_data: List of news data records
            financial_data: List of financial data records

        Returns:
            Dict with summarized input snapshot
        """
        return {
            "market_data_count": len(market_data),
            "market_data_sample": market_data[:3] if market_data else [],
            "news_data_count": len(news_data),
            "news_data_sample": news_data[:3] if news_data else [],
            "financial_data_count": len(financial_data),
            "financial_data_sample": financial_data[:3] if financial_data else [],
            "collected_at": datetime.utcnow().isoformat(),
        }


class FeatureCollector:
    """Collects factor scores and technical indicators from Step 2."""

    def collect(self, feature_vectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Collect and summarize features.

        Args:
            feature_vectors: List of feature vectors with scores

        Returns:
            Dict with feature summary including factor scores and technical indicators
        """
        if not feature_vectors:
            return {
                "feature_count": 0,
                "feature_summary": {},
                "collected_at": datetime.utcnow().isoformat(),
            }

        # Aggregate feature statistics
        feature_summary = {}
        for feature_vector in feature_vectors:
            for key, value in feature_vector.items():
                if key not in feature_summary:
                    feature_summary[key] = {
                        "values": [],
                        "min": None,
                        "max": None,
                        "avg": None,
                    }
                if isinstance(value, (int, float)):
                    feature_summary[key]["values"].append(value)

        # Calculate statistics for numeric features
        for key in feature_summary:
            if feature_summary[key]["values"]:
                values = feature_summary[key]["values"]
                feature_summary[key]["min"] = min(values)
                feature_summary[key]["max"] = max(values)
                feature_summary[key]["avg"] = sum(values) / len(values)
                del feature_summary[key]["values"]  # Don't store raw values

        return {
            "feature_count": len(feature_vectors),
            "feature_summary": feature_summary,
            "collected_at": datetime.utcnow().isoformat(),
        }


class SignalCollector:
    """Collects individual strategy signals from Step 3."""

    def collect(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collect individual strategy signals.

        Args:
            signals: List of signal dicts (e.g., from different strategies)

        Returns:
            List of signal dicts with metadata
        """
        collected_signals = []
        for signal in signals:
            collected_signal = {
                "signal_id": signal.get("signal_id", f"signal_{len(collected_signals)}"),
                "strategy_name": signal.get("strategy_name", "unknown"),
                "signal_type": signal.get("signal_type", "unknown"),  # BUY, SELL, HOLD
                "confidence": signal.get("confidence", 0.0),
                "strength": signal.get("strength", 0.0),
                "reasoning": signal.get("reasoning", ""),
            }
            collected_signals.append(collected_signal)

        return collected_signals


class RiskCheckCollector:
    """Collects TradingGuard 7-layer risk check results from Step 6."""

    def collect(self, risk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Collect and summarize TradingGuard risk check results.

        Args:
            risk_results: List of risk check results from TradingGuard

        Returns:
            Dict with risk check summary
        """
        if not risk_results:
            return {
                "risk_check_count": 0,
                "passed_checks": 0,
                "failed_checks": 0,
                "risk_summary": [],
                "collected_at": datetime.utcnow().isoformat(),
            }

        passed = 0
        failed = 0
        risk_summary = []

        for result in risk_results:
            check_result = {
                "layer_name": result.get("layer_name", "unknown"),
                "status": result.get("status", "unknown"),  # PASS or FAIL
                "severity": result.get("severity", "INFO"),
                "message": result.get("message", ""),
            }
            risk_summary.append(check_result)

            if check_result["status"] == "PASS":
                passed += 1
            else:
                failed += 1

        return {
            "risk_check_count": len(risk_results),
            "passed_checks": passed,
            "failed_checks": failed,
            "risk_summary": risk_summary,
            "collected_at": datetime.utcnow().isoformat(),
        }


class GateResultCollector:
    """Collects 9-gate pass/block results from Stage 2-B."""

    def collect(self, gate_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collect 9-gate results.

        Args:
            gate_results: List of gate result dicts from Stage 2-B

        Returns:
            List of gate result dicts with standardized format
        """
        collected_gates = []
        for gate_result in gate_results:
            collected_gate = {
                "gate_id": gate_result.get("gate_id", "unknown"),
                "decision": gate_result.get("decision", "UNKNOWN"),  # PASS or BLOCK
                "reason": gate_result.get("reason", ""),
                "severity": gate_result.get("severity", "INFO"),
                "timestamp": gate_result.get("timestamp", datetime.utcnow().isoformat()),
                "context": gate_result.get("context", {}),
            }
            collected_gates.append(collected_gate)

        return collected_gates
