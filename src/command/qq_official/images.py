"""Set the QQ Markdown display dimensions while linking to the source image."""

from urllib.parse import quote

from ...platforms.qq_official_avatars import avatar_dimensions

MAX_DISPLAY_WIDTH = 128
MAX_DISPLAY_HEIGHT = 220


async def markdown_image(url: str | None) -> str | None:
    dimensions = await avatar_dimensions(url) if url else None
    if not dimensions:
        return None
    width, height = dimensions
    scale = min(1, MAX_DISPLAY_WIDTH / width, MAX_DISPLAY_HEIGHT / height)
    display_width = max(1, round(width * scale))
    display_height = max(1, round(height * scale))
    escaped_url = quote(url, safe=":/?&=%+#@!$*,;~-._")
    return f"![群友头像 #{display_width}px #{display_height}px]({escaped_url})"
