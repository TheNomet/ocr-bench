"""HTML -> PDF (headless Chrome) -> PNG (pypdfium2), plus a simulated phone-photo variant."""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
CHROME_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for n in CHROME_NAMES:
        if p := shutil.which(n):
            return p
    return None


def html_to_pdf(chrome: str, html_path: Path, pdf_path: Path, timeout: int = 90, attempts: int = 3) -> None:
    """Print `html_path` to `pdf_path`. Retries, because headless Chrome occasionally fails under load."""
    err = ""
    for _ in range(attempts):
        pdf_path.unlink(missing_ok=True)
        try:
            _chrome_print(chrome, html_path, pdf_path, timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            err = (getattr(e, "stderr", b"") or b"").decode("utf-8", "replace")[-2000:]
            continue
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return
    raise RuntimeError(f"Chrome failed to print {html_path} after {attempts} attempts:\n{err}")


def _chrome_print(chrome: str, html_path: Path, pdf_path: Path, timeout: int) -> None:
    with tempfile.TemporaryDirectory(prefix="ocrbench-chrome-") as profile:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def pdf_to_images(pdf_path: Path, scale: float = 2.0) -> list[Image.Image]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return [pdf[i].render(scale=scale).to_pil().convert("RGB") for i in range(len(pdf))]
    finally:
        pdf.close()


def degrade(img: Image.Image, r: random.Random) -> tuple[Image.Image, int]:
    """Phone-photo / bad-scan simulation. Returns (image, jpeg_quality); fully determined by `r`."""
    angle = r.uniform(-2.5, 2.5)
    bg = (r.randint(200, 230), r.randint(195, 222), r.randint(185, 210))
    out = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=bg)
    f = r.uniform(0.6, 0.75)
    out = out.resize((max(1, int(out.width * f)), max(1, int(out.height * f))), Image.Resampling.BILINEAR)
    out = out.filter(ImageFilter.GaussianBlur(r.uniform(0.5, 1.1)))
    # Deterministic grain: random bytes from r, at half resolution, upscaled.
    nw, nh = max(1, out.width // 2), max(1, out.height // 2)
    noise = Image.frombytes("L", (nw, nh), r.randbytes(nw * nh)).resize(out.size, Image.Resampling.BILINEAR)
    out = Image.blend(out, Image.merge("RGB", (noise, noise, noise)), r.uniform(0.06, 0.1))
    # Warm tint plus uneven lighting (darker towards one corner).
    tint = Image.new("RGB", out.size, (250, 238, 210))
    out = Image.blend(out, tint, r.uniform(0.08, 0.15))
    shade = Image.linear_gradient("L")
    if r.random() < 0.5:
        shade = shade.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if r.random() < 0.5:
        shade = shade.transpose(Image.Transpose.ROTATE_90)
    strength = r.uniform(0.1, 0.22)
    mask = shade.resize(out.size, Image.Resampling.BILINEAR).point(lambda v: int(v * strength))
    out = Image.composite(Image.new("RGB", out.size, (60, 50, 40)), out, mask)
    return out, r.randint(35, 50)
