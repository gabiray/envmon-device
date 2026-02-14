import subprocess
from pathlib import Path


def capture_image(
    output_path: str,
    width: int = 1280,
    height: int = 720,
    timeout_ms: int = 500,
    quality: int = 85,
):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rpicam-still",
        "-o", output_path,
        "--width", str(width),
        "--height", str(height),
        "--timeout", str(timeout_ms),
        "--nopreview",
        "--quality", str(quality),
    ]

    subprocess.run(cmd, check=True)
