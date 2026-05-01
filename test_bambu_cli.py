"""
BambuSlicer CLI Integration Test

Usage:
    python test_bambu_cli.py
    python test_bambu_cli.py --exe "C:\Program Files\Bambu Studio\bambu-studio.exe"
    python test_bambu_cli.py --model test_kiri2.gcode  (wrong format - skipped)
    python test_bambu_cli.py --model path/to/model.stl
"""

import os
import sys
import json
import argparse
import tempfile
import shutil
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser.slicer import (
    bambu_executable,
    bambu_executable_diagnostics,
    run_bambu_slice,
    parse_bambu_gcode_stats,
    bambu_support_diff_stats,
)


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_ok(msg: str):
    print(f"  [OK]  {msg}")


def print_fail(msg: str):
    print(f"  [FAIL] {msg}")


def print_warn(msg: str):
    print(f"  [WARN] {msg}")


def print_info(msg: str):
    print(f"  [INFO] {msg}")


def test_diagnostics():
    print_header("Diagnostics: Bambu Studio Environment")
    diag = bambu_executable_diagnostics()
    print(f"  Bambu Studio found: {diag.get('bambu_studio_found')}")
    print(f"  Executable path:    {diag.get('bambu_studio_path') or '(not found)'}")
    print(f"  Profile directory:  {diag.get('profile_dir')}")
    print(f"  Profile dir exists: {diag.get('profile_dir_exists')}")

    profile_files = diag.get("profile_files", {})
    if profile_files:
        print(f"  machine.json:  {profile_files.get('machine_json_exists')}")
        print(f"  process.json:  {profile_files.get('process_json_exists')}")
        print(f"  filament.json: {profile_files.get('filament_json_exists')}")

    print()
    print("  Candidate executables:")
    for cand in diag.get("candidates", []):
        status = cand.get("status", "?")
        path = cand.get("candidate", "?")
        icon = "✓" if status == "ok" else "✗"
        print(f"    {icon} [{status}] {path}")
        if cand.get("error"):
            print(f"       Error: {cand['error']}")
        if cand.get("missing_sources"):
            print(f"       Missing: {cand['missing_sources'][:5]}")

    return diag


def test_basic_slice(model_path: str, output_dir: str):
    print_header("Test: Basic Slicing")

    if not os.path.exists(model_path):
        print_warn(f"Model file not found: {model_path}")
        return False

    if not model_path.lower().endswith((".stl", ".obj", ".step", ".stp", ".3mf")):
        print_warn(f"Skipping non-3D file: {model_path}")
        return False

    output_3mf = os.path.join(output_dir, "test_output.gcode.3mf")

    try:
        stats = run_bambu_slice(
            model_path,
            output_3mf,
            extra_sets={
                "sliceHeight": "0.2",
                "sliceFillSparse": "0.2",
                "sliceShells": "3",
            },
        )
        print_ok("Slicing completed successfully")
        print_info(f"Output 3MF: {output_3mf}")
        print_info(f"Estimated time: {stats.get('estimated_time_s', 'N/A')}s")
        print_info(f"Filament (g):   {stats.get('filament_g', 'N/A')}g")
        print_info(f"Filament (mm):  {stats.get('filament_mm', 'N/A')}mm")

        gcode_path = os.path.join(output_dir, "test_output.gcode")
        if os.path.exists(gcode_path):
            gcode_size = os.path.getsize(gcode_path)
            print_info(f"Extracted G-code: {gcode_path} ({gcode_size} bytes)")
        return True
    except Exception as e:
        print_fail(f"Slicing failed: {e}")
        traceback.print_exc()
        return False


def test_support_diff(model_path: str, output_dir: str):
    print_header("Test: Support Diff Stats")

    if not os.path.exists(model_path):
        print_warn(f"Model file not found: {model_path}")
        return False

    if not model_path.lower().endswith((".stl", ".obj", ".step", ".stp", ".3mf")):
        print_warn(f"Skipping non-3D file: {model_path}")
        return False

    try:
        stats = bambu_support_diff_stats(
            model_path,
            extra_sets={
                "sliceHeight": "0.2",
                "sliceFillSparse": "0.2",
                "sliceShells": "3",
            },
            output_dir=output_dir,
            output_prefix="test_diff",
        )
        print_ok("Support diff slicing completed")
        print_info(f"Support weight:           {stats.get('support_g', 'N/A')}g")
        print_info(f"Total filament (with):    {stats.get('filament_g', 'N/A')}g")
        print_info(f"Estimated time:           {stats.get('estimated_time_s', 'N/A')}s")
        print_info(f"With support 3MF:        {stats.get('output_3mf_with_support', 'N/A')}")
        print_info(f"No support 3MF:          {stats.get('output_3mf_no_support', 'N/A')}")
        return True
    except Exception as e:
        print_fail(f"Support diff failed: {e}")
        traceback.print_exc()
        return False


