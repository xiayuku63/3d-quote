import os
import re
import subprocess
import tempfile
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


def parse_curaengine_gcode_stats(gcode_path: str) -> dict:
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
        
        # Cura output format:
        # ;TIME:4523
        # ;Filament used: 3.456m
        # ;MINX: ... (and other stats)
        
        m = re.search(r"TIME:\s*([0-9]+)", s, flags=re.I)
        if m and out["estimated_time_s"] is None:
            try:
                out["estimated_time_s"] = int(m.group(1))
            except Exception:
                pass
                
        m = re.search(r"Filament used:\s*([0-9]+(?:\.[0-9]+)?)\s*m", s, flags=re.I)
        if m and out["filament_mm"] is None:
            try:
                # Cura typically outputs in meters, so multiply by 1000 for mm
                out["filament_mm"] = float(m.group(1)) * 1000.0
                # Approximate weight (assuming 1.75mm PLA with density 1.24g/cm^3)
                # Volume in cm^3 = pi * r^2 * h = 3.14159 * (0.175/2)^2 * (length in cm)
                # Usually Cura also prints weight if configured, but let's just rely on mm for now if weight isn't found
            except Exception:
                pass
                
        # Some Cura versions might print weight
        m = re.search(r"Filament weight:\s*([0-9]+(?:\.[0-9]+)?)\s*g", s, flags=re.I)
        if m and out["filament_g"] is None:
            try:
                out["filament_g"] = float(m.group(1))
            except Exception:
                pass

    # If weight is not provided but mm is, we approximate it (assume 1.75mm filament, 1.24 density)
    if out["filament_g"] is None and out["filament_mm"] is not None:
        radius_cm = 1.75 / 20.0
        length_cm = out["filament_mm"] / 10.0
        volume_cm3 = 3.14159265 * (radius_cm ** 2) * length_cm
        out["filament_g"] = volume_cm3 * 1.24  # default PLA density approximation

    return out


def curaengine_executable() -> Optional[str]:
    candidates = []
    env_path = os.getenv("CURAENGINE_PATH", "").strip()
    if env_path:
        candidates.append(env_path)
    candidates.extend(_env_csv("CURAENGINE_PATH_CANDIDATES"))
    
    # 优先查找本地便携版 CuraEngine
    local_portable = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "CuraEngine", "CuraEngine.exe"))
    candidates.append(local_portable)

    # Linux common path
    candidates.append("/usr/bin/CuraEngine")
    candidates.append("/usr/local/bin/CuraEngine")

    # 常见 Windows 路径
    windir = os.environ.get("ProgramFiles", "C:\\Program Files")
    candidates.extend(
        [
            os.path.join(windir, "UltiMaker Cura", "CuraEngine.exe"),
            os.path.join(windir, "Cura", "CuraEngine.exe"),
        ]
    )
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


def run_curaengine_slice(
    model_path: str,
    output_gcode_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
) -> dict:
    exe = curaengine_executable()
    if not exe:
        raise RuntimeError("未配置 CURAENGINE_PATH (找不到 CuraEngine)")
    out_dir = os.path.dirname(str(output_gcode_path or "").strip())
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        
    cmd = [exe, "slice"]
    
    # CuraEngine 需要一个 base definition json 文件，比如 fdmprinter.def.json
    def_json = os.getenv("CURAENGINE_DEF_JSON")
    if def_json and os.path.exists(def_json):
        cmd.extend(["-j", def_json])
    
    loads = []
    loads.extend(_env_csv("CURAENGINE_LOAD_FILES"))
    if extra_loads:
        loads.extend([x for x in extra_loads if x])
        
    # Cura uses `-j` for definitions and `-s` for settings
    for cfg in loads:
        if os.path.exists(cfg):
            # 简化处理：暂时假设用户传的是 Cura 识别的 profile/def
            # 由于实际 Cura 的命令行系统比较挑剔，可能需要视文件扩展名区分
            cmd.extend(["-j", cfg])

    if extra_sets:
        for k, v in extra_sets.items():
            # CuraEngine 命令行传入 setting 格式为 `-s key=value`
            # 将旧的 --layer-height 风格转换为 layer_height 风格
            key = k.lstrip("-").replace("-", "_")
            cmd.extend(["-s", f"{key}={v}"])

    cmd.extend(["-l", model_path, "-o", output_gcode_path])
    
    timeout_s = float(os.getenv("CURAENGINE_TIMEOUT_SECONDS", "90") or "90")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"CuraEngine 切片失败：{err[:300]}")
        
    stats = parse_curaengine_gcode_stats(output_gcode_path)
    return stats


def curaengine_support_diff_stats(
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
    # CuraEngine support setting keys: support_enable=true
    st_on = run_curaengine_slice(
        model_path,
        g_on,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "support_enable": "true", "support_type": "buildplate"},
    )
    st_off = run_curaengine_slice(
        model_path,
        g_off,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "support_enable": "false"},
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

