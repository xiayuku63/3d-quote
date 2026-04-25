import os
import re
import subprocess
import tempfile
import shutil
from typing import Optional

def _env_csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw:
        return []
    parts = []
    for token in raw.replace(";", ",").split(","):
        t = token.strip().strip('"').strip("'")
        if t:
            parts.append(t)
    return parts


def _parse_hms_to_seconds(text: str) -> Optional[int]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    m = re.search(r"(?:(\d+)\s*d\s*)?(?:(\d+)\s*h\s*)?(?:(\d+)\s*m\s*)?(?:(\d+)\s*s\s*)?$", raw)
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    secs = int(m.group(4) or 0)
    total = days * 86400 + hours * 3600 + mins * 60 + secs
    if total <= 0:
        return None
    return total


def _grid_apps_root_candidates() -> list[str]:
    roots: list[str] = []
    for key in ("GRID_APPS_DIR", "GRID_APPS_ROOT", "KIRIMOTO_GRID_APPS_DIR"):
        raw = os.getenv(key, "").strip()
        if raw:
            roots.append(raw)
    roots.extend(_env_csv("GRID_APPS_DIR_CANDIDATES"))
    project_root = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__))))
    roots.append(os.path.join(project_root, "grid-apps"))
    out: list[str] = []
    for r in roots:
        rr = os.path.abspath(str(r or "").strip())
        if rr and rr not in out:
            out.append(rr)
    return out


def parse_kirimoto_gcode_stats(gcode_path: str) -> dict:
    out = {"estimated_time_s": None, "filament_g": None, "filament_mm": None}
    try:
        import os
        with open(gcode_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            filesize = f.tell()
            chunk_size = min(65536, filesize)
            f.seek(-chunk_size, os.SEEK_END)
            chunk = f.read().decode('utf-8', errors='ignore')
            lines = chunk.splitlines()
    except Exception:
        return out

    for line in lines:
        s = line.strip()
        if not s.startswith(";"):
            continue
        
        # Kiri:Moto output format is typically:
        # ; print time: 2h 34m 12s
        # ; filament used: 3456 mm
        # We also support PrusaSlicer/Cura generic formats as fallbacks
        
        # Time parsing
        m_time = re.search(r"(?:print time|estimated printing time).*?:\s*(.+)", s, flags=re.I)
        if m_time and out["estimated_time_s"] is None:
            time_str = m_time.group(1).strip()
            # Try parsing HMS
            hms = _parse_hms_to_seconds(time_str)
            if hms:
                out["estimated_time_s"] = hms
            else:
                # Try raw seconds like Cura: TIME:4523
                m_sec = re.search(r"TIME:\s*([0-9]+)", s, flags=re.I)
                if m_sec:
                    try:
                        out["estimated_time_s"] = int(m_sec.group(1))
                    except Exception:
                        pass
                        
        # Filament mm
        m_fil = re.search(r"filament used.*?:\s*([0-9]+(?:\.[0-9]+)?)\s*m", s, flags=re.I)
        if m_fil and out["filament_mm"] is None:
            try:
                # typically meters, check if m or mm
                if "mm" in s.lower():
                    out["filament_mm"] = float(m_fil.group(1))
                else:
                    out["filament_mm"] = float(m_fil.group(1)) * 1000.0
            except Exception:
                pass
                
        # Filament weight
        m_weight = re.search(r"filament weight.*?:\s*([0-9]+(?:\.[0-9]+)?)\s*g", s, flags=re.I)
        if m_weight and out["filament_g"] is None:
            try:
                out["filament_g"] = float(m_weight.group(1))
            except Exception:
                pass

    if out["filament_g"] is None and out["filament_mm"] is not None:
        radius_cm = 1.75 / 20.0
        length_cm = out["filament_mm"] / 10.0
        volume_cm3 = 3.14159265 * (radius_cm ** 2) * length_cm
        out["filament_g"] = volume_cm3 * 1.24  # default PLA density approximation

    return out


def kirimoto_executable() -> Optional[str]:
    candidates = []
    env_path = os.getenv("KIRIMOTO_PATH", "").strip()
    if env_path:
        candidates.append(env_path)
    candidates.extend(_env_csv("KIRIMOTO_PATH_CANDIDATES"))
    
    # Try global node module / CLI
    candidates.append("kiri-moto")
    candidates.append("kirimoto-slicer")

    for root in _grid_apps_root_candidates():
        local_cli = os.path.join(root, "src", "kiri", "run", "cli.js")
        candidates.append(f"node {os.path.abspath(local_cli)}")

    candidates.append("node /root/grid-apps/src/kiri/run/cli.js")

    for p in candidates:
        try:
            cand = str(p or "").strip()
            if not cand:
                continue
            if cand.startswith("node "):
                import shlex
                parts = shlex.split(cand, posix=(os.name != "nt"))
                if len(parts) < 2:
                    continue
                if shutil.which(parts[0]) is None:
                    continue
                script_path = parts[1]
                if os.path.exists(script_path):
                    return cand
                continue
            if os.path.isabs(cand):
                if os.path.exists(cand):
                    return cand
                continue
            import shlex
            parts = shlex.split(cand, posix=(os.name != "nt"))
            exe = parts[0] if parts else ""
            if exe and shutil.which(exe) is not None:
                return cand
            if os.path.exists(cand):
                return os.path.abspath(cand)
        except Exception:
            continue
    return None


def run_kirimoto_slice(
    model_path: str,
    output_gcode_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
) -> dict:
    exe = kirimoto_executable()
    if not exe:
        raise RuntimeError("未配置 KIRIMOTO_PATH (找不到 Kiri:Moto CLI)")
    out_dir = os.path.dirname(str(output_gcode_path or "").strip())
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        
    import shlex
    cmd = shlex.split(exe, posix=(os.name != "nt")) if (" " in exe or "\t" in exe) else [exe]
    
    # kiri-moto options
    if extra_loads:
        for cfg in extra_loads:
            if cfg and os.path.exists(cfg):
                # Using --load if supported by Spiritdude's fork, or --process for grid-apps
                cmd.extend([f"--process={cfg}"])

    if extra_sets:
        for k, v in extra_sets.items():
            # Add parameters based on how Kiri:Moto CLI accepts them
            # For grid-apps, settings might need to be in a process JSON file
            pass

    cmd.extend([f"--output={output_gcode_path}", model_path])
    
    timeout_s = float(os.getenv("KIRIMOTO_TIMEOUT_SECONDS", "120") or "120")
    
    cwd = None
    if cmd and cmd[0] == "node" and len(cmd) >= 2:
        script_path = cmd[1]
        if script_path and os.path.exists(script_path) and ("grid-apps" in script_path.replace("\\", "/")):
            grid_root = os.path.abspath(os.path.join(os.path.dirname(script_path), "..", "..", ".."))
            if os.path.isdir(grid_root):
                cwd = grid_root

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=cwd, shell=False)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"Kiri:Moto 切片失败：{err[:300]}")
    except FileNotFoundError:
         raise RuntimeError(f"未找到 Kiri:Moto CLI 命令: {exe}")
        
    stats = parse_kirimoto_gcode_stats(output_gcode_path)
    return stats


