"""Conservative full-page detail enhancement; no crops, boxes or generated pixels."""
import base64
from dataclasses import replace
import io

from PIL import Image, ImageFilter, ImageOps


def detailed_page(image):
    # Keep tiny fixtures and already large photos unchanged. Upsampling is
    # bounded and only makes the existing strokes easier for vision encoders.
    if min(image.width_px, image.height_px) < 100 or max(image.width_px, image.height_px) >= 2400:
        return image
    with Image.open(io.BytesIO(base64.b64decode(image.data_url.split(',', 1)[1]))) as source:
        if source.size != (image.width_px, image.height_px):
            return image
        page = ImageOps.autocontrast(source.convert('RGB'), cutoff=0.5)
        scale = min(2, 2400 / max(page.size))
        page = page.resize((round(page.width * scale), round(page.height * scale)), Image.Resampling.LANCZOS)
        page = page.filter(ImageFilter.UnsharpMask(radius=1, percent=110, threshold=4))
        stream = io.BytesIO()
        page.save(stream, format='JPEG', quality=95)
    if len(stream.getvalue()) > 2 * 1024 * 1024:
        return image
    return replace(image, data_url='data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode(),
                   width_px=page.width, height_px=page.height)
