"""Private, immutable local object adapter for the single-host pilot."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, UnidentifiedImageError

from .errors import Problem

MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


class LocalAssetStore:
    def __init__(self, root: Path | None):
        self.root = Path(root).expanduser().resolve() if root else None

    def path(self, asset_id: str) -> Path:
        if self.root is None:
            raise Problem(503, "service", "storage_unconfigured", "图片存储尚未配置。")
        # Asset IDs are minted by the service. Never use a client file name.
        if not asset_id.startswith("asset_") or not asset_id[6:].isalnum():
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise Problem(503, "service", "storage_error", "图片存储暂不可用。") from None
        return self.root / asset_id

    @staticmethod
    def verify(content: bytes, document: dict) -> None:
        if len(content) != document["byte_size"] or len(content) > MAX_ASSET_BYTES or hashlib.sha256(content).hexdigest() != document["sha256"]:
            raise Problem(422, "validation", "asset_integrity_mismatch", "图片字节数或 SHA256 与上传声明不一致。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.width * image.height > MAX_IMAGE_PIXELS or image.size != (document["width"], document["height"]):
                        raise ValueError("dimensions")
                    if image.get_format_mimetype() != document["mime_type"] or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("format")
                    if image.getexif().get(274, 1) != 1:
                        raise ValueError("noncanonical_orientation")
                    image.load()  # A valid header alone is not a valid image.
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()  # PNG requires this directly after a fresh open.
        except (OSError, ValueError, RuntimeError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise Problem(422, "validation", "invalid_image", "图片不能完整解码，或格式、尺寸与声明不一致。") from None

    def put(self, asset_id: str, content: bytes, document: dict) -> None:
        self.verify(content, document)
        target = self.path(asset_id)
        if target.exists():
            self.verify(self.read(asset_id, document), document)
            return
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        except OSError:
            raise Problem(503, "service", "storage_error", "图片存储暂不可用。") from None
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Link is atomic and will not overwrite an immutable existing asset.
            try:
                os.link(temporary, target)
            except FileExistsError:
                self.verify(self.read(asset_id, document), document)
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise Problem(503, "service", "storage_error", "图片写入失败，请使用原票据重试。") from None
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read(self, asset_id: str, document: dict) -> bytes:
        path = self.path(asset_id)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(MAX_ASSET_BYTES + 1)
        except OSError:
            raise Problem(503, "service", "storage_error", "图片尚未写入或存储暂不可用。") from None
        self.verify(content, document)
        return content
