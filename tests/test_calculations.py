"""Core calculation tests – no server needed."""

import sys
import os

# Ensure project root in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calculator.cost import (
    calculate_weight,
    estimate_print_time_hours,
    safe_eval_formula,
    validate_formula_expression,
    merge_pricing_config,
)

DEFAULT_CFG = {
    "machine_hourly_rate_cny": 15.0,
    "setup_fee_cny": 0.0,
    "min_job_fee_cny": 0.0,
    "material_waste_percent": 5.0,
    "support_percent_of_model": 0.0,
    "post_process_fee_per_part_cny": 0.0,
    "difficulty_coefficient": 0.25,
    "difficulty_ratio_low": 0.8,
    "difficulty_ratio_high": 4.0,
    "time_overhead_min": 5.0,
    "time_vol_min_per_cm3": 0.8,
    "time_area_min_per_cm2": 0.0,
    "time_ref_layer_height_mm": 0.2,
    "time_layer_height_exponent": 1.0,
    "time_ref_infill_percent": 20.0,
    "time_infill_coefficient": 1.0,
}


class TestCalculateWeight:
    def test_pla_weight(self):
        """PLA density 1.24 g/cm³"""
        weight = calculate_weight(100000, 1.24)  # 100 cm³
        assert abs(weight - 124.0) < 0.1

    def test_zero_volume(self):
        weight = calculate_weight(0, 1.24)
        assert weight == 0.0

    def test_low_density(self):
        weight = calculate_weight(1000, 0.5)  # 1 cm³
        assert abs(weight - 0.5) < 0.01


class TestPrintTimeEstimation:
    def test_basic_time(self):
        h = estimate_print_time_hours(100000, 5000, 0.2, 20, DEFAULT_CFG)
        assert h > 0.0

    def test_thin_layer_hours(self):
        h1 = estimate_print_time_hours(100000, 5000, 0.1, 20, DEFAULT_CFG)
        h2 = estimate_print_time_hours(100000, 5000, 0.2, 20, DEFAULT_CFG)
        # thinner layers should take longer (or at least not be faster)
        assert h1 >= h2 * 0.5  # can't be way faster

    def test_high_infill_longer(self):
        h1 = estimate_print_time_hours(100000, 5000, 0.2, 10, DEFAULT_CFG)
        h2 = estimate_print_time_hours(100000, 5000, 0.2, 80, DEFAULT_CFG)
        assert h2 >= h1

    def test_returns_float(self):
        h = estimate_print_time_hours(100000, 5000, 0.2, 20, DEFAULT_CFG)
        assert isinstance(h, float)


class TestSafeEval:
    def test_simple_addition(self):
        result = safe_eval_formula("a + b", {"a": 3.0, "b": 4.0})
        assert result == 7.0

    def test_with_max_min(self):
        result = safe_eval_formula("max(a, b)", {"a": 3.0, "b": 7.0})
        assert result == 7.0

    def test_complex_formula(self):
        result = safe_eval_formula(
            "(weight * price_per_kg / 1000) + (time * rate)",
            {"weight": 100.0, "price_per_kg": 200.0, "time": 2.0, "rate": 15.0}
        )
        assert abs(result - 50.0) < 0.01

    def test_blocks_dangerous_code(self):
        result = safe_eval_formula("__import__('os').system('ls')", {})
        assert result is None

    def test_blocks_attribute_access(self):
        result = safe_eval_formula("a.__class__", {"a": "hello"})
        assert result is None

    def test_empty_returns_none(self):
        assert safe_eval_formula("", {}) is None

    def test_blocks_unknown_variables(self):
        result = safe_eval_formula("unknown_var + 1", {"a": 1.0})
        assert result is None

    def test_infinite_result_returns_none(self):
        result = safe_eval_formula("1 / 0", {})
        assert result is None


class TestFormulaValidation:
    def test_valid_formula(self):
        ok, err, vars = validate_formula_expression("effective_weight_g * price_per_kg / 1000")
        assert ok is True
        assert err == ""
        assert "effective_weight_g" in vars

    def test_syntax_error(self):
        ok, err, vars = validate_formula_expression("a +* b")
        assert ok is False
        assert len(vars) == 0

    def test_unknown_variable(self):
        ok, err, vars = validate_formula_expression("bogus_var + 1")
        assert ok is False

    def test_max_min_allowed(self):
        ok, err, vars = validate_formula_expression("max(unit_cost_cny, min_job_fee_cny)")
        assert ok is True

    def test_empty_formula(self):
        ok, err, vars = validate_formula_expression("")
        assert ok is False


class TestMergePricingConfig:
    def test_empty_returns_default(self):
        merged = merge_pricing_config({})
        assert "machine_hourly_rate_cny" in merged
        assert merged["machine_hourly_rate_cny"] == 15.0

    def test_override_value(self):
        merged = merge_pricing_config({"machine_hourly_rate_cny": 25.0})
        assert merged["machine_hourly_rate_cny"] == 25.0

    def test_unknown_keys_preserved(self):
        merged = merge_pricing_config({"custom_key": 42})
        assert merged["custom_key"] == 42
