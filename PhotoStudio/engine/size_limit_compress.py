"""Compress only oversized images to a strict per-file limit."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from engine.batch_files import safe_copy, safe_destination

ALLOWED_LIMITS = (1, 2, 3, 4, 5)

def _save_bytes(data: bytes, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.photostudio_', dir=output.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.link(temp, output)
    finally:
        Path(temp).unlink(missing_ok=True)

def _encode_webp(image: Image.Image, quality: int, icc: bytes | None) -> bytes:
    buf = io.BytesIO()
    options = {'format': 'WEBP', 'quality': quality, 'method': 4}
    if icc:
        options['icc_profile'] = icc
    image.save(buf, **options)
    return buf.getvalue()

def _encode_lossless(image: Image.Image, icc: bytes | None) -> bytes:
    buf = io.BytesIO()
    options = {'format': 'WEBP', 'lossless': True, 'method': 4}
    if icc:
        options['icc_profile'] = icc
    image.save(buf, **options)
    return buf.getvalue()

def compress_file(source, original_name, folder, max_mb):
    if isinstance(max_mb, bool) or str(max_mb) not in ('1', '2', '3', '4', '5'):
        raise ValueError('Select a maximum file size from 1 to 5 MB')
    limit = int(max_mb) * 1_000_000
    source, folder = Path(source), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    # Files that already fit are copied byte-for-byte.
    if source.stat().st_size <= limit:
        destination = safe_destination(folder, Path(original_name).name)
        safe_copy(source, destination)
        return destination

    # Only oversized files get encoded.
    Image.MAX_IMAGE_PIXELS = 120_000_000
    with Image.open(source) as opened:
        opened.load()
        icc = opened.info.get('icc_profile')
        working = ImageOps.exif_transpose(opened)
        alpha = 'A' in working.getbands() or 'transparency' in working.info
        image = working.convert('RGBA' if alpha else 'RGB')

    # Prefer pixel-perfect, lossless WebP where practical.
    if source.suffix.lower() in ('.png', '.webp'):
        lossless = _encode_lossless(image, icc)
        if len(lossless) <= limit:
            output = safe_destination(folder, Path(original_name).stem + '.webp')
            _save_bytes(lossless, output)
            return output

    # Keep maximum resolution, then find highest quality fitting
    # the limit. Reduce dimensions only when necessary.
    for scale in (1.0, 0.92, 0.84, 0.76, 0.68, 0.60,
                  0.52, 0.44, 0.36, 0.28, 0.20, 0.14, 0.10):
        if scale == 1.0:
            candidate = image
        else:
            width = max(1, round(image.width * scale))
            height = max(1, round(image.height * scale))
            candidate = image.resize((width, height), Image.Resampling.LANCZOS)

        minimum_quality = 68 if scale >= 0.28 else 45
        smallest = _encode_webp(candidate, minimum_quality, icc)
        if len(smallest) > limit:
            continue

        best = smallest
        low, high = minimum_quality + 1, 98
        while low <= high:
            q = (low + high) // 2
            encoded = _encode_webp(candidate, q, icc)
            if len(encoded) <= limit:
                best = encoded
                low = q + 1
            else:
                high = q - 1

        output = safe_destination(folder, Path(original_name).stem + '.webp')
        _save_bytes(best, output)
        return output

    raise ValueError(
        f'Cannot compress {original_name} within {max_mb} MB; '
        'file was not saved above the selected size limit'
    )
