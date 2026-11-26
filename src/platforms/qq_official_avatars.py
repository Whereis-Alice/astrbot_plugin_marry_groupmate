"""Bounded QQ avatar lookup with stock-image detection."""

from collections import OrderedDict
from hashlib import sha256
from time import monotonic
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageFile

# QQ's avatar CDN returns these images with HTTP 200 for unknown members.
_STOCK_SIZES = {(40, 40), (100, 100)}
_STOCK_DIGESTS = {
    1512: "d3b86c828178ce7a598e86eb74c8dc1b1c3948f9cbd01aece8eeb3915a7dcc06",
    4208: "20005763993af7e0ed37add100182e29c804d48f439befe320c3b817e25b2afa",
}
_MAX_STOCK_BYTES = max(_STOCK_DIGESTS)
_MAX_PROBE_BYTES = 131072
_CACHE_LIMIT = 512
_cache: OrderedDict[str, tuple[float, tuple[int, int] | None]] = OrderedDict()


async def _probe(url: str) -> tuple[int, int] | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            return None
        if host not in {"qlogo.cn", "gtimg.cn"} and not host.endswith(
            (".qlogo.cn", ".gtimg.cn")
        ):
            return None

        parser = ImageFile.Parser()
        data = bytearray()
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session,
            session.get(url, allow_redirects=False) as response,
        ):
            if (
                response.status != 200
                or response.headers.get("X-FailNo") == "-1208"
                or response.headers.get("X-Info", "").lower().startswith("notexist:")
            ):
                return None
            async for chunk in response.content.iter_chunked(8192):
                if len(data) + len(chunk) > _MAX_PROBE_BYTES:
                    return None
                data.extend(chunk)
                parser.feed(chunk)
                if parser.image and (
                    parser.image.size not in _STOCK_SIZES
                    or len(data) > _MAX_STOCK_BYTES
                ):
                    return parser.image.size

        if not parser.image:
            return None
        stock_digest = _STOCK_DIGESTS.get(len(data))
        if stock_digest and sha256(data).hexdigest() == stock_digest:
            return None
        return parser.image.size
    except (
        aiohttp.ClientError,
        TimeoutError,
        ValueError,
        OSError,
        Image.DecompressionBombError,
    ):
        return None


async def avatar_dimensions(url: str) -> tuple[int, int] | None:
    """Return the size of a QQ-hosted avatar when the CDN did not send a placeholder."""
    now = monotonic()
    cached = _cache.get(url)
    if cached and now < cached[0]:
        _cache.move_to_end(url)
        return cached[1]

    dimensions = await _probe(url)
    # The Markdown renderer reuses the probe performed during member lookup.
    _cache[url] = monotonic() + (300 if dimensions else 60), dimensions
    _cache.move_to_end(url)
    if len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)
    return dimensions
