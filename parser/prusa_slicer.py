"""
PrusaSlicer integration — headless slicing for accurate time & filament estimation.

Uses PrusaSlicer via apt (prusa-slicer CLI) in headless mode.
Config is loaded from profiles/prusa/print.ini (system default) or user presets.
Quote parameters (layer_height, infill%, perimeters, density) override config at
slice time by generating a temporary combined INI file.
"""

import os
import re
import logging
import tempfile
import subprocess
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

# ── Executable discovery ──

def prusa_executable() -> Optional[str]:
    """Return path to prusa-slicer binary, or None if not installed."""
    for candidate in [
        shutil.which("prusa-slicer"),
        "/usr/bin/prusa-slicer",
        "/usr/local/bin/prusa-slicer",
        "/snap/bin/prusa-slicer",
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def prusa_executable_diagnostics() -> dict:
    diag = {"found": False, "path": None, "version": None}
    exe = prusa_executable()
    if exe:
        diag["found"] = True
        diag["path"] = exe
        try:
            out = subprocess.check_output([exe, "--help"], stderr=subprocess.STDOUT, timeout=10)
            diag["version"] = out.decode("utf-8", errors="replace").split("\n")[0].strip()
        except Exception as e:
            diag["version"] = f"error: {e}"
    return diag


# ── G-code parsing ──

def parse_prusa_gcode_stats(gcode_path: str) -> dict:
    """Parse PrusaSlicer G-code for filament usage and print time."""
    result: dict = {
        "filament_mm": 0.0,
        "filament_cm3": 0.0,
        "filament_g": 0.0,
        "time_s": 0,
        "time_str": "",
    }
    if not os.path.exists(gcode_path):
        return result

    with open(gcode_path, "r", encoding="utf-8", errors="replace") as f:
        # Stats are at the end of the file — read last 256KB
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        chunk_size = min(256 * 1024, file_size)
        f.seek(file_size - chunk_size)
        content = f.read(chunk_size)

    if m := re.search(r"; filament used \[mm\] = ([\d.]+)", content):
        result["filament_mm"] = float(m.group(1))
    if m := re.search(r"; filament used \[cm3\] = ([\d.]+)", content):
        result["filament_cm3"] = float(m.group(1))
    if m := re.search(r"; total filament used \[g\] = ([\d.]+)", content):
        result["filament_g"] = float(m.group(1))

    # Parse time: "estimated printing time (normal mode) = Xh Ym Zs" or "Xm Ys"
    if m := re.search(r"; estimated printing time \(normal mode\) = (\d+)h (\d+)m (\d+)s", content):
        result["time_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        result["time_str"] = f"{m.group(1)}h {m.group(2)}m {m.group(3)}s"
    elif m := re.search(r"; estimated printing time \(normal mode\) = (\d+)m (\d+)s", content):
        result["time_s"] = int(m.group(1)) * 60 + int(m.group(2))
        result["time_str"] = f"{m.group(1)}m {m.group(2)}s"

    return result


# ── Config generation ──

_SYSTEM_INI_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles", "prusa", "print.ini")


def _load_system_ini() -> str:
    """Load system default INI content."""
    if not os.path.exists(_SYSTEM_INI_PATH):
        raise RuntimeError(f"System PrusaSlicer config not found: {_SYSTEM_INI_PATH}")
    with open(_SYSTEM_INI_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse_ini_settings(content: str) -> dict[str, str]:
    """Parse INI content into flat key=value dict (ignoring section headers)."""
    settings: dict[str, str] = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            key = k.strip()
            if key:
                settings[key] = v.strip()
    return settings


def generate_slice_config(
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    material_density: float = 1.24,
    slicer_preset: Optional[dict] = None,
) -> str:
    """
    Generate a combined PrusaSlicer INI config file for a quote request.

    Merges: system defaults → user preset overrides → quote parameters.
    Returns path to temporary INI file.
    """
    # Load system default settings
    try:
        ini_content = _load_system_ini()
    except Exception:
        # Fallback: generate minimal config
        ini_content = ""

    settings = _parse_ini_settings(ini_content)

    # Apply user preset overrides (if provided and is valid INI content)
    if slicer_preset and isinstance(slicer_preset, dict) and slicer_preset.get("content"):
        raw = slicer_preset["content"]
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        first_byte = raw[:1] if raw else b""
        if first_byte not in (b"{", b"[") and b"=" in raw:
            preset_settings = _parse_ini_settings(raw.decode("utf-8", errors="replace"))
            settings.update(preset_settings)

    # Apply quote parameter overrides (these always win)
    settings["layer_height"] = str(layer_height)
    settings["first_layer_height"] = str(round(min(layer_height * 1.75, 0.35), 2))
    settings["fill_density"] = f"{infill_percent}%"
    settings["perimeters"] = str(perimeters)
    settings["wall_loops"] = str(perimeters)
    settings["top_shell_layers"] = str(max(3, min(perimeters + 2, 10)))
    settings["bottom_shell_layers"] = str(max(3, min(perimeters + 2, 10)))
    settings["filament_density"] = str(material_density)

    # Write temp config file
    fd, path = tempfile.mkstemp(suffix=".ini", prefix="prc3d_")
    with os.fdopen(fd, "w") as f:
        f.write("; Generated by pricer3d — combined slice config\n")
        f.write(f"; layer_height={layer_height} infill={infill_percent}% perimeters={perimeters} density={material_density}\n\n")
        for key in sorted(settings):
            f.write(f"{key} = {settings[key]}\n")

    logger.info(f"PrusaSlicer config: {len(settings)} settings → {path}")
    return path


# ── Slice ──

_SLICE_TIMEOUT = int(os.getenv("PRUSA_SLICE_TIMEOUT", "120"))


def run_prusa_slice(
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
    Run PrusaSlicer headless. Merges system/user/quote config into temp INI.

    Returns dict from parse_prusa_gcode_stats() plus:
      - preset_used: str (config source description)
      - error: str (only on failure)
    """
    exe = prusa_executable()
    if not exe:
        raise RuntimeError("PrusaSlicer not found — install: apt-get install prusa-slicer")

    out_dir = os.path.dirname(output_gcode_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    config_path = generate_slice_config(
        layer_height=layer_height,
        infill_percent=infill_percent,
        perimeters=perimeters,
        material_density=material_density,
        slicer_preset=slicer_preset,
    )

    preset_label = "系统默认 (A1)"
    if slicer_preset and slicer_preset.get("name"):
        preset_label = str(slicer_preset["name"])

    cmd = [
        exe,
        "--ignore-nonexistent-config",
        "--load", config_path,
        "--export-gcode",
        "--output", output_gcode_path,
    ]
    if enable_supports:
        cmd.append("--support-material")
    cmd.append(model_path)

    logger.info(f"PrusaSlicer: preset={preset_label} model={os.path.basename(model_path)}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_SLICE_TIMEOUT)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if proc.returncode != 0 and not os.path.exists(output_gcode_path):
            error_msg = stderr or stdout or f"exit code {proc.returncode}"
            raise RuntimeError(f"PrusaSlicer failed: {error_msg[:200]}")

        if stderr:
            logger.debug(f"PrusaSlicer stderr: {stderr[:200]}")

        stats = parse_prusa_gcode_stats(output_gcode_path)
        stats["preset_used"] = preset_label
        logger.info(f"PrusaSlicer result: cm³={stats['filament_cm3']} g={stats['filament_g']} time={stats['time_str']}")
        return stats

    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PrusaSlicer timed out ({_SLICE_TIMEOUT}s)")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"PrusaSlicer error: {e}")
    finally:
        # Cleanup temp config
        try:
            if os.path.exists(config_path):
                os.unlink(config_path)
        except OSError:
            pass


def prusa_support_diff_stats(
    model_path: str,
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    output_dir: Optional[str] = None,
    output_prefix: Optional[str] = None,
) -> dict:
    """Slice with & without supports; return support material diff + time."""
    result: dict = {
        "support_g": 0.0,
        "estimated_time_s": None,
        "filament_g": None,
        "no_support_time_s": None,
        "no_support_filament_g": None,
    }
    if not os.path.exists(model_path):
        return result

    out_dir = output_dir or tempfile.mkdtemp()
    prefix = output_prefix or "prusa_support"

    def _slice_and_get_stats(supports: bool) -> tuple[float, int]:
        """Slice once and return (filament_g, time_s)."""
        gcode = os.path.join(out_dir, f"{prefix}_{'with' if supports else 'no'}_support.gcode")
        stats = run_prusa_slice(model_path, gcode,
                                layer_height=layer_height, infill_percent=infill_percent,
                                perimeters=perimeters, enable_supports=supports)
        g = float(stats.get("filament_g") or 0)
        if g <= 0 and float(stats.get("filament_cm3") or 0) > 0:
            g = float(stats["filament_cm3"]) * 1.24
        time_s = int(stats.get("time_s") or 0)
        return g, time_s

    try:
        no_sup_g, no_sup_time = _slice_and_get_stats(False)
        with_sup_g, with_sup_time = _slice_and_get_stats(True)
        result["no_support_filament_g"] = no_sup_g
        result["no_support_time_s"] = no_sup_time if no_sup_time > 0 else None
        result["filament_g"] = with_sup_g
        result["estimated_time_s"] = with_sup_time if with_sup_time > 0 else None
        result["support_g"] = round(max(0.0, with_sup_g - no_sup_g), 3)
    except Exception as e:
        logger.error(f"PrusaSlicer support diff failed: {e}")

    return result


# ── Config generation for user presets ──

def generate_prusa_config(
    layer_height: float = 0.2,
    infill_percent: int = 20,
    perimeters: int = 3,
    top_shell_layers: int = 5,
    bottom_shell_layers: int = 5,
    material_density: float = 1.24,
) -> str:
    """
    Generate a PrusaSlicer-compatible INI config snippet.
    Uses system default as base, overrides with given params.
    Returns path to temporary config file.
    """
    fd, path = tempfile.mkstemp(suffix=".ini", prefix="prusa_gen_")
    with os.fdopen(fd, "w") as f:
        f.write(f"""; Generated PrusaSlicer config — pricer3d
layer_height = {layer_height}
first_layer_height = {round(min(layer_height * 1.75, 0.35), 2)}
fill_density = {infill_percent}%
perimeters = {perimeters}
top_shell_layers = {top_shell_layers}
bottom_shell_layers = {bottom_shell_layers}
nozzle_diameter = 0.4
filament_diameter = 1.75
filament_density = {material_density}
filament_type = PLA
temperature = 220
first_layer_temperature = 220
bed_temperature = 55
first_layer_bed_temperature = 55
perimeter_speed = 250
external_perimeter_speed = 200
infill_speed = 250
solid_infill_speed = 250
top_solid_infill_speed = 200
travel_speed = 500
first_layer_speed = 45
default_acceleration = 6000
perimeter_acceleration = 5000
infill_acceleration = 10000
external_perimeter_acceleration = 4000
bridge_acceleration = 5000
first_layer_acceleration = 2000
travel_acceleration = 10000
machine_max_acceleration_x = 12000
machine_max_acceleration_y = 12000
machine_max_acceleration_z = 1500
machine_max_acceleration_e = 5000
machine_max_acceleration_extruding = 12000
machine_max_feedrate_x = 500
machine_max_feedrate_y = 500
machine_max_feedrate_z = 30
machine_max_feedrate_e = 30
machine_max_jerk_x = 9
machine_max_jerk_y = 9
machine_max_jerk_z = 3
machine_max_jerk_e = 3
retract_length = 0.8
retract_speed = 30
deretract_speed = 30
retract_before_travel = 1.5
retract_lift = 0.4
max_volumetric_speed = 0
cooling = 1
fan_always_on = 1
max_fan_speed = 100
min_fan_speed = 35
bridge_fan_speed = 100
disable_fan_first_layers = 1
fan_below_layer_time = 60
slowdown_below_layer_time = 10
min_print_speed = 25
resolution = 0.0125
extrusion_multiplier = 0.98
bed_shape = 0x0,256x0,256x256,0x256
min_layer_height = 0.08
max_layer_height = 0.32
""")
    return path
