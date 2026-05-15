"""
bambu_cli.py — Python wrapper for the pure-CLI BambuStudio slicer.

Drop-in replacement for parser/prusa_slicer.py's run_prusa_slice().
Calls the `bambu_cli` binary which links ONLY against libslic3r.
"""

import os
import re
import json
import logging
import tempfile
import subprocess
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------

def bambu_cli_executable() -> Optional[str]:
    """Return path to bambu_cli binary, or None."""
    # Check common paths
    candidates = [
        "bambu_cli",
        "/usr/local/bin/bambu_cli",
        "/app/bambu_cli",
        os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "bambu_cli", "build", "bambu_cli"),
    ]
    for p in candidates:
        if shutil.which(p) or (os.path.isabs(p) and os.path.exists(p)):
            return p
    # Try PATH
    found = shutil.which("bambu_cli")
    if found:
        return found
    return None


def bambu_cli_diagnostics() -> dict:
    """Return diagnostic info about the bambu_cli installation."""
    diag = {
        "found": False,
        "path": None,
        "version": None,
    }
    exe = bambu_cli_executable()
    if exe:
        diag["found"] = True
        diag["path"] = exe
        try:
            out = subprocess.check_output(
                [exe, "--version"], stderr=subprocess.STDOUT, timeout=10
            )
            diag["version"] = out.decode("utf-8", errors="replace").strip()
        except Exception as e:
            diag["version"] = f"error: {e}"
    return diag


# ---------------------------------------------------------------------------
# Config generation (JSON → temp file that bambu_cli reads)
# ---------------------------------------------------------------------------

def _generate_cli_config_json(
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    material_density: float = 1.24,
) -> str:
    """
    Generate a minimal Bambu-compatible process JSON override.
    This is used when the user hasn't provided a custom preset.
    """
    fd, path = tempfile.mkstemp(suffix=".json", prefix="bambu_cli_")
    config = {
        "type": "process",
        "name": "0.20mm Standard @bambu_cli",
        "layer_height": str(layer_height),
        "fill_density": f"{infill_percent}%",
        "perimeters": str(perimeters),
        "top_shell_layers": "5",
        "bottom_shell_layers": "5",
        "sparse_infill_density": f"{infill_percent}%",
        # A1 speed/accel defaults
        "outer_perimeter_speed": "200",
        "inner_perimeter_speed": "300",
        "infill_speed": "300",
        "solid_infill_speed": "250",
        "travel_speed": "500",
        "first_layer_speed": "50",
        "default_acceleration": "10000",
        "perimeter_acceleration": "8000",
        "infill_acceleration": "12000",
        "first_layer_acceleration": "2000",
        "travel_acceleration": "10000",
        "retract_length": "0.8",
        "retract_speed": "30",
        "filament_diameter": "1.75",
        "filament_density": str(material_density),
        "support_material": "1",
        "support_material_auto": "1",
        "enable_support": "1",
    }
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# G-code stat parsing (same format as PrusaSlicer output)
# ---------------------------------------------------------------------------

