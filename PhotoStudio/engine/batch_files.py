"""Safe, dependency-light utilities shared by Sort and Shuffle."""
from __future__ import annotations
import os
import random
import shutil
import tempfile
from pathlib import Path

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.heic'}

def list_photos(folder: Path, recursive: bool = False) -> list[Path]:
    folder = folder.resolve()
    iterator = folder.rglob('*') if recursive else folder.iterdir()
    return sorted((p for p in iterator if p.is_file() and p.suffix.lower() in EXTENSIONS), key=lambda p: str(p).casefold())

def safe_destination(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    source_name = Path(filename)
    candidate = directory / source_name.name
    counter = 2
    while candidate.exists():
        candidate = directory / f'{source_name.stem}_{counter}{source_name.suffix}'
        counter += 1
    return candidate

def safe_copy(source: Path, target: Path) -> None:
    """Copy to temporary file in destination dir, then atomically publish without overwrite."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix='.photostudio_', dir=target.parent)
    os.close(handle)
    try:
        shutil.copy2(source, temp_name)
        # Exclusive creation of final filename: avoid overwriting on a race.
        os.link(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)

def validate_dest(source: Path, output: Path) -> None:
    src = source.resolve()
    dest = output.resolve()
    if dest == src or src in dest.parents:
        raise ValueError('Выбери папку результатов вне исходной папки.')

def distribute(files: list[Path], output: Path, size: int, cancelled=lambda: False, progress=lambda i,n: None):
    if size not in (250, 500):
        raise ValueError('Разрешены только 250 или 500 фотографий в папке')
    count = 0
    for index, source in enumerate(files):
        if cancelled():
            break
        directory = output / f'Folder_{index // size + 1:03d}'
        dest = safe_destination(directory, source.name)
        safe_copy(source, dest)
        count += 1
        progress(count, len(files))
    return count

def shuffle_copy(files: list[Path], output: Path, within_folders: bool = False,
                 seed: int | None = None, source_root: Path | None = None,
                 cancelled=lambda: False, progress=lambda i,n: None):
    rng = random.Random(seed)
    total = len(files)
    groups: dict[Path, list[Path]] = {}
    for file in files:
        key = file.parent.relative_to(source_root) if within_folders and source_root else Path('.')
        groups.setdefault(key, []).append(file)
    count = 0
    for rel_dir, group in sorted(groups.items(), key=lambda x: str(x[0])):
        ordered = group.copy()
        rng.shuffle(ordered)
        for number, source in enumerate(ordered, 1):
            if cancelled():
                return count
            target_dir = output / rel_dir
            target = safe_destination(target_dir, f'{number:06d}_{source.name}')
            safe_copy(source, target)
            count += 1
            progress(count, total)
    return count
