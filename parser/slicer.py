import os
import re
import subprocess
import tempfile
import shutil
import json
import zipfile
from typing import Optional

BAMBU_EXECUTABLE_ENV = os.getenv("BAMBU_EXECUTABLE", "").strip() or "bambu-studio"
BAMBU_PROFILE_DIR = os.getenv("BAMBU_PROFILE_DIR", "").strip() or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "profiles", "bambu"
)
BAMBU_TIMEOUT_SECONDS = float(os.getenv("BAMBU_TIMEOUT_SECONDS", "300") or "300")


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


def _extract_gcode_from_3mf(three_mf_path: str, output_gcode_path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(three_mf_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".gcode"):
                    with zf.open(name) as src:
                        with open(output_gcode_path, "wb") as dst:
                            dst.write(src.read())
                    return output_gcode_path
    except Exception:
        return None
    return None


def parse_bambu_gcode_stats(gcode_path: str) -> dict:
    out = {"estimated_time_s": None, "filament_g": None, "filament_mm": None}
    try:
        with open(gcode_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            filesize = f.tell()
            chunk_size = min(131072, filesize)
            f.seek(max(0, filesize - chunk_size))
            chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.splitlines()
    except Exception:
        return out

    for line in lines:
        s = line.strip()
        if not s.startswith(";"):
            continue

        m_time = re.search(r"estimated printing time.*?:\s*(.+)", s, flags=re.I)
        if m_time and out["estimated_time_s"] is None:
            time_str = m_time.group(1).strip()
            hms = _parse_hms_to_seconds(time_str)
            if hms:
                out["estimated_time_s"] = hms

        m_mm = re.search(r"filament used\s*\[mm\].*?:\s*([0-9]+(?:\.[0-9]+)?)", s, flags=re.I)
        if m_mm and out["filament_mm"] is None:
            try:
                out["filament_mm"] = float(m_mm.group(1))
            except Exception:
                pass

        m_g = re.search(r"filament used\s*\[g\].*?:\s*([0-9]+(?:\.[0-9]+)?)", s, flags=re.I)
        if m_g and out["filament_g"] is None:
            try:
                out["filament_g"] = float(m_g.group(1))
            except Exception:
                pass

    if out["filament_g"] is None and out["filament_mm"] is not None:
        radius_cm = 1.75 / 20.0
        length_cm = out["filament_mm"] / 10.0
        volume_cm3 = 3.14159265 * (radius_cm ** 2) * length_cm
        out["filament_g"] = volume_cm3 * 1.24

    return out


def _bambu_cli_kind(exe: str) -> str:
    raw = str(exe or "").strip().lower()
    if not raw:
        return "unknown"
    if "bambustudio" in raw.replace("\\", "/").replace(" ", ""):
        return "bambustudio"
    if "bambu-studio" in raw.replace("\\", "/"):
        return "bambu-studio"
    return "generic"


def _find_bambu_exe_candidates() -> list[str]:
    candidates: list[str] = []

    if BAMBU_EXECUTABLE_ENV != "bambu-studio":
        candidates.append(BAMBU_EXECUTABLE_ENV)

    candidates.extend(_env_csv("BAMBU_EXECUTABLE_CANDIDATES"))

    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\Bambu Studio\bambu-studio.exe",
            r"C:\Program Files\Bambu Studio\BambuStudio.exe",
            r"C:\Program Files (x86)\Bambu Studio\bambu-studio.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\BambuStudio\bambu-studio.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\BambuStudio\bambu-studio.exe"),
        ])
    else:
        candidates.extend([
            "/usr/bin/bambu-studio",
            "/opt/BambuStudio/bambu-studio",
            os.path.expanduser("~/BambuStudio/bambu-studio"),
        ])

    candidates.append("bambu-studio")

    out: list[str] = []
    for c in candidates:
        cc = os.path.abspath(str(c or "").strip())
        if cc and cc not in out:
            out.append(cc)
    return out


def bambu_executable() -> Optional[str]:
    for p in _find_bambu_exe_candidates():
        try:
            cand = str(p or "").strip()
            if not cand:
                continue
            if os.path.isabs(cand) and os.path.exists(cand):
                return cand
            if shutil.which(cand) is not None:
                return cand
        except Exception:
            continue
    return None


