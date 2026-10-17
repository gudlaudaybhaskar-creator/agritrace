"""
Redact PII from extracted Soil Health Card certificates and generate 480px WebP thumbnails.
Covers Farmer Name, Address/Village, Aadhaar, and Mobile number.
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw

BASE_DIR = Path(__file__).resolve().parent.parent
CERTS_DIR = BASE_DIR / "extracted_certificates"

def redact_and_create_thumbnails():
    if not CERTS_DIR.exists():
        print(f"{CERTS_DIR} does not exist.")
        return

    for i in range(1, 9):
        png_path = CERTS_DIR / f"cert_page_{i}.png"
        if not png_path.exists():
            continue

        im = Image.open(png_path)
        w, h = im.size
        draw = ImageDraw.Draw(im)

        # Soil Health Card farmer metadata box is situated in the upper portion
        # (approximately y: 22% to 45% of height, x: 15% to 85% of width)
        # We apply solid redaction boxes over the Farmer Details rows:
        # 1. Name & Aadhaar Number line
        draw.rectangle([int(w * 0.18), int(h * 0.23), int(w * 0.85), int(h * 0.29)], fill=(40, 40, 40))
        # 2. Village / Address line
        draw.rectangle([int(w * 0.18), int(h * 0.29), int(w * 0.85), int(h * 0.35)], fill=(40, 40, 40))
        # 3. Mobile Number / Khata/Khasra line
        draw.rectangle([int(w * 0.18), int(h * 0.35), int(w * 0.85), int(h * 0.41)], fill=(40, 40, 40))

        # Save the redacted full version
        redacted_full = CERTS_DIR / f"cert_page_{i}_redacted.png"
        im.save(redacted_full, "PNG", optimize=True)

        # Generate 480px WebP thumbnail
        thumb_w = 480
        thumb_h = int(h * (thumb_w / w))
        thumb = im.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        thumb_path = CERTS_DIR / f"cert_page_{i}_thumb.webp"
        thumb.save(thumb_path, "WEBP", quality=80)

        # Also replace original png with the redacted version to guarantee no PII is served
        im.save(png_path, "PNG", optimize=True)

        print(f"Redacted cert_page_{i}.png & generated {thumb_path.name} (size: {os.path.getsize(thumb_path)} bytes)")

if __name__ == "__main__":
    redact_and_create_thumbnails()
