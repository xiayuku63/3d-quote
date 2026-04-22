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


def parse_prusaslicer_gcode_stats(gcode_path: str) -> dict:
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
        m = re.search(r"filament used\s*\[g\]\s*=\s*([0-9]+(?:\.[0-9]+)?)", s, flags=re.I)
        if m and out["filament_g"] is None:
            try:
                out["filament_g"] = float(m.group(1))
            except Exception:
                pass
        m = re.search(r"filament used\s*\[mm\]\s*=\s*([0-9]+(?:\.[0-9]+)?)", s, flags=re.I)
        if m and out["filament_mm"] is None:
            try:
                out["filament_mm"] = float(m.group(1))
            except Exception:
                pass
        m = re.search(r"estimated printing time.*=\s*(.+)$", s, flags=re.I)
        if m and out["estimated_time_s"] is None:
            sec = _parse_hms_to_seconds(m.group(1))
            if sec is not None:
                out["estimated_time_s"] = int(sec)

    if out["estimated_time_s"] is None:
        for line in lines:
            s = line.strip()
            if not s.startswith(";"):
                continue
            m = re.search(r"\bTIME\s*:\s*([0-9]+)\b", s, flags=re.I)
            if m:
                try:
                    out["estimated_time_s"] = int(m.group(1))
                    break
                except Exception:
                    pass
    return out


def prusaslicer_executable() -> Optional[str]:
    candidates = []
    env_path = os.getenv("PRUSASLICER_PATH", "").strip()
    if env_path:
        candidates.append(env_path)
    candidates.extend(_env_csv("PRUSASLICER_PATH_CANDIDATES"))
    
    # 优先查找本地便携版 PrusaSlicer
    local_portable = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "PrusaSlicer", "PrusaSlicer-2.9.4", "prusa-slicer-console.exe"))
    candidates.append(local_portable)

    # 常见 Windows 路径
    windir = os.environ.get("ProgramFiles", "C:\\Program Files")
    windir_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    candidates.extend(
        [
            os.path.join(windir, "Prusa3D", "PrusaSlicer", "prusa-slicer-console.exe"),
            os.path.join(windir, "Prusa3D", "PrusaSlicer", "prusa-slicer.exe"),
            os.path.join(windir_x86, "Prusa3D", "PrusaSlicer", "prusa-slicer-console.exe"),
            os.path.join(windir_x86, "Prusa3D", "PrusaSlicer", "prusa-slicer.exe"),
        ]
    )
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


def run_prusaslicer_slice(
    model_path: str,
    output_gcode_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
) -> dict:
    exe = prusaslicer_executable()
    if not exe:
        raise RuntimeError("未配置 PRUSASLICER_PATH")
    out_dir = os.path.dirname(str(output_gcode_path or "").strip())
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    loads = []
    loads.extend(_env_csv("PRUSASLICER_LOAD_FILES"))
    if extra_loads:
        loads.extend([x for x in extra_loads if x])
    cmd = []
    # 如果是 Linux 环境且未配置 DISPLAY，则使用 xvfb-run 避免缺少 X11 的报错
    if os.name == "posix" and not os.environ.get("DISPLAY"):
        cmd.extend(["xvfb-run", "-a"])
    cmd.extend([exe, "--export-gcode", "--output", output_gcode_path])
    for cfg in loads:
        if os.path.exists(cfg):
            cmd.extend(["--load", cfg])
    if extra_sets:
        for k, v in extra_sets.items():
            if str(k).startswith("--"):
                flag = k
            else:
                flag = f"--{k.replace('_', '-')}"
            if v == "":
                cmd.append(flag)
            else:
                cmd.append(f"{flag}={v}")
    cmd.append(model_path)
    timeout_s = float(os.getenv("PRUSASLICER_TIMEOUT_SECONDS", "90") or "90")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(f"PrusaSlicer 切片失败：{err[:300]}")
    stats = parse_prusaslicer_gcode_stats(output_gcode_path)
    return stats


def prusaslicer_support_diff_stats(
    model_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
    output_dir: Optional[str] = None,
    output_prefix: str = "",
) -> dict:
    base_dir = str(output_dir or "").strip()
    if not base_dir:
        base_dir = os.path.join(_outputs_base_dir(), _date_folder_utc(), uuid.uuid4().hex)
    os.makedirs(base_dir, exist_ok=True)
    prefix = _sanitize_filename_component(output_prefix, fallback="", max_len=50)
    if prefix and not prefix.endswith("_"):
        prefix = prefix + "_"
    g_on = os.path.join(base_dir, f"{prefix}with_support.gcode")
    g_off = os.path.join(base_dir, f"{prefix}no_support.gcode")
    load_on = _env_csv("PRUSASLICER_LOAD_FILES_SUPPORT_ON")
    load_off = _env_csv("PRUSASLICER_LOAD_FILES_SUPPORT_OFF")
    if load_on and load_off:
        st_on = run_prusaslicer_slice(model_path, g_on, extra_loads=(list(load_on) + list(extra_loads or [])), extra_sets=dict(extra_sets or {}))
        st_off = run_prusaslicer_slice(model_path, g_off, extra_loads=(list(load_off) + list(extra_loads or [])), extra_sets=dict(extra_sets or {}))
    else:
        base_sets = dict(extra_sets or {})
        st_on = run_prusaslicer_slice(
            model_path,
            g_on,
            extra_loads=extra_loads,
            extra_sets={**base_sets, "--support-material": "1", "--support-material-auto": "1"},
        )
        st_off = run_prusaslicer_slice(
            model_path,
            g_off,
            extra_loads=extra_loads,
            extra_sets={**base_sets, "--support-material": "0", "--support-material-auto": "0"},
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