def bambu_executable_diagnostics() -> dict:
    diagnostics: dict = {
        "bambu_studio_found": False,
        "bambu_studio_path": None,
        "profile_dir": BAMBU_PROFILE_DIR,
        "profile_dir_exists": os.path.isdir(BAMBU_PROFILE_DIR),
        "candidates": [],
    }

    for cand in _find_bambu_exe_candidates():
        entry = {"candidate": cand, "status": "unknown"}
        try:
            raw = str(cand or "").strip()
            if not raw:
                entry["status"] = "empty"
            elif os.path.isabs(raw) and os.path.exists(cand):
                entry["status"] = "ok"
                if not diagnostics["bambu_studio_found"]:
                    diagnostics["bambu_studio_found"] = True
                    diagnostics["bambu_studio_path"] = cand
            elif shutil.which(raw) is not None:
                entry["status"] = "ok"
                if not diagnostics["bambu_studio_found"]:
                    diagnostics["bambu_studio_found"] = True
                    diagnostics["bambu_studio_path"] = raw
            else:
                entry["status"] = "not_found"
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
        diagnostics["candidates"].append(entry)

    if diagnostics["profile_dir_exists"]:
        machine_path = os.path.join(BAMBU_PROFILE_DIR, "machine.json")
        process_path = os.path.join(BAMBU_PROFILE_DIR, "process.json")
        filament_path = os.path.join(BAMBU_PROFILE_DIR, "filament.json")
        diagnostics["profile_files"] = {
            "machine_json_exists": os.path.exists(machine_path),
            "process_json_exists": os.path.exists(process_path),
            "filament_json_exists": os.path.exists(filament_path),
        }

    return diagnostics


def _generate_bambu_process_json(extra_sets: Optional[dict[str, str]] = None) -> dict:
    cfg: dict = {
        "type": "process",
        "name": "0.20mm Standard",
        "from": "system",
        "inherits": "0.20mm Standard @BBL A1",
        "layer_height": 0.2,
        "fill_density": "20%",
        "wall_loops": 3,
        "top_shell_layers": 5,
        "bottom_shell_layers": 5,
        "sparse_infill_density": "20%",
        "enable_support": False,
        "support_type": "normal(auto)",
        "skirt_loops": 1,
        "brim_width": 0,
    }

    sets = dict(extra_sets or {})
    if "sliceHeight" in sets:
        cfg["layer_height"] = float(sets["sliceHeight"])
    if "sliceFillSparse" in sets:
        fill_val = float(sets["sliceFillSparse"])
        cfg["fill_density"] = f"{round(fill_val * 100)}%"
        cfg["sparse_infill_density"] = f"{round(fill_val * 100)}%"
    if "sliceShells" in sets:
        shells = int(float(sets["sliceShells"]))
        cfg["wall_loops"] = shells
        cfg["top_shell_layers"] = shells
        cfg["bottom_shell_layers"] = shells
    if "sliceSupportDensity" in sets:
        sd = float(sets["sliceSupportDensity"])
        cfg["enable_support"] = sd > 0.0001

    return cfg


def _load_or_generate_profile(profile_dir: str, name: str, extra_sets: Optional[dict[str, str]] = None) -> str:
    profile_path = os.path.join(profile_dir, f"{name}.json")
    if os.path.exists(profile_path):
        cfg = None
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = None

        if cfg and isinstance(cfg, dict) and name == "process" and extra_sets:
            settings = dict(extra_sets)
            if "sliceHeight" in settings:
                cfg["layer_height"] = float(settings["sliceHeight"])
            if "sliceFillSparse" in settings:
                fill_val = float(settings["sliceFillSparse"])
                cfg["fill_density"] = f"{round(fill_val * 100)}%"
                cfg["sparse_infill_density"] = f"{round(fill_val * 100)}%"
            if "sliceShells" in settings:
                shells = int(float(settings["sliceShells"]))
                cfg["wall_loops"] = shells
                cfg["top_shell_layers"] = shells
                cfg["bottom_shell_layers"] = shells
            if "sliceSupportDensity" in settings:
                sd = float(settings["sliceSupportDensity"])
                cfg["enable_support"] = sd > 0.0001

    existing_profile = os.path.join(profile_dir, f"{name}.json")
    if os.path.exists(existing_profile):
        return existing_profile

    if name == "process":
        with open(existing_profile, "w", encoding="utf-8") as f:
            json.dump(_generate_bambu_process_json(extra_sets), f, ensure_ascii=False, indent=2)
        return existing_profile

    raise RuntimeError(
        f"缺少 Bambu Studio 配置文件: {name}.json，请将配置文件放入 {profile_dir}"
    )


