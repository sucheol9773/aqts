# YAML-Based Hyperparameter Configuration Management System

## Overview

This system implements centralized YAML-based configuration management for AQTS hyperparameters, replacing hardcoded values in code and enabling easy hyperparameter optimization integration.

## Architecture

### Components

1. **config/ensemble_config.yaml** - Central configuration file
2. **config/ensemble_config_loader.py** - Configuration loader module
3. **core/strategy_ensemble/dynamic_ensemble.py** - Updated to use YAML config
4. **tests/test_ensemble_config.py** - Comprehensive test suite

### Parameter Hierarchy (Priority Order)

```
Function Parameters > YAML Config > Code Defaults
```

## Files Created/Modified

### 1. config/ensemble_config.yaml

Central configuration file with three main sections:

```yaml
metadata:
  last_optimized: <date>        # Last optimization date
  oos_sharpe_baseline: <value>  # Reference OOS Sharpe ratio
  version: "1.0"

ensemble:
  adx_threshold: 25             # ADX trend threshold
  vol_pct_threshold: 0.75       # Volatility percentile threshold
  perf_window: 60               # Performance measurement window (business days)
  softmax_temperature: 5.0      # Softmax temperature (higher = more uniform)
  perf_blend: 0.3               # Performance blending ratio
  target_vol: 0.25              # Target annualized volatility

regime_weights:
  TRENDING_UP:
    TF: 0.55                    # Trend Following weight
    MR: 0.15                    # Mean Reversion weight
    RP: 0.30                    # Risk Parity weight
  # ... (similar for other regimes)

risk:
  stop_loss_atr_multiplier: 2.0
  trailing_stop_atr_multiplier: 3.0
  max_drawdown_limit: 0.20
  drawdown_cooldown_days: 20
  dd_cushion_start: 0.08
  dd_cushion_floor: 0.25
```

### 2. config/ensemble_config_loader.py

Module providing configuration loading, saving, and validation:

#### Key Functions

**load_ensemble_config() -> dict**
- Loads YAML config if it exists, otherwise returns code defaults
- Returns dict with keys: "ensemble", "regime_weights", "risk"
- Falls back to code defaults if file doesn't exist
- Handles load errors gracefully

**save_ensemble_config(config: dict, metadata: dict = None, config_path: Path = None) -> Path**
- Validates configuration before saving
- Saves to YAML file with formatting
- Optionally includes metadata (last_optimized, oos_sharpe_baseline)
- Raises ValueError if validation fails

**validate_ensemble_config(config: dict) -> list[str]**
- Validates all parameter ranges and constraints
- Checks regime weights sum to exactly 1.0
- Returns list of error strings (empty list = valid)

**apply_hyperopt_results(result_path: str, config_path: Path = None) -> dict**
- Loads hyperopt JSON output: `{"best_params": {...}, "best_value": ...}`
- Converts flat params to structured config
- Automatically handles regime weight normalization (RP = 1.0 - TF - MR)
- Saves updated config to YAML with metadata

**_convert_hyperopt_params_to_config(params: dict) -> dict**
- Internal helper to convert flat hyperopt params to config structure
- Handles regime weight mapping and normalization

### 3. core/strategy_ensemble/dynamic_ensemble.py

Updated to use YAML configuration:

#### Changes Made

**DynamicEnsembleService.__init__()**
```python
def __init__(self, params: dict | None = None):
    from config.ensemble_config_loader import load_ensemble_config

    # Load YAML config
    yaml_config = load_ensemble_config()

    # Merge: code defaults < YAML < function params
    base = {**DEFAULT_PARAMS}
    if yaml_config.get("ensemble"):
        base.update(yaml_config["ensemble"])
    if params:
        base.update(params)
    self._params = base

    # Load regime weights from YAML
    self._regime_weights = dict(REGIME_WEIGHTS)
    if yaml_config.get("regime_weights"):
        for regime_str, weights in yaml_config["regime_weights"].items():
            try:
                regime = DynamicRegime(regime_str)
                self._regime_weights[regime] = weights
            except ValueError:
                pass
```

**_assign_regime_weights() (static method)**
- Now accepts `regime_weights` parameter
- Uses provided weights or falls back to defaults
- Enables per-instance weight customization

## Usage Examples

### Basic Usage

```python
from core.strategy_ensemble.dynamic_ensemble import DynamicEnsembleService

# Load config from YAML (or defaults if YAML doesn't exist)
service = DynamicEnsembleService()
result = service.compute(ohlcv, mr_signal, tf_signal, rp_signal)
```

### Override with Function Parameters

```python
# Function params override YAML
service = DynamicEnsembleService(params={
    "adx_threshold": 28,
    "target_vol": 0.26
})
```

### Save Configuration

```python
from config.ensemble_config_loader import save_ensemble_config

config = {
    "ensemble": {"adx_threshold": 26, ...},
    "regime_weights": {...},
    "risk": {...}
}

metadata = {
    "last_optimized": "2026-04-06",
    "oos_sharpe_baseline": 0.95
}

save_ensemble_config(config, metadata=metadata)
```

### Apply Hyperopt Results

