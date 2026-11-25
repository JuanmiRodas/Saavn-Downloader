"""Generate a crisp macOS .iconset (pure stdlib PNG encoder) for Saavn-Downloader Studio."""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib


def _write_png(path: str, width: int, height: int, rgba_rows: list[bytes]) -> None:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + row for row in rgba_rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, level=6))
        + _chunk(b"IEND", b"")
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)


def render_icon(size: int) -> list[bytes]:
    """Render an archival vinyl-disc squircle icon at the requested square pixel size."""
    rows: list[bytes] = []
    half = size / 2.0
    pad = size * 0.08
    inner_half = half - pad
    corner_r = size * 0.21

    disc_r = size * 0.34
    label_r = size * 0.135
    hole_r = size * 0.032

    for y in range(size):
        row = bytearray(size * 4)
        dy = y + 0.5 - half
        ay = abs(dy)
        for x in range(size):
            dx = x + 0.5 - half
            ax = abs(dx)

            # Rounded-rect signed distance
            qx = max(ax - (inner_half - corner_r), 0.0)
            qy = max(ay - (inner_half - corner_r), 0.0)
            sdf_box = math.hypot(qx, qy) - corner_r
            if sdf_box > 1.0:
                continue

            alpha = int(max(0.0, min(1.0, 0.5 - sdf_box)) * 255)
            # Base warm charcoal background with subtle top-to-bottom depth
            t = y / max(1, size - 1)
            r = int(28 + 12 * (1.0 - t))
            g = int(26 + 10 * (1.0 - t))
            b = int(24 + 8 * (1.0 - t))

            dist = math.hypot(dx, dy)
            if dist <= disc_r:
                # Subtle vinyl grooves
                groove = 1.0 + 0.07 * math.sin(dist / max(1.0, size * 0.008))
                # Terracotta vermilion vinyl outer ring + deep slate grooves
                if dist > label_r:
                    r = min(255, int(199 * groove))
                    g = min(255, int(75 * groove))
                    b = min(255, int(47 * groove))
                    # Subtle diagonal sheen
                    sheen = max(0.0, 1.0 - abs(dx + dy) / (disc_r * 0.55)) * 0.14
                    r = min(255, int(r + (255 - r) * sheen))
                    g = min(255, int(g + (255 - g) * sheen))
                    b = min(255, int(b + (255 - b) * sheen))
                elif dist > hole_r:
                    # Warm alabaster center label
                    r, g, b = 246, 244, 239
                else:
                    # Spindle hole
                    r, g, b = 26, 25, 23

            idx = x * 4
            row[idx] = r
            row[idx + 1] = g
            row[idx + 2] = b
            row[idx + 3] = alpha
        rows.append(bytes(row))
    return rows


def build_iconset(iconset_dir: str) -> None:
    os.makedirs(iconset_dir, exist_ok=True)
    specs = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
    ]
    for filename, sz in specs:
        _write_png(os.path.join(iconset_dir, filename), sz, sz, render_icon(sz))
    # Copy 512 as 512@2x? Better: render 512@2x at 1024 only if fast; 512x512 renders in <0.4s, 1024 in ~1.2s
    _write_png(os.path.join(iconset_dir, "icon_512x512@2x.png"), 1024, 1024, render_icon(1024))


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join("packaging", "SaavnDownloader.iconset")
    build_iconset(target)
    print(f"Generated macOS iconset at {target}")
