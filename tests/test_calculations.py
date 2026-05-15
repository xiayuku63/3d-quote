"""Cost calculation unit tests."""

import sys, os
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
        weight = calculate_weight(100000, 1.24)  # 100 cm³
        assert abs(weight - 124.0) < 0.1

    def test_zero_volume(self):
        assert calculate_weight(0, 1.24) == 0.0

    def test_low_density(self):
        weight = calculate_weight(1000, 0.5)
        assert abs(weight - 0.5) < 0.01

    def test_typical_pla_part(self):
        # A typical 50 cm³ PLA part
        weight = calculate_weight(50000, 1.24)
        assert abs(weight - 62.0) < 0.1


class TestPrintTimeEstimation:
    def test_basic_time(self):
        h = estimate_print_time_hours(100000, 5000, 0.2, 20, DEFAULT_CFG)
        assert h > 0.0

    def test_thinner_layer_takes_longer(self):
        h_thin = estimate_print_time_hours(100000, 5000, 0.1, 20, DEFAULT_CFG)
        h_thick = estimate_print_time_hours(100000, 5000, 0.2, 20, DEFAULT_CFG)
        assert h_thin >= h_thick * 0.5  # at minimum, not faster than half

    def test_high_infill_takes_longer(self):
        h_low = estimate_print_time_hours(100000, 5000, 0.2, 10, DEFAULT_CFG)
        h_high = estimate_print_time_hours(100000, 5000, 0.2, 80, DEFAULT_CFG)
        assert h_high >= h_low

    def test_zero_volume(self):
        h = estimate_print_time_hours(0, 0, 0.2, 20, DEFAULT_CFG)
        # 5 min overhead → 5/60 ≈ 0.083 hours
        assert 0.08 <= h <= 0.09

    def test_large_volume(self):
        h = estimate_print_time_hours(1000000, 50000, 0.2, 20, DEFAULT_CFG)
        assert h > 5.0  # large part should take hours


class TestSafeEval:
    def test_simple_addition(self):
        result = safe_eval_formula("a + b", {"a": 3.0, "b": 4.0})
        assert result == 7.0

    def test_max_min(self):
        result = safe_eval_formula("max(a, b)", {"a": 3.0, "b": 7.0})
        assert result == 7.0

    def test_division(self):
        result = safe_eval_formula("a / b", {"a": 10.0, "b": 2.0})
        assert abs(result - 5.0) < 0.001

    def test_power(self):
        result = safe_eval_formula("a ** 2", {"a": 3.0})
        assert abs(result - 9.0) < 0.001

    def test_complex_formula(self):
        result = safe_eval_formula(
            "max((a * b) + c, 10)",
            {"a": 2.0, "b": 3.0, "c": 1.0}
        )
        assert abs(result - 10.0) < 0.001  # 2*3+1=7, max(7,10)=10

    def test_malicious_code_blocked(self):
        # __import__, eval, exec, etc should be blocked
        result = safe_eval_formula("__import__('os').system('ls')", {})
        assert result is None

    def test_empty_expression(self):
        result = safe_eval_formula("", {})
        assert result is None

    def test_missing_variable(self):
        result = safe_eval_formula("a + b", {"a": 1.0})
        assert result is None


class TestValidateFormula:
    def test_valid_formula(self):
        ok, err, vars = validate_formula_expression("effective_weight_g * price_per_kg + machine_cost_cny")
        assert ok is True, f"Expected OK, got: {err}"

    def test_invalid_syntax(self):
        ok, err, vars = validate_formula_expression("a +")
        assert ok is False
        assert "语法" in err

    def test_unknown_variable(self):
        ok, err, vars = validate_formula_expression("foo + bar")
        assert ok is False
        assert "未知变量" in err

    def test_valid_quote_variable(self):
        ok, err, vars = validate_formula_expression("effective_weight_g * price_per_kg")
        assert ok is True

    def test_empty_formula(self):
        ok, err, vars = validate_formula_expression("")
        assert ok is False


class TestMergePricingConfig:
    def test_merge_empty(self):
        result = merge_pricing_config(None)
        assert "machine_hourly_rate_cny" in result
        assert result["machine_hourly_rate_cny"] == 15.0

    def test_override_value(self):
        result = merge_pricing_config({"machine_hourly_rate_cny": 30.0})
        assert result["machine_hourly_rate_cny"] == 30.0

    def test_partial_override_preserves_defaults(self):
        result = merge_pricing_config({"setup_fee_cny": 50.0})
        assert result["setup_fee_cny"] == 50.0
        assert result["machine_hourly_rate_cny"] == 15.0  # default preserved
