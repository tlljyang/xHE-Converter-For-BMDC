"""Dependency resolution: bundled (Onekey) -> dependencies.yaml (Lite) -> PATH.

Resolution order per tool (ffmpeg / ffprobe / xaacdec):
  1. XHE4BMDC_FFMPEG / XHE4BMDC_FFPROBE / XHE4BMDC_XAACDEC env override
  2. bundled layout next to the executable / package (Onekey install)
  3. dependencies.yaml next to the program directory (Lite)
  4. system PATH
Each candidate is validated (exists + runs) before being accepted.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from xhe4bmdc.engine.probe import _creationflags

DEPS_FILENAME = "dependencies.yaml"

_TOOLS = ("ffmpeg", "ffprobe", "xaacdec")


@dataclass
class Deps:
    ffmpeg: str = ""
    ffprobe: str = ""
    xaacdec: str = ""
    problems: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    @property
    def ok(self) -> bool:
        """ffmpeg + ffprobe present (xaacdec optional — only xHE jobs need it)."""
        return bool(self.ffmpeg and self.ffprobe)

    @property
    def xhe_ready(self) -> bool:
        return bool(self.xaacdec)


def program_dir() -> Path:
    """Directory of the frozen exe (Onekey/Lite) or of the package (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _bundled_candidates() -> dict[str, list[Path]]:
    root = program_dir()
    ffdir = root / "ffmpeg" / "bin"
    bindir = root / "bin"
    return {
        "ffmpeg": [ffdir / _exe("ffmpeg"), bindir / _exe("ffmpeg"), root / _exe("ffmpeg")],
        "ffprobe": [ffdir / _exe("ffprobe"), bindir / _exe("ffprobe"), root / _exe("ffprobe")],
        "xaacdec": [ffdir / _exe("xaacdec"), bindir / _exe("xaacdec"), root / _exe("xaacdec")],
    }


def load_yaml_config(path: Path) -> dict:
    """Strict YAML load for the Lite dependency file.

    - No custom tags (safe_load)
    - Must be a flat mapping of tool -> {executable, ...}
    - Unknown keys are rejected so typos fail loudly
    """
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("dependencies.yaml 顶层必须是映射")
    out: dict = {}
    for key, val in data.items():
        if key not in _TOOLS and key != "schema":
            raise ValueError(f"未知配置项: {key}")
        if key == "schema":
            continue
        if not isinstance(val, dict):
            raise ValueError(f"{key} 必须是映射(含 executable 字段)")
        bad = set(val) - {"executable", "expected_version"}
        if bad:
            raise ValueError(f"{key} 含未知字段: {sorted(bad)}")
        out[key] = val
    return out


def _validate_runnable(path: str, version_flag: str = "-version") -> tuple[bool, str]:
    if not path:
        return False, "路径为空"
    p = Path(path)
    if not p.exists():
        return False, f"文件不存在: {path}"
    if not p.is_file():
        return False, f"不是文件: {path}"
    try:
        proc = subprocess.run([str(p), version_flag], capture_output=True,
                              text=True, timeout=15, creationflags=_creationflags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"无法执行: {exc}"
    if proc.returncode != 0:
        # xaacdec has no -version; a usage exit is acceptable evidence
        return "usage" in (proc.stdout + proc.stderr).lower() or proc.returncode == 1, \
            f"退出码 {proc.returncode}"
    return True, ""


def _check_yaml_version(path: str, expected: Optional[str]) -> list[str]:
    if not expected:
        return []
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, text=True,
                              timeout=15, creationflags=_creationflags())
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    except (OSError, subprocess.TimeoutExpired):
        return []
    import re
    m = re.search(r"(\d+)\.(\d+)", first)
    if not m:
        return [f"{path}: 无法解析版本，不校验 {expected}"]
    want = re.search(r">=\s*(\d+)\.(\d+)", expected)
    if want:
        got = (int(m.group(1)), int(m.group(2)))
        need = (int(want.group(1)), int(want.group(2)))
        if got < need:
            return [f"{path}: 版本 {got} 低于要求 {expected}"]
    return []


def resolve(config_path: Optional[Path] = None,
            overrides: Optional[dict[str, str]] = None) -> Deps:
    """Resolve the three tools; problems describe every failure (for the UI)."""
    deps = Deps()
    overrides = overrides or {}
    bundled = _bundled_candidates()

    yaml_cfg: dict = {}
    yml = config_path or (program_dir() / DEPS_FILENAME)
    if yml.exists():
        try:
            yaml_cfg = load_yaml_config(yml)
        except (ValueError, yaml.YAMLError) as exc:
            deps.problems.append(f"dependencies.yaml 无效: {exc}")

    for tool in _TOOLS:
        env_key = f"XHE4BMDC_{tool.upper()}"
        candidates: list[str] = []
        if env_key in os.environ:
            candidates.append(os.environ[env_key])
        if tool in overrides:
            candidates.append(overrides[tool])
        candidates += [str(p) for p in bundled[tool]]
        if tool in yaml_cfg and yaml_cfg[tool].get("executable"):
            candidates.append(yaml_cfg[tool]["executable"])
        found = shutil.which(tool)
        if found:
            candidates.append(found)

        chosen = ""
        problems: list[str] = []
        for cand in candidates:
            ok, why = _validate_runnable(cand)
            if ok:
                chosen = cand
                break
            problems.append(f"{cand}: {why}")
        if not chosen:
            deps.problems.append(
                f"未找到可用的 {tool}。尝试过:\n  " + "\n  ".join(problems[:4]))
        else:
            setattr(deps, tool, chosen)
            if tool in yaml_cfg:
                deps.problems += _check_yaml_version(chosen, yaml_cfg[tool].get("expected_version"))

    return deps


def write_example_yaml(path: Path) -> None:
    path.write_text(
        """# xHE4BMDC Lite 依赖配置
# 填写本机已有的 FFmpeg / xaacdec 路径（正斜杠或双反斜杠均可）
schema: 1
ffmpeg:
  executable: "C:/Tools/ffmpeg/bin/ffmpeg.exe"
  expected_version: ">=6.0"
ffprobe:
  executable: "C:/Tools/ffmpeg/bin/ffprobe.exe"
xaacdec:
  executable: "C:/Tools/xaacdec/xaacdec.exe"
""",
        encoding="utf-8")


def save_yaml_config(path: Path, values: dict[str, str]) -> None:
    data = {"schema": 1}
    for tool in _TOOLS:
        if values.get(tool):
            data[tool] = {"executable": values[tool]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