def parse_bambu_cli_gcode_stats(gcode_path: str) -> dict:
    """
    Parse BambuStudio G-code output for filament usage and print time.
    BambuStudio uses the same comment format as PrusaSlicer.
    """
    result = {
        "filament_mm": 0.0,
        "filament_cm3": 0.0,
        "filament_g": 0.0,
        "time_s": 0,
        "time_str": "",
    }

    if not os.path.exists(gcode_path):
        return result

    with open(gcode_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # BambuStudio G-code comments
    m_mm = re.search(r"; filament used \[mm\] = ([\d.]+)", content)
    if m_mm:
        result["filament_mm"] = float(m_mm.group(1))

    m_cm3 = re.search(r"; filament used \[cm3\] = ([\d.]+)", content)
    if m_cm3:
        result["filament_cm3"] = float(m_cm3.group(1))

    m_g = re.search(r"; total filament used \[g\] = ([\d.]+)", content)
    if m_g:
        result["filament_g"] = float(m_g.group(1))

    # Time: both "1h 44m 3s" and "44m 3s" formats
    m_time = re.search(
        r"; estimated printing time \(normal mode\) = (\d+)m (\d+)s", content
    )
    if m_time:
        result["time_s"] = int(m_time.group(1)) * 60 + int(m_time.group(2))
        result["time_str"] = f"{m_time.group(1)}m {m_time.group(2)}s"

    m_time_h = re.search(
        r"; estimated printing time \(normal mode\) = (\d+)h (\d+)m (\d+)s",
        content,
    )
    if m_time_h:
        result["time_s"] = (
            int(m_time_h.group(1)) * 3600
            + int(m_time_h.group(2)) * 60
            + int(m_time_h.group(3))
        )
        result["time_str"] = f"{m_time_h.group(1)}h {m_time_h.group(2)}m {m_time_h.group(3)}s"

    # BambuStudio JSON stats line (alternative format)
    m_json = re.search(r'; \{.*?"estimated_time".*?\}', content)
    if m_json and result["time_s"] == 0:
        try:
            # Extract JSON from comment line: ; { ... }
            json_start = m_json.group(0).find('{')
            json_str = m_json.group(0)[json_start:]
            stats_json = json.loads(json_str)
            result["time_s"] = stats_json.get("estimated_time", 0)
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Slice
# ---------------------------------------------------------------------------

def run_bambu_cli_slice(
    model_path: str,
    output_gcode_path: str,
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    material_density: float = 1.24,
    slicer_preset: Optional[dict] = None,
    enable_supports: bool = False,
) -> dict:
    """
    Run bambu_cli (pure CLI BambuStudio) to slice an STL file.

    Args:
        model_path: Path to input STL/3MF/STEP file
        output_gcode_path: Path for output G-code
        layer_height: Layer height in mm
        infill_percent: Infill percentage
        perimeters: Number of wall perimeters
        material_density: Filament density in g/cm³
        slicer_preset: User-provided preset (dict with 'content' key)
        enable_supports: Whether to enable support material

    Returns:
        Dict with filament/time stats plus 'preset_used' and 'error' on failure
    """
    exe = bambu_cli_executable()
    if not exe:
        raise RuntimeError(
            "bambu_cli not found. Build it from BambuStudio source with "
            "cmake -DSLIC3R_BUILD_CLI=ON, or install the Docker image."
        )

    # Ensure output directory exists
    out_dir = os.path.dirname(output_gcode_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ── Determine config source ──
    preset_name = None
    profile_files = {"printer": None, "process": None, "filament": None}

    # Check for user preset
    if slicer_preset and isinstance(slicer_preset, dict) and slicer_preset.get("content"):
        raw = slicer_preset["content"]
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        # If it's JSON content, write to temp file
        first_byte = raw.lstrip()[:1]
        if first_byte == b"{":
            fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="bambu_preset_")
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            profile_files["process"] = tmp_path
            preset_name = slicer_preset.get("name", "用户自定义")
            logger.info(f"bambu_cli: Using user preset: {preset_name}")

    # Fall back to system default profiles
    if not profile_files["printer"]:
        system_printer = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "profiles", "bambu", "machine.json",
        )
        if os.path.exists(system_printer):
            profile_files["printer"] = system_printer
            logger.info(f"bambu_cli: Using system printer profile: {system_printer}")

    if not profile_files["process"]:
        # Generate minimal process JSON
        profile_files["process"] = _generate_cli_config_json(
            layer_height, infill_percent, perimeters, material_density
        )
        preset_name = preset_name or "系统默认 (A1)"
        logger.info("bambu_cli: Using generated process config")

    if not profile_files["filament"]:
        system_filament = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "profiles", "bambu", "filament.json",
        )
        if os.path.exists(system_filament):
            profile_files["filament"] = system_filament

    # ── Build command ──
    cmd = [exe]

    if profile_files["printer"]:
        cmd.extend(["--printer", profile_files["printer"]])
    if profile_files["process"]:
        cmd.extend(["--process", profile_files["process"]])
    if profile_files["filament"]:
        cmd.extend(["--filament", profile_files["filament"]])

    cmd.extend([
        "--output", output_gcode_path,
        "--stats",
    ])

    # Layer / infill / perimeter overrides via --set
    cmd.extend([
        "--layer-height", str(layer_height),
        "--infill", str(infill_percent),
        "--set", f"perimeters={perimeters}",
    ])

    if enable_supports:
        cmd.extend([
            "--set", "enable_support=1",
            "--set", "support_material=1",
        ])

    cmd.append(model_path)

    logger.info(f"bambu_cli command: {' '.join(cmd)}")

    # ── Execute ──
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0:
            error_msg = stderr or stdout or f"bambu_cli exited with code {proc.returncode}"
            raise RuntimeError(f"bambu_cli slicing failed: {error_msg}")

        if stderr:
            logger.info(f"bambu_cli stderr: {stderr[:400]}")

        # Try to parse JSON stats from stdout
        stats = parse_bambu_cli_gcode_stats(output_gcode_path)
        stats["preset_used"] = preset_name

        # If --stats produced JSON, try to parse it for more accurate data
        if stdout:
            try:
                json_stats = json.loads(stdout)
                if json_stats.get("estimated_time_s"):
                    stats["time_s"] = json_stats["estimated_time_s"]
                if json_stats.get("filament_mm"):
                    stats["filament_mm"] = json_stats["filament_mm"]
            except json.JSONDecodeError:
                pass

        logger.info(
            f"bambu_cli result: filament_cm3={stats['filament_cm3']}, "
            f"time_s={stats['time_s']}"
        )
        return stats

    except subprocess.TimeoutExpired:
        raise RuntimeError("bambu_cli timed out (300s limit)")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"bambu_cli execution failed: {e}")


