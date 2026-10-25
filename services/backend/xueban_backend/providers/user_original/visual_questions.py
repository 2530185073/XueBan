"""Local, bounded question crops and diagram observation contracts."""
import base64
import binascii
import io
import math
import warnings

from PIL import Image, ImageDraw, UnidentifiedImageError

from .ark import ProviderError


def layout_guide(image):
    """A normalized coordinate ruler for localization only; OCR uses clean pixels."""
    if min(image.width_px, image.height_px) < 100:
        return image.data_url
    try:
        with Image.open(io.BytesIO(base64.b64decode(image.data_url.split(',', 1)[1], validate=True))) as source:
            if source.size != (image.width_px, image.height_px) or source.width * source.height > 24_000_000:
                raise ValueError('dimensions')
            guided = source.convert('RGB')
        draw = ImageDraw.Draw(guided, 'RGBA')
        for value in range(100, 1000, 100):
            x, y = guided.width * value / 1000, guided.height * value / 1000
            draw.line([(x, 0), (x, guided.height)], fill=(0, 90, 255, 85), width=1)
            draw.line([(0, y), (guided.width, y)], fill=(0, 90, 255, 85), width=1)
            for point, label in [((x + 2, 3), str(value)), ((3, y + 2), str(value))]:
                draw.rectangle(draw.textbbox(point, label), fill=(255, 255, 255, 230))
                draw.text(point, label, fill=(0, 60, 220, 255))
        stream = io.BytesIO()
        guided.save(stream, format='JPEG', quality=90)
        return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode()
    except (ValueError, IndexError, binascii.Error, OSError, Image.DecompressionBombError):
        raise ProviderError('invalid_image') from None


def complete_layout_regions(items, images):
    """Protect labels/answers omitted by an overly tight model box.

    Expand into nearby whitespace, bounded by adjacent columns and the next
    question's top. This is context padding, not a new model segmentation.
    """
    completed = []
    for item in items:
        box = item['region']
        source = images[box['asset_id']]
        left = max(0, box['x'] - box['width'] * .2)
        right = min(source.width_px, box['x'] + box['width'] * 1.2)
        bottom = min(source.height_px, box['y'] + box['height'] * 1.35)
        for other in items:
            if other is item or other['page'] != item['page']:
                continue
            adjacent = other['region']
            vertical_overlap = min(box['y'] + box['height'], adjacent['y'] + adjacent['height']) - max(box['y'], adjacent['y'])
            horizontal_overlap = min(box['x'] + box['width'], adjacent['x'] + adjacent['width']) - max(box['x'], adjacent['x'])
            if vertical_overlap > min(box['height'], adjacent['height']) * .5:
                gutter = (box['x'] + box['width'] / 2 + adjacent['x'] + adjacent['width'] / 2) / 2
                if adjacent['x'] >= box['x'] + box['width']:
                    right = min(right, max(box['x'] + box['width'], gutter))
                elif box['x'] >= adjacent['x'] + adjacent['width']:
                    left = max(left, min(box['x'], gutter))
            if horizontal_overlap > min(box['width'], adjacent['width']) * .5 and adjacent['y'] >= box['y'] + box['height']:
                gap = adjacent['y'] - box['y'] - box['height']
                bottom = min(bottom, adjacent['y'] - min(8, gap / 2))
        completed.append({**item, 'region': {**box, 'x': left, 'width': right - left, 'height': bottom - box['y']}})
    return completed


def crop_question(image, regions):
    """Return a crop in memory; all stored regions remain in original pixels.

    A small margin protects brackets and labels. Upsampling at most twice can
    help the model attend to small print, but cannot restore lost detail.
    """
    left = max(0, math.floor(min(r['x'] for r in regions)) - 8)
    top = max(0, math.floor(min(r['y'] for r in regions)) - 8)
    right = min(image.width_px, math.ceil(max(r['x'] + r['width'] for r in regions)) + 8)
    bottom = min(image.height_px, math.ceil(max(r['y'] + r['height'] for r in regions)) + 8)
    if (left, top, right, bottom) == (0, 0, image.width_px, image.height_px):
        return image.data_url, image.width_px, image.height_px
    try:
        content = base64.b64decode(image.data_url.split(',', 1)[1], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.size != (image.width_px, image.height_px) or source.width * source.height > 24_000_000:
                    raise ValueError('dimensions')
                if not (left < right and top < bottom):
                    raise ValueError('empty_crop')
                crop = source.crop((left, top, right, bottom)).convert('RGB')
        scale = min(2, 1600 / max(crop.size))
        if scale != 1:
            crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        crop.save(stream, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode(), crop.width, crop.height
    except (ValueError, IndexError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProviderError('invalid_image') from None


def reading_order(items):
    """Group nearly aligned, horizontally separate boxes before sorting left to right.

    Use an anchored row (not a pairwise fuzzy comparator), so ordering stays
    transitive and a series of staggered boxes cannot join separate rows.
    """
    rows = []
    for item in sorted(items, key=lambda i: (i['page'], i['region']['y'], i['region']['x'])):
        box = item['region']
        row = next((row for row in reversed(rows) if row[0]['page'] == item['page']
                    and abs(box['y'] - row[0]['region']['y']) <= min(box['height'], row[0]['region']['height']) * .15
                    and all(box['x'] >= other['region']['x'] + other['region']['width'] * .8
                            or other['region']['x'] >= box['x'] + box['width'] * .8 for other in row)), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    return [item for row in rows for item in sorted(row, key=lambda i: i['region']['x'])]
