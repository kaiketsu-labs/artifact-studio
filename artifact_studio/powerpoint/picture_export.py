"""Keep picture pixels and color data while dropping nonvisual metadata."""

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IMAGE_CHUNKS = {
    b"IHDR",
    b"PLTE",
    b"IDAT",
    b"IEND",
    b"tRNS",
    b"gAMA",
    b"cHRM",
    b"sRGB",
    b"iCCP",
    b"sBIT",
    b"pHYs",
    b"cICP",
    b"mDCV",
    b"cLLI",
}


def _png(data: bytes) -> bytes:
    parts = [_PNG_SIGNATURE]
    index = len(_PNG_SIGNATURE)
    while index + 12 <= len(data):
        length = int.from_bytes(data[index : index + 4], "big")
        chunk = data[index + 4 : index + 8]
        end = index + 12 + length
        if end > len(data):
            break
        if chunk in _PNG_IMAGE_CHUNKS:
            parts.append(data[index:end])
        if chunk == b"IEND":
            return b"".join(parts)
        index = end
    raise ValueError("PowerPoint export could not read an imported PNG.")


def _jpeg(data: bytes) -> bytes:
    parts = [b"\xff\xd8"]
    invalid = "PowerPoint export could not read an imported JPEG."
    index = 2
    while index + 1 < len(data) and data[index] == 0xFF:
        start = index
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index == len(data):
            break
        marker = data[index]
        index += 1
        if marker == 0xD9:
            return b"".join(parts) + b"\xff\xd9"
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            parts.append(data[start:index])
            continue
        length = int.from_bytes(data[index : index + 2], "big")
        end = index + length
        if length < 2 or end > len(data) or marker in (0x00, 0xD8):
            break
        body = data[index + 2 : end]
        if marker == 0xE0 and len(body) >= 14 and body.startswith(b"JFIF\x00"):
            # Preserve color interpretation and scan density. Omit the optional thumbnail.
            parts.append(b"\xff\xe0\x00\x10" + body[:12] + b"\x00\x00")
        elif (
            marker == 0xE2
            and body.startswith(b"ICC_PROFILE\x00")
            or marker == 0xEE
            and body.startswith(b"Adobe")
            and len(body) == 12
            or marker not in range(0xE0, 0xF0)
            and marker != 0xFE
        ):
            parts.append(data[start:end])
        index = end
        if marker == 0xDA:
            # Copy entropy-coded data up to the next non-stuffed marker.
            while (index := data.find(b"\xff", index)) >= 0 and index + 1 < len(data):
                following = index + 1
                while following < len(data) and data[following] == 0xFF:
                    following += 1
                if following == len(data):
                    raise ValueError(invalid)
                code = data[following]
                if code != 0x00 and not 0xD0 <= code <= 0xD7:
                    parts.append(data[end:index])
                    break
                index = following + 1
            else:
                raise ValueError(invalid)
    raise ValueError(invalid)


def audience_picture(data: bytes, image_type: str) -> bytes:
    """Remove picture comments, EXIF, thumbnails and trailing bytes without re-encoding."""
    if image_type == "png" and data.startswith(_PNG_SIGNATURE):
        return _png(data)
    if image_type == "jpeg" and data.startswith(b"\xff\xd8"):
        return _jpeg(data)
    raise ValueError("PowerPoint export found an unsupported imported picture.")
