"""Determine the display size of remote QQ avatars without downloading them."""

from urllib.parse import quote, urlsplit

import aiohttp
from PIL import Image, ImageFile


async def markdown_image(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        # Only probe public QQ avatar CDNs, and never follow redirects.
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            return None
        if not (
            host.endswith((".qlogo.cn", ".gtimg.cn"))
            or host in {"qlogo.cn", "gtimg.cn"}
        ):
            return None
        parser = ImageFile.Parser()
        total = 0
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session,
            session.get(url, allow_redirects=False) as response,
        ):
            if response.status != 200:
                return None
            async for chunk in response.content.iter_chunked(8192):
                total += len(chunk)
                if total > 131072:
                    return None
                parser.feed(chunk)
                if parser.image:
                    width, height = parser.image.size
                    break
            else:
                return None
        if width < 1 or height < 1:
            return None
        scale = min(1, 480 / width, 420 / height)
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
        escaped_url = quote(url, safe=":/?&=%+#@!$*,;~-._")
        return f"![群友头像 #{width}px #{height}px]({escaped_url})"
    except (
        aiohttp.ClientError,
        TimeoutError,
        ValueError,
        OSError,
        Image.DecompressionBombError,
    ):
        return None
