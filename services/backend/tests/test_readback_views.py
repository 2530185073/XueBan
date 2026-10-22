import base64
import hashlib
import io

from PIL import Image

from xueban_backend.providers.ark import _images
from xueban_backend.providers.correction_pipeline import PipelineImage
from xueban_backend.providers.readback_views import readback_views


def page(width=600, height=1000):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "#ab7832").save(stream, format="PNG")
    return PipelineImage("asset_original", "data:image/png;base64," +
                         base64.b64encode(stream.getvalue()).decode(), width, height)


def test_optional_views_preserve_original_and_transport_bounds():
    source = page()
    urls, views = readback_views(source, ("top_left", "top_left", "middle_left", None))
    assert urls[0] == source.data_url
    assert len(urls) == 3 and [v["zone"] for v in views] == ["top_left", "middle_left"]
    assert len(_images(urls)) == 3
    for n, view in enumerate(views, 1):
        assert view["image_index"] == n and view["asset_id"] == source.asset_id
        blob = base64.b64decode(urls[n].split(",", 1)[1])
        assert hashlib.sha256(blob).hexdigest() == view["sha256"]
        with Image.open(io.BytesIO(blob)) as decoded:
            assert decoded.size == (view["width_px"], view["height_px"])
            # Existing color/pixels only; no overlays that could become text.
            assert all(abs(a - b) < 3 for a, b in zip(decoded.getpixel((10, 10)), (171, 120, 50)))


def test_unknown_zones_fall_back_to_overlapping_full_width_views():
    source = page()
    for zones in ((), ("wrong",)):
        urls, views = readback_views(source, zones)
        assert len(urls) == 4 and [v["zone"] for v in views] == ["top", "middle", "bottom"]
        boxes = [v["box_original_pixels"] for v in views]
        assert all(b[0] == 0 and b[2] == 600 for b in boxes)
        assert boxes[0][1] == 0 and boxes[-1][3] == 1000
        assert boxes[0][3] > boxes[1][1] and boxes[1][3] > boxes[2][1]
        assert len(_images(urls)) == 4


def test_more_than_three_requested_zones_cover_every_row_instead_of_dropping_the_tail():
    urls, views = readback_views(page(), ("top_left", "bottom_right", "middle_left", "top_right"))
    assert len(urls) == 4
    assert [v["zone"] for v in views] == ["top", "middle", "bottom"]
    assert all(v["box_original_pixels"][2] - v["box_original_pixels"][0] == 600 for v in views)


def test_small_critical_only_request_retains_focused_detail_views():
    _, views = readback_views(page(), ("bottom_right", "top_left"))
    assert [v["zone"] for v in views] == ["bottom_right", "top_left"]
    assert all(v["box_original_pixels"][2] - v["box_original_pixels"][0] < 600 for v in views)


def test_tiny_or_dimension_mismatched_images_never_get_invented_views():
    source = page(10, 10)
    assert readback_views(source, ("top_left",)) == ((source.data_url,), [])
    mismatch = PipelineImage(source.asset_id, source.data_url, 600, 1000)
    assert readback_views(mismatch, ("top_left",)) == ((source.data_url,), [])
