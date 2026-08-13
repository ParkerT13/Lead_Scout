"""
Generate ContactPuller app logo / icon.

Saves:
  assets/logo.svg   -- scalable source
  assets/logo.png   -- 512x512 for display / splash
  assets/logo.ico   -- multi-size Windows icon (16/32/48/64/128/256)

Run once from the project root:
    python generate_logo.py

Requires PySide6 (already installed) and Pillow:
    pip install pillow
"""

import sys
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)

# ── SVG source ─────────────────────────────────────────────────────────────────
# Bold, clean design that reads at every size from 16x16 to 512x512.
# Strokes and shapes are intentionally thick so nothing disappears when small.

SVG_SOURCE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">

  <!-- Background: deep navy, rounded corners -->
  <rect width="100" height="100" rx="16" ry="16" fill="#0F172A"/>

  <!-- Subtle radial glow behind person -->
  <radialGradient id="glow" cx="50%" cy="50%" r="50%">
    <stop offset="0%"   stop-color="#1E40AF" stop-opacity="0.55"/>
    <stop offset="100%" stop-color="#0F172A" stop-opacity="0"/>
  </radialGradient>
  <circle cx="50" cy="50" r="44" fill="url(#glow)"/>

  <!-- Outer target ring — bold -->
  <circle cx="50" cy="50" r="34" fill="none" stroke="#F97316" stroke-width="5.5"/>

  <!-- Inner target ring — thinner accent -->
  <circle cx="50" cy="50" r="26" fill="none" stroke="#F97316" stroke-width="1.8" opacity="0.4"/>

  <!-- Crosshair ticks — bold and well-spaced -->
  <line x1="50" y1="4"  x2="50" y2="17" stroke="#F97316" stroke-width="5" stroke-linecap="round"/>
  <line x1="50" y1="83" x2="50" y2="96" stroke="#F97316" stroke-width="5" stroke-linecap="round"/>
  <line x1="4"  y1="50" x2="17" y2="50" stroke="#F97316" stroke-width="5" stroke-linecap="round"/>
  <line x1="83" y1="50" x2="96" y2="50" stroke="#F97316" stroke-width="5" stroke-linecap="round"/>

  <!-- Person silhouette — head (larger, bold) -->
  <circle cx="50" cy="40" r="10" fill="#FFFFFF"/>

  <!-- Person silhouette — body / shoulders (clean arc) -->
  <path d="M28,68 C28,54 72,54 72,68 Z" fill="#FFFFFF"/>

  <!-- Pull-arrow badge — bottom-right, bold and readable -->
  <circle cx="78" cy="78" r="12" fill="#F97316"/>
  <!-- Down-right arrow -->
  <polyline points="72,72 80,72 80,80" fill="none" stroke="#FFFFFF"
            stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <line x1="72" y1="80" x2="80" y2="72" stroke="#FFFFFF"
        stroke-width="3" stroke-linecap="round"/>

</svg>
"""

svg_path = ASSETS / "logo.svg"
svg_path.write_text(SVG_SOURCE, encoding="utf-8")
print(f"[+] SVG  -> {svg_path}")

# ── Render via PySide6 at 1024x1024, then LANCZOS-downscale all outputs ────────
# Rendering at high resolution then downscaling eliminates the pixelation that
# happens when you render SVG directly at small target sizes.

MASTER_PX = 1024  # render everything from this resolution

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QByteArray

    app = QApplication.instance() or QApplication(sys.argv)

    svg_bytes = QByteArray(SVG_SOURCE.encode("utf-8"))
    renderer  = QSvgRenderer(svg_bytes)

    # Render master at high resolution
    master_img = QImage(MASTER_PX, MASTER_PX, QImage.Format_ARGB32)
    master_img.fill(0)
    p = QPainter(master_img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(p)
    p.end()

    # Convert master to Pillow for high-quality downscaling
    from PIL import Image as PILImage

    master_buf = master_img.bits().tobytes()
    master_pil = PILImage.frombytes("RGBA", (MASTER_PX, MASTER_PX), master_buf, "raw", "BGRA")

    # 512x512 PNG
    png_512 = master_pil.resize((512, 512), PILImage.LANCZOS)
    png_path = str(ASSETS / "logo.png")
    png_512.save(png_path, "PNG")
    print(f"[+] PNG  -> {png_path}  (512x512)")

    # Multi-size ICO — let Pillow resize from the 256px image using the sizes= param.
    # Using sizes= on a single image is the only reliable way to embed all resolutions;
    # append_images= silently drops extras in most Pillow builds.
    ico_sizes = [(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)]
    img256 = master_pil.resize((256, 256), PILImage.LANCZOS)

    ico_path = str(ASSETS / "logo.ico")
    img256.save(ico_path, format="ICO", sizes=ico_sizes)
    print(f"[+] ICO  -> {ico_path}  (16/32/48/64/128/256px)")

except ImportError as e:
    print(f"[!] Missing dependency: {e}")
    print("    Run: pip install pillow")
except Exception as e:
    print(f"[!] Logo generation failed: {e}")
    import traceback; traceback.print_exc()

print("\nDone. Run 'Create Desktop Shortcut.bat' to pin the app to your desktop.")