def run_bambu_slice(
    model_path: str,
    output_3mf_path: str,
    extra_loads: Optional[list[str]] = None,
    extra_sets: Optional[dict[str, str]] = None,
):
    exe = bambu_executable()
    if not exe:
        diag = bambu_executable_diagnostics()
        details = []
        if not any(c.get("status") == "ok" for c in diag.get("candidates", [])):
            details.append("未找到 Bambu Studio 可执行文件，请安装 Bambu Studio 或设置环境变量 BAMBU_EXECUTABLE")
        if not diag.get("profile_dir_exists"):
            details.append(f"Profile 目录不存在: {BAMBU_PROFILE_DIR}")
        hint = (
            "请安装 Bambu Studio 并确保 bambu-studio 在 PATH 中，"
            "或设置环境变量 BAMBU_EXECUTABLE 指向 bambu-studio 可执行文件。"
            "配置文件 (machine.json, process.json, filament.json) 需位于 profiles/bambu/ 目录。"
        )
        msg = "未配置 Bambu Studio (找不到 bambu-studio)"
        if details:
            msg = msg + "；" + "；".join(details)
        raise RuntimeError(msg + "。 " + hint)

    out_dir = os.path.dirname(str(output_3mf_path or "").strip())
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    settings_files: list[str] = []
    if extra_loads:
        for ld in extra_loads:
            if ld and os.path.exists(ld):
                settings_files.append(ld)

    if not settings_files:
        settings_files = [
            _load_or_generate_profile(BAMBU_PROFILE_DIR, "machine", extra_sets),
            _load_or_generate_profile(BAMBU_PROFILE_DIR, "process", extra_sets),
        ]
    elif len(settings_files) == 1:
        settings_files.insert(0, _load_or_generate_profile(BAMBU_PROFILE_DIR, "machine", extra_sets))

    filament_path = os.path.join(BAMBU_PROFILE_DIR, "filament.json")
    if os.path.exists(filament_path):
        filament_arg = filament_path
    else:
        filament_arg = ""

    cmd = [exe]

    cmd.append("--slice")
    cmd.append("1")

    cmd.append("--load-settings")
    cmd.append(";".join(settings_files))

    if filament_arg:
        cmd.append("--load-filaments")
        cmd.append(filament_arg)

    cmd.append("--allow-newer-file")
    cmd.append("--skip-useless-pick")
    cmd.append("--export-3mf")
    cmd.append(output_3mf_path)
    cmd.append(model_path)

    timeout_s = BAMBU_TIMEOUT_SECONDS

    import shlex
    env = dict(os.environ)
    env.pop("DISPLAY", None)

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            env=env,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"Bambu Studio 切片失败 (exit={res.returncode}): {err[:400]} (exe={exe})")
    except FileNotFoundError:
        raise RuntimeError(f"未找到 Bambu Studio 命令: {exe}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Bambu Studio 切片超时 ({timeout_s}秒)")

    if not os.path.exists(output_3mf_path) or os.path.getsize(output_3mf_path) == 0:
        raise RuntimeError("Bambu Studio 未生成输出文件")

    gcode_output = os.path.splitext(output_3mf_path)[0] + ".gcode"
    if _extract_gcode_from_3mf(output_3mf_path, gcode_output):
        stats = parse_bambu_gcode_stats(gcode_output)
    else:
        stats = {"estimated_time_s": None, "filament_g": None, "filament_mm": None}

    return stats


def bambu_support_diff_stats(
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

    out_on = os.path.join(base_dir, f"{prefix}with_support.3mf")
    out_off = os.path.join(base_dir, f"{prefix}no_support.3mf")

    base_sets = dict(extra_sets or {})

    st_on = run_bambu_slice(
        model_path,
        out_on,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "sliceSupportDensity": "0.25"},
    )
    st_off = run_bambu_slice(
        model_path,
        out_off,
        extra_loads=extra_loads,
        extra_sets={**base_sets, "sliceSupportDensity": "0"},
    )

    out: dict = {"with_support": st_on, "no_support": st_off}
    out["output_dir"] = base_dir
    out["output_3mf_with_support"] = out_on
    out["output_3mf_no_support"] = out_off

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