```python
from config.ensemble_config_loader import apply_hyperopt_results

# Load JSON from Optuna trial
config = apply_hyperopt_results("hyperopt_result.json")
```

### Validate Configuration

```python
from config.ensemble_config_loader import validate_ensemble_config

errors = validate_ensemble_config(config)
if errors:
    print("Validation failed:")
    for error in errors:
        print(f"  - {error}")
```

## Validation Rules

### Ensemble Parameters

- **adx_threshold**: [10, 50]
- **vol_pct_threshold**: [0.0, 1.0]
- **perf_window**: int >= 5
- **softmax_temperature**: [0.1, 50.0]
- **perf_blend**: [0.0, 1.0]
- **target_vol**: [0.01, 1.0]

### Regime Weights

- Valid regimes: TRENDING_UP, TRENDING_DOWN, HIGH_VOLATILITY, SIDEWAYS
- Each regime must have TF, MR, RP keys
- Sum of TF + MR + RP must equal 1.0 (within 1e-6 tolerance)
- Each weight must be in [0.0, 1.0]

### Risk Parameters

- **stop_loss_atr_multiplier**: [0.5, 10.0]
- **trailing_stop_atr_multiplier**: [0.5, 10.0]
- **max_drawdown_limit**: [0.05, 0.5]
- **drawdown_cooldown_days**: int >= 1
- **dd_cushion_start**: [0.01, 0.3]
- **dd_cushion_floor**: [0.1, 0.9]

## Integration with Hyperopt

### Workflow

1. Run hyperopt optimization and save results to JSON
2. Call `apply_hyperopt_results("result.json")`
3. Configuration is automatically validated and saved to YAML
4. Next DynamicEnsembleService instance loads new configuration

### JSON Format Expected

```json
{
  "best_params": {
    "adx_threshold": 28.5,
    "vol_pct_threshold": 0.78,
    "w_trending_up_tf": 0.52,
    "w_trending_up_mr": 0.16,
    "stop_loss_atr_multiplier": 2.1,
    ...
  },
  "best_value": 0.98,
  "best_trial": 42
}
```

## Testing

Comprehensive test suite in `tests/test_ensemble_config.py`:

- **TestLoadEnsembleConfig**: YAML loading and fallback behavior
- **TestValidateEnsembleConfig**: Validation rules (valid/invalid cases)
- **TestSaveAndReloadConfig**: Save/reload consistency
- **TestApplyHyperoptResults**: Hyperopt JSON integration
- **TestDynamicEnsembleIntegration**: End-to-end integration tests

Run tests:
```bash
cd backend && python -m pytest tests/test_ensemble_config.py -v
```

All 23 tests pass, covering:
- Default value fallback
- YAML loading and merging
- Configuration validation
- Save/reload consistency
- Hyperopt result application
- DynamicEnsembleService integration

## Code Quality

All code follows project standards:
- **Black formatting**: ✅
- **Ruff linting**: ✅
- **Type hints**: ✅ (uses `|` union syntax for Python 3.10+)
- **Docstrings**: ✅ (Korean + examples)

## Design Principles

### 1. Defensive Programming
- Code defaults serve as ultimate fallback
- YAML validation before saving
- Graceful error handling with logging

### 2. Consistency
- Regime weights always sum to 1.0
- Parameter ranges enforced at validation
- yaml + code defaults kept in sync

### 3. Flexibility
- Function parameters override YAML
- YAML overrides code defaults
- Easy to add new parameters (extend YAML + validation)

### 4. Maintainability
- Single source of truth: YAML file
- No scattered hardcoded values
- Clear separation: load → validate → use

## Migration Guide

### For Existing Code

1. Code defaults remain unchanged - no breaking changes
2. DynamicEnsembleService works as before if no YAML exists
3. To use YAML config:
   - Create/modify `config/ensemble_config.yaml`
   - DynamicEnsembleService automatically loads it

### For Hyperopt Integration

```python
# In run_hyperopt.py or hyperopt script
from config.ensemble_config_loader import apply_hyperopt_results

# After optimization completes
best_trial = study.best_trial
output_path = "hyperopt_result.json"

# Save JSON (existing code)
# ... save best_trial to JSON ...

# Apply to YAML (new)
apply_hyperopt_results(output_path)
```

## Future Enhancements

1. **Multi-profile support**: Different YAML files for different scenarios
2. **Version control**: Track config changes with git
3. **A/B testing**: Compare multiple config versions
4. **Auto-reload**: Watch YAML file and reload on changes
5. **UI dashboard**: Edit config through web interface

## File Locations

```
/sessions/practical-eager-davinci/mnt/aqts/backend/
├── config/
│   ├── ensemble_config.yaml              # ← Central config file
│   ├── ensemble_config_loader.py         # ← Loader module
│   └── operational_thresholds.yaml       # ← Existing operational config
├── core/strategy_ensemble/
│   └── dynamic_ensemble.py               # ← Updated to use YAML
└── tests/
    └── test_ensemble_config.py           # ← Test suite
```

## References

- SearchSpace: `core/hyperopt/search_space.py` (defines optimizable params)
- DynamicEnsembleService: `core/strategy_ensemble/dynamic_ensemble.py` (uses config)
- BacktestConfig: Uses similar config pattern (future integration)
