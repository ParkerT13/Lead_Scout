"""
Generate ContactPuller app logo / icon.

Saves:
  assets/logo.svg   — scalable source
  assets/logo.png   — 256x256 for display / splash
  assets/logo.ico   — multi-size Windows icon (16/32/48/64/128/256)

Run once from the project root:
    python generate_logo.py

Requires PySide6 (already installed) and Pillow:
    pip install pillow
"""

import sys
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)

# ── SVG source ────────────────────────────────────────────────────────────────
# Design: dark navy rounded square, orange target crosshair, white contact
# silhouette at centre.  Reads well at every size from 16×16 to 256×256.

SVG_SOURCE = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">

  <!-- Background: dark navy, rounded corners -->
  <rect width="100" height="100" rx="18" ry="18" fill="#0F172A"/>

  <!-- Subtle inner glow ring behind person -->
  <circle cx="50" cy="50" r="26" fill="#1E3A5F" opacity="0.6"/>

  <!-- Outer target / crosshair ring -->
  <circle cx="50" cy="50" r="32" fill="none" stroke="#F97316" stroke-width="4.5"/>

  <!-- Crosshair ticks (4 compass points) -->
  <line x1="50" y1="10" x2="50" y2="21" stroke="#F97316" stroke-width="4" stroke-linecap="round"/>
  <line x1="50" y1="79" x2="50" y2="90" stroke="#F97316" stroke-width="4" stroke-linecap="round"/>
  <line x1="10" y1="50" x2="21" y2="50" stroke="#F97316" stroke-width="4" stroke-linecap="round"/>
  <line x1="79" y1="50" x2="90" y2="50" stroke="#F97316" stroke-width="4" stroke-linecap="round"/>

  <!-- Person silhouette — head -->
  <circle cx="50" cy="42" r="9.5" fill="white"/>

  <!-- Person silhouette — shoulders -->
  <path d="M30,67 Q30,54 50,54 Q70,54 70,67 Z" fill="white"/>

  <!-- Tiny "pull" arrow at bottom-right corner (data pull metaphor) -->
  <circle cx="79" cy="79" r="10" fill="#F97316"/>
  <path d="M75,75 L83,75 L83,83" fill="none" stroke="white"
        stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <line x1="75" y1="83" x2="83" y2="75" stroke="white"
        stroke-width="2.5" stroke-linecap="round"/>

</svg>
"""

svg_path = ASSETS / "logo.svg"
svg_path.write_text(SVG_SOURCE, encoding="utf-8")
print(f"[+] SVG  -> {svg_path}")

# ── Render to PNG and ICO via PySide6 ────────────────────────────────────────

app = None
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage, QPainter, QIcon, QPixmap
    from PySide6.QtCore import QByteArray, Qt

    app = QApplication.instance() or QApplication(sys.argv)

    svg_bytes = QByteArray(SVG_SOURCE.encode("utf-8"))
    renderer  = QSvgRenderer(svg_bytes)

    def render_size(px: int) -> QImage:
        img = QImage(px, px, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p)
        p.end()
        return img

    # 256×256 PNG
    big = render_size(256)
    png_path = str(ASSETS / "logo.png")
    big.save(png_path, "PNG")
    print(f"[+] PNG  -> {png_path}")

    # Multi-size ICO via Pillow
    try:
        from PIL import Image as PILImage
        import io

        ico_sizes = [16, 32, 48, 64, 128, 256]
        pil_images = []
        for sz in ico_sizes:
            img_qt = render_size(sz)
            buf = img_qt.bits().tobytes()
            pil_img = PILImage.frombytes("RGBA", (sz, sz), buf, "raw", "BGRA")
            pil_images.append(pil_img)

        ico_path = str(ASSETS / "logo.ico")
        pil_images[0].save(
            ico_path, format="ICO",
            sizes=[(s, s) for s in ico_sizes],
            append_images=pil_images[1:],
        )
        print(f"[+] ICO  -> {ico_path}")
    except ImportError:
        print("[!] Pillow not installed — skipping ICO generation.")
        print("    Run: pip install pillow   then re-run this script.")

except Exception as e:
    print(f"[!] Logo generation failed: {e}")
    import traceback; traceback.print_exc()

print("\nDone. Run 'Create Desktop Shortcut.bat' to pin the app to your desktop.")