def bambu_cli_support_diff_stats(
    model_path: str,
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    output_dir: Optional[str] = None,
    output_prefix: Optional[str] = None,
) -> dict:
    """
    Estimate support material usage by slicing with and without supports.

    Returns dict with:
        - support_g: weight of support material (diff between on/off)
        - estimated_time_s: print time WITH supports
        - filament_g: total filament WITH supports
        - no_support_time_s: print time WITHOUT supports
        - no_support_filament_g: filament WITHOUT supports
    """
    result = {
        "support_g": 0.0,
        "estimated_time_s": None,
        "filament_g": None,
        "no_support_time_s": None,
        "no_support_filament_g": None,
    }

    if not os.path.exists(model_path):
        return result

    out_dir = output_dir or tempfile.mkdtemp()
    prefix = output_prefix or "bambu_cli_support"

    try:
        # Without supports
        no_sup_gcode = os.path.join(out_dir, f"{prefix}_no_support.gcode")
        no_sup_stats = run_bambu_cli_slice(
            model_path, no_sup_gcode,
            layer_height=layer_height,
            infill_percent=infill_percent,
            perimeters=perimeters,
            enable_supports=False,
        )
        result["no_support_time_s"] = no_sup_stats.get("time_s", 0)
        no_sup_g = no_sup_stats.get("filament_g", 0.0)
        if no_sup_g <= 0 and no_sup_stats.get("filament_cm3", 0) > 0:
            no_sup_g = no_sup_stats["filament_cm3"] * 1.24
        result["no_support_filament_g"] = no_sup_g

        # With supports
        with_sup_gcode = os.path.join(out_dir, f"{prefix}_with_support.gcode")
        with_sup_stats = run_bambu_cli_slice(
            model_path, with_sup_gcode,
            layer_height=layer_height,
            infill_percent=infill_percent,
            perimeters=perimeters,
            enable_supports=True,
        )
        result["estimated_time_s"] = with_sup_stats.get("time_s", 0)
        with_sup_g = with_sup_stats.get("filament_g", 0.0)
        if with_sup_g <= 0 and with_sup_stats.get("filament_cm3", 0) > 0:
            with_sup_g = with_sup_stats["filament_cm3"] * 1.24
        result["filament_g"] = with_sup_g

        # Diff
        result["support_g"] = round(
            max(0.0, float(with_sup_g) - float(no_sup_g)), 3
        )

    except Exception as e:
        logger.error(f"bambu_cli support diff failed: {e}")

    return result
