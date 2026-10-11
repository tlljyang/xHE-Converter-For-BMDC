"""PyInstaller entry point: launches the Gradio UI with file logging.

Kept outside the package so the frozen app can log startup failures even when
the console window is hidden (Onekey). In windowed (console=False) mode
sys.stdout/sys.stderr are None — uvicorn's logging formatter calls isatty(),
so we must substitute real streams before Gradio/uvicorn configure logging.
"""
from __future__ import annotations

import datetime
import io
import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local" / "xHE4BMDC" / "logs"
    else:
        base = Path.home() / "Library" / "Logs" / "xHE4BMDC"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _install_stream(is_stdout: bool) -> None:
    """Replace None streams with a real file stream so isatty() works."""
    stream = sys.stdout if is_stdout else sys.stderr
    if stream is not None:
        return
    log = _log_dir() / f"stream-{datetime.date.today().isoformat()}.log"
    fh = open(log, "a", buffering=1, encoding="utf-8", errors="replace")
    setattr(sys, "stdout" if is_stdout else "stderr", fh)


def main() -> int:
    log = _log_dir() / f"startup-{datetime.date.today().isoformat()}.log"
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now()}] frozen={getattr(sys, 'frozen', False)} "
                    f"exe={sys.executable}\n")
        if getattr(sys, "frozen", False):
            _install_stream(True)
            _install_stream(False)
        from xhe4bmdc.app import main as app_main
        app_main()
        return 0
    except Exception:
        err = traceback.format_exc()
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(err)
        except OSError:
            pass
        # last resort: also surface on whatever stream exists
        print(err, file=sys.stderr or io.StringIO())
        raise


if __name__ == "__main__":
    sys.exit(main())
