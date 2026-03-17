"""
server/image_utils.py — Image compression utilities for reducing token count.

Compresses screenshots before sending to Gemini to stay within token budgets.

Gemini token cost for images is roughly proportional to pixel area.
A 1920x1080 image at 85% JPEG ≈ ~200-400KB ≈ ~100K-200K tokens.
Target: keep each screenshot under ~150KB / ~75K tokens.
"""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults (override via environment) ───────────────────────────────────────

# Max dimension on the longer axis (maintains aspect ratio)
MAX_SCREENSHOT_WIDTH  = int(os.environ.get("MAX_SCREENSHOT_WIDTH", "1280"))
# Target max file size in bytes — compressor will step down quality to hit this
MAX_SCREENSHOT_BYTES  = int(os.environ.get("MAX_SCREENSHOT_BYTES", str(200 * 1024)))   # 200KB
# Initial JPEG quality
SCREENSHOT_QUALITY    = int(os.environ.get("SCREENSHOT_QUALITY", "80"))


def compress_screenshot(
    image_bytes: bytes,
    max_width: int = MAX_SCREENSHOT_WIDTH,
    quality: int = SCREENSHOT_QUALITY,
    max_bytes: int = MAX_SCREENSHOT_BYTES,
) -> tuple[bytes, str]:
    """
    Compress a screenshot (PNG or JPEG) to reduce Gemini token usage.

    Strategy:
    1. Resize to max_width if wider (maintains aspect ratio)
    2. Convert to JPEG at `quality`
    3. If still over max_bytes, step down quality in increments until within budget
    4. Fallback: return original bytes as-is if PIL unavailable

    Args:
        image_bytes: Raw image bytes (PNG or JPEG)
        max_width:   Maximum width in pixels
        quality:     Initial JPEG quality (1-100)
        max_bytes:   Target maximum file size in bytes

    Returns:
        (compressed_bytes, mime_type)
    """
    try:
        from PIL import Image

        original_size = len(image_bytes)
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB (strips alpha, required for JPEG)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # Resize: cap width at max_width, maintain aspect ratio
        if img.width > max_width:
            ratio = max_width / img.width
            new_h = int(img.height * ratio)
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

        # Also cap height at 1.5× width (ultra-tall screenshots waste tokens)
        max_height = int(img.width * 1.5)
        if img.height > max_height:
            img = img.crop((0, 0, img.width, max_height))

        # Compress at initial quality, step down if over budget
        current_quality = quality
        compressed_bytes = b""
        for _ in range(6):  # max 6 quality steps
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=current_quality, optimize=True)
            compressed_bytes = buf.getvalue()
            if len(compressed_bytes) <= max_bytes or current_quality <= 30:
                break
            current_quality = max(30, current_quality - 10)

        reduction = 100 * (1 - len(compressed_bytes) / max(original_size, 1))
        logger.info(
            "Screenshot: %dKB → %dKB (%.0f%% reduction, q=%d, %dx%d)",
            original_size // 1024,
            len(compressed_bytes) // 1024,
            reduction,
            current_quality,
            img.width,
            img.height,
        )

        return compressed_bytes, "image/jpeg"

    except ImportError:
        logger.warning("PIL not available — returning uncompressed image")
        return image_bytes, "image/png"
    except Exception as e:
        logger.warning("compress_screenshot failed: %s", e)
        return image_bytes, "image/png"


def get_compressed_dimensions(
    width: int,
    height: int,
    max_width: int = MAX_SCREENSHOT_WIDTH,
) -> tuple[int, int]:
    """Calculate resized dimensions maintaining aspect ratio."""
    if width <= max_width:
        return width, height
    ratio = max_width / width
    return max_width, int(height * ratio)
