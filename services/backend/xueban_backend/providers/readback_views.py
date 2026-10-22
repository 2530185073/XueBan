"""Optional context views for blind reading after the original whole-page pass.

Zones are coarse attention hints, never question boundaries or evidence of a
correct answer. The original bytes always remain image 0. No OCR, generated
content, contrast edits or question-specific answer hints are involved.
"""
import base64
import hashlib
import io

from PIL import Image


ZONES = ("top_left", "top_right", "middle_left", "middle_right", "bottom_left", "bottom_right")
_ROWS = {"top": (0, .42), "middle": (.29, .71), "bottom": (.58, 1)}


def readback_views(image, zones=()):
    original = (image.data_url,)
    if min(image.width_px, image.height_px) < 100:
        return original, []
    raw = base64.b64decode(image.data_url.split(",", 1)[1])
    try:
        source = Image.open(io.BytesIO(raw))
        if source.size != (image.width_px, image.height_px):
            return original, []
        source.load()
    except (OSError, ValueError):
        return original, []
    chosen = list(dict.fromkeys(zone for zone in zones if zone in ZONES))
    # A missing/disagreeing zone must not block reading. Include all page rows
    # within the existing four-image transport bound, retaining full context.
    if not chosen or len(chosen) > 3:
        chosen = ["top", "middle", "bottom"]
    urls, manifests, total = [image.data_url], [], len(raw)
    with source:
        page = source.convert("RGB")
        for zone in chosen:
            row, _, side = zone.partition("_")
            y0, y1 = _ROWS[row]
            x0, x1 = (0, .6) if side == "left" else (.4, 1) if side == "right" else (0, 1)
            box = (int(x0 * page.width), int(y0 * page.height),
                   round(x1 * page.width), round(y1 * page.height))
            view = page.crop(box)
            scale = min(3, 2048 / max(view.size))
            # Downscaling an already detailed view provides no extra evidence.
            if scale > 1:
                view = view.resize((round(view.width * scale), round(view.height * scale)),
                                   Image.Resampling.LANCZOS)
            stream = io.BytesIO()
            view.save(stream, format="JPEG", quality=95)
            blob = stream.getvalue()
            if len(blob) > 2_097_152 or total + len(blob) > 8_388_608:
                continue
            total += len(blob)
            manifests.append({
                "image_index": len(urls), "asset_id": image.asset_id, "zone": zone,
                "box_original_pixels": list(box), "width_px": view.width, "height_px": view.height,
                "sha256": hashlib.sha256(blob).hexdigest(), "transform": "crop_resize_lanczos",
            })
            urls.append("data:image/jpeg;base64," + base64.b64encode(blob).decode())
    return tuple(urls), manifests