def test_gcode_parsing():
    print_header("Test: G-code Stats Parsing")
    test_files = []
    for root, dirs, files in os.walk("."):
        for f in files:
            if f.endswith(".gcode"):
                test_files.append(os.path.join(root, f))
            if len(test_files) >= 5:
                break
        if len(test_files) >= 5:
            break

    if not test_files:
        print_info("No .gcode files found to test parsing")
        return

    for gcode_path in test_files[:3]:
        try:
            stats = parse_bambu_gcode_stats(gcode_path)
            filename = os.path.basename(gcode_path)
            print(f"  File: {filename}")
            print(f"    Time: {stats.get('estimated_time_s', 'N/A')}s")
            print(f"    Filament: {stats.get('filament_g', 'N/A')}g / {stats.get('filament_mm', 'N/A')}mm")
        except Exception as e:
            print_warn(f"  Parse error for {os.path.basename(gcode_path)}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="BambuSlicer CLI Integration Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--exe",
        default=None,
        help="Path to bambu-studio executable (overrides BAMBU_EXECUTABLE env)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Path to 3D model file for slicing test (.stl, .obj, .3mf, etc.)",
    )
    parser.add_argument(
        "--diag-only",
        action="store_true",
        help="Only run diagnostics, skip slicing tests",
    )

    args = parser.parse_args()

    if args.exe:
        os.environ["BAMBU_EXECUTABLE"] = args.exe

    print("=" * 60)
    print("  BambuSlicer CLI Integration Test Suite")
    print("=" * 60)
    print(f"  Python:     {sys.version}")
    print(f"  Working dir: {os.getcwd()}")

    diag = test_diagnostics()

    if args.diag_only:
        if not diag.get("bambu_studio_found"):
            print("\n[INFO] Bambu Studio 未安装或未找到。请:")
            print("  1. 安装 Bambu Studio: https://bambulab.com/en/download/studio")
            print('  2. 设置环境变量: set BAMBU_EXECUTABLE="C:\\Program Files\\Bambu Studio\\bambu-studio.exe"')
            print("  3. 确认 profiles/bambu/ 目录下有 machine.json, process.json, filament.json")
        return 0 if diag.get("bambu_studio_found") else 1

    if not diag.get("bambu_studio_found"):
        print("\n" + "=" * 60)
        print("  Bambu Studio not found - skipping slicing tests")
        print("=" * 60)
        print("[INFO] 请安装 Bambu Studio 后再运行完整测试。")
        return 1

    if not diag.get("profile_dir_exists"):
        print_fail("Profile directory not found. Run: mkdir profiles\\bambu")
        return 1

    if not diag.get("profile_files", {}).get("machine_json_exists"):
        print_fail("machine.json missing in profile directory")
        return 1

    model_path = args.model
    if not model_path:
        for candidate in [
            "test_kiri2.gcode",
        ]:
            if os.path.exists(candidate):
                model_path = candidate
                break

    if model_path and model_path.lower().endswith(".gcode"):
        print_warn(f"G-code file cannot be sliced, skipping: {model_path}")
        model_path = None

    if not model_path:
        print("\n" + "=" * 60)
        print("  No valid 3D model found for slicing test")
        print("=" * 60)
        print("[INFO] Provide a .stl/.obj/.3mf file:")
        print("  python test_bambu_cli.py --model path/to/your_model.stl")
        print()
        test_gcode_parsing()
        return 0

    output_dir = tempfile.mkdtemp(prefix="bambu_test_")
    print_info(f"Test output directory: {output_dir}")

    results = {"diagnostics": True, "basic_slice": False, "support_diff": False}

    try:
        results["basic_slice"] = test_basic_slice(model_path, output_dir)
        results["support_diff"] = test_support_diff(model_path, output_dir)
    finally:
        print(f"\n[INFO] Output preserved at: {output_dir}")
        print("[INFO] Use --diag-only to skip slicing tests.")
        print()

    test_gcode_parsing()

    print_header("Summary")
    print(f"  Diagnostics:    {'PASS' if results['diagnostics'] else 'FAIL'}")
    print(f"  Basic slice:    {'PASS' if results['basic_slice'] else 'FAIL'}")
    print(f"  Support diff:   {'PASS' if results['support_diff'] else 'FAIL'}")

    all_pass = all(results.values())
    if all_pass:
        print("\n  All tests passed!")
    else:
        print("\n  Some tests failed. Check the output above.")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
