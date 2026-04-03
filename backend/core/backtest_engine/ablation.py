"""
Stage 6 Performance Validation: Ablation Study

Measure incremental contribution of strategy layers:
- Base returns (e.g., Quant baseline)
- +Layer2 (e.g., +Sentiment)
- +Layer3 (e.g., +Risk Control)

Calculates delta metrics between layers to isolate contribution.
"""

import numpy as np
from typing import List, Dict, Optional, Union
from .metrics_calculator import MetricsCalculator


class AblationStudy:
    """Track cumulative strategy layers and measure contribution of each."""

    def __init__(
        self,
        base_returns: Union[List[float], np.ndarray],
        labels: Optional[List[str]] = None,
    ):
        """
        Initialize ablation study with base returns.

        Args:
            base_returns: Baseline daily fractional returns
            labels: Optional list of labels for each return (for segmentation)
        """
        self.base_returns = np.asarray(base_returns)
        self.labels = labels
        self.layers: Dict[str, np.ndarray] = {"Base": self.base_returns.copy()}
        self.metrics_cache: Dict[str, Dict[str, float]] = {}

    def add_layer(self, name: str, returns: Union[List[float], np.ndarray]) -> None:
        """
        Add a strategy layer (typically cumulative with previous layers).

        Example:
            study.add_layer("Quant", quant_returns)
            study.add_layer("+Sentiment", quant_sentiment_returns)
            study.add_layer("+RiskControl", final_returns)

        Args:
            name: Layer name
            returns: Daily fractional returns for this layer configuration
        """
        returns = np.asarray(returns)
        if len(returns) != len(self.base_returns):
            raise ValueError(
                f"Layer {name} has {len(returns)} returns, "
                f"expected {len(self.base_returns)}"
            )
        self.layers[name] = returns
        # Clear cache since we added a new layer
        self.metrics_cache.clear()

    def run(self) -> Dict[str, Dict[str, float]]:
        """
        Calculate metrics for all layers.

        Returns:
            Dict {layer_name: metrics_dict}
            where metrics_dict has: cagr, max_drawdown, sharpe_ratio, etc.
        """
        results = {}

        for layer_name, layer_returns in self.layers.items():
            metrics = MetricsCalculator.calculate_all(layer_returns)
            results[layer_name] = metrics
            self.metrics_cache[layer_name] = metrics

        return results

    def contribution(
        self, layer_a: str, layer_b: str
    ) -> Dict[str, Optional[float]]:
        """
        Calculate delta metrics between two layers (layer_b - layer_a).

        Useful for isolating the contribution of a new layer.

        Example:
            delta = study.contribution("Base", "+Sentiment")
            # Shows how much +Sentiment improves over Base

        Args:
            layer_a: Reference layer name
            layer_b: Comparison layer name

        Returns:
            Dict with delta for each metric (layer_b metric - layer_a metric)
        """
        if layer_a not in self.layers:
            raise ValueError(f"Layer {layer_a} not found")
        if layer_b not in self.layers:
            raise ValueError(f"Layer {layer_b} not found")

        # Ensure metrics are calculated
        if not self.metrics_cache:
            self.run()

        metrics_a = self.metrics_cache.get(layer_a)
        metrics_b = self.metrics_cache.get(layer_b)

        if metrics_a is None or metrics_b is None:
            # Calculate if not in cache
            metrics_a = MetricsCalculator.calculate_all(self.layers[layer_a])
            metrics_b = MetricsCalculator.calculate_all(self.layers[layer_b])

        # Calculate deltas
        delta = {}
        for metric_key in metrics_a.keys():
            val_a = metrics_a[metric_key]
            val_b = metrics_b[metric_key]

            if val_a is None or val_b is None:
                delta[metric_key] = None
            else:
                delta[metric_key] = val_b - val_a

        return delta

    def layer_names(self) -> List[str]:
        """Return list of layer names in order added."""
        return list(self.layers.keys())

    def remove_layer(self, name: str) -> bool:
        """
        Remove a layer (except Base).

        Args:
            name: Layer name

        Returns:
            True if removed, False if not found or is Base
        """
        if name == "Base":
            return False
        if name in self.layers:
            del self.layers[name]
            self.metrics_cache.clear()
            return True
        return False
