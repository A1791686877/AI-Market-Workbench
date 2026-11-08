from __future__ import annotations

import os

import uvicorn

from app.config import settings


def main() -> None:
    pid_path = settings.root / "tmp" / "uvicorn-live.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=8080)
    finally:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
