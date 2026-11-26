"""Render a bounded Markdown image for a QQ-hosted avatar."""

from urllib.parse import quote

from ...platforms.qq_official_avatars import avatar_dimensions


async def markdown_image(url: str | None) -> str | None:
    dimensions = await avatar_dimensions(url) if url else None
    if not dimensions:
        return None
    width, height = dimensions
    scale = min(1, 480 / width, 420 / height)
    width = max(1, round(width * scale))
    height = max(1, round(height * scale))
    escaped_url = quote(url, safe=":/?&=%+#@!$*,;~-._")
    return f"![群友头像 #{width}px #{height}px]({escaped_url})"