def kirimoto_support_diff_stats(
    model_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
    output_dir: Optional[str] = None,
    output_prefix: str = "",
) -> dict:
    base_dir = str(output_dir or "").strip()
    if not base_dir:
        import uuid
        from main import _outputs_base_dir, _date_folder_utc
        base_dir = os.path.join(_outputs_base_dir(), _date_folder_utc(), uuid.uuid4().hex)
    os.makedirs(base_dir, exist_ok=True)
    
    from main import _sanitize_filename_component
    prefix = _sanitize_filename_component(output_prefix, fallback="", max_len=50)
    if prefix and not prefix.endswith("_"):
        prefix = prefix + "_"
        
    g_on = os.path.join(base_dir, f"{prefix}with_support.gcode")
    g_off = os.path.join(base_dir, f"{prefix}no_support.gcode")
    
    base_sets = dict(extra_sets or {})
    # KiriMoto support settings
    st_on = run_kirimoto_slice(
        model_path,
        g_on,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "sliceSupportDensity": "0.25"},
    )
    st_off = run_kirimoto_slice(
        model_path,
        g_off,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "sliceSupportDensity": "0"},
    )
    
    out = {"with_support": st_on, "no_support": st_off}
    out["output_dir"] = base_dir
    out["gcode_with_support"] = g_on
    out["gcode_no_support"] = g_off
    try:
        g_on_val = float(st_on.get("filament_g") or 0.0)
    except Exception:
        g_on_val = 0.0
    try:
        g_off_val = float(st_off.get("filament_g") or 0.0)
    except Exception:
        g_off_val = 0.0
    support_g = max(0.0, g_on_val - g_off_val)
    out["support_g"] = round(support_g, 3)
    if st_on.get("filament_g") is not None:
        try:
            out["filament_g"] = float(st_on.get("filament_g") or 0.0)
        except Exception:
            out["filament_g"] = None
    if st_on.get("estimated_time_s") is not None:
        out["estimated_time_s"] = int(st_on["estimated_time_s"])
    return out
