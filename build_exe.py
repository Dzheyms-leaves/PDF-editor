#!/usr/bin/env python3
"""Build a standalone PDF Workbench executable with PyInstaller.

    pip install pyinstaller
    python build_exe.py                # one-folder build (fast start, recommended)
    python build_exe.py --onefile      # single .exe (slower start, easier to hand over)
    python build_exe.py --with-ocr     # bundle the RapidOCR CPU engine too

The result lands in ``dist/``. The heavy GPU stack (torch + DeepSeek-OCR) is
deliberately never bundled — it is multiple gigabytes and machine specific.
A packaged build still picks those up at runtime if they are installed in the
Python environment the user later points it at, and otherwise falls back to
whatever CPU engine is bundled.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "PDFWorkbench"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true",
                        help="Bundle into a single executable")
    parser.add_argument("--with-ocr", action="store_true",
                        help="Bundle the RapidOCR CPU engine and its ONNX models")
    parser.add_argument("--clean", action="store_true", help="Wipe build/ and dist/ first")
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed.  pip install pyinstaller")
        return 1

    if args.clean:
        for folder in ("build", "dist"):
            shutil.rmtree(ROOT / folder, ignore_errors=True)

    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--name", NAME,
        "--noconfirm",
        "--windowed" if sys.platform == "darwin" else "--console",
        "--add-data", f"{ROOT / 'static'}{separator}static",
        # Uvicorn and FastAPI load plenty of things by string name.
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "email.mime.multipart",
        "--collect-submodules", "src",
        # Never drag the GPU stack into the bundle.
        "--exclude-module", "torch",
        "--exclude-module", "transformers",
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
    ]

    if args.onefile:
        command.append("--onefile")

    if args.with_ocr:
        try:
            import rapidocr_onnxruntime as rapid

            models = Path(rapid.__file__).parent
            command += [
                "--collect-data", "rapidocr_onnxruntime",
                "--hidden-import", "onnxruntime",
                "--collect-binaries", "onnxruntime",
            ]
            print(f"[*] Bundling RapidOCR from {models}")
        except ImportError:
            print("[!] --with-ocr given but rapidocr-onnxruntime is not installed; skipping")

    command.append(str(ROOT / "run_app.py"))

    print("[*] " + " ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode

    target = ROOT / "dist" / (f"{NAME}.exe" if sys.platform == "win32" else NAME)
    print("\n" + "=" * 62)
    print(f"  Built: {target if args.onefile else ROOT / 'dist' / NAME}")
    print("  The app writes its workspace to the user's app-data folder,")
    print("  so it can be run from anywhere, including a read-only share.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
