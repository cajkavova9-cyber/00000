"""Playwright automation for Facy's official Batch Image Swap page.

Only Facy is used for face swapping. This module never writes credentials and
never opens a visible Chrome window; the graphical login lives in facy_login.

The website is third-party software: always test with ONE authorised target
image after Facy changes its UI. No CAPTCHA or usage-limit bypass is provided.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

FACY_URL = 'https://facy.ai/swap-face-ai/photo-batch'
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp'}
BATCH_SIZE = 20
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024 * 1024
GENERATE_TIMEOUT_MS = 900_000
DOWNLOAD_TIMEOUT_SECONDS = 1200
STALL_SECONDS = 900  # bounded watchdog for abnormally slow downloads


class FacyError(RuntimeError):
    """Recoverable, user-facing website compatibility or processing error."""


class FacyUploadLayoutError(FacyError):
    """The two upload zones cannot be identified unambiguously."""


class FacyIncompleteBatch(FacyError):
    """A Facy batch did not yield the requested number of usable images."""


class FacyCancelled(FacyError):
    """Cancellation requested before a batch was committed."""


def list_targets(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT),
        key=lambda p: p.name.casefold(),
    )


def _status(progress, message):
    progress('status', message)


def _login_required(page) -> bool:
    path = page.url.lower()
    return '/login' in path or '/sign-in' in path


def _safe_output_path(directory: Path, remote_filename: str) -> Path:
    """Return unused, non-traversing filename within directory."""
    name = re.split(r'[\\/]', str(remote_filename))[-1].strip().lstrip('.')
    if not name or name in {'.', '..'}:
        name = 'result.jpg'
    name = ''.join(c if c.isprintable() and c not in '<>:"|?*' else '_' for c in name)
    root = directory / name
    path = root
    index = 2
    while path.exists():
        path = directory / f'{root.stem}_{index}{root.suffix}'
        index += 1
    return path


def _copy_exclusive(src: Path, dest: Path):
    """Exclusive copy: a failed transfer must never leave a truncated output."""
    opened = False
    try:
        with dest.open('xb') as writer:
            opened = True
            with src.open('rb') as reader:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
    except BaseException:
        if opened:
            dest.unlink(missing_ok=True)
        raise


def _verify_image(path: Path):
    """Verify actual image content, not only the filename/extension."""
    if not path.is_file() or path.stat().st_size == 0:
        raise FacyIncompleteBatch('Facy вернул пустой файл изображения.')
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise FacyIncompleteBatch('Изображение превышает безопасный предел размера (200 МБ).')
    try:
        with Image.open(path) as img:
            if img.width < 16 or img.height < 16:
                raise FacyIncompleteBatch('Полученное изображение подозрительно маленькое.')
            img.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise FacyIncompleteBatch('Получен повреждённый файл вместо готовой фотографии.') from exc


class StallWatchdog:
    """Calls on_stall(label, seconds) once if an armed operation takes too long.

    Playwright's download.save_as() has no timeout and blocks forever when the
    site never finishes a file. Playwright objects must not be touched from
    another thread, so this watchdog only observes the clock and asks the
    owner (the worker process) to stop itself.
    """

    def __init__(self, timeout: float, on_stall, poll: float = 0.5):
        self.timeout = float(timeout)
        self.on_stall = on_stall
        self._lock = threading.Lock()
        self._label = ''
        self._since = None
        self._fired = False
        self._stop = threading.Event()
        self._poll = poll
        self._thread = threading.Thread(target=self._loop, name='facy-stall-watchdog', daemon=True)
        self._thread.start()

    def arm(self, label: str):
        with self._lock:
            self._label = label
            self._since = time.monotonic()

    def disarm(self):
        with self._lock:
            self._since = None

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self._poll):
            with self._lock:
                since, label = self._since, self._label
                due = since is not None and not self._fired and time.monotonic() - since >= self.timeout
                if due:
                    self._fired = True
            if due:
                try:
                    self.on_stall(label, int(self.timeout))
                except Exception:
                    pass


def _save_download(download, staging: Path) -> int:
    """Wait for the browser download to finish; verify and stage photos.

    Supports both individual images and a ZIP from Download All. Contents of
    ZIPs are *flattened* and checked; no remote path is ever extracted verbatim.
    Each call either adds its verified images or removes its own partial files.
    """
    remote_name = str(download.suggested_filename)
    ext = Path(remote_name).suffix.lower()
    if ext not in IMAGE_EXT | {'.zip'}:
        raise FacyIncompleteBatch(f'Неожиданный тип файла от Facy: {ext or "без расширения"}.')
    with tempfile.TemporaryDirectory(prefix='.incoming_', dir=staging) as tempdir:
        temp = Path(tempdir)
        incoming = temp / ('download' + ext)
        # Playwright save_as blocks until the download is complete.
        download.save_as(str(incoming))
        created = []
        try:
            if ext in IMAGE_EXT:
                _verify_image(incoming)
                target = _safe_output_path(staging, remote_name)
                # A verified file is placed into the staging area, not the final result.
                _copy_exclusive(incoming, target)
                created.append(target)
            else:
                with zipfile.ZipFile(incoming) as archive:
                    entries = [z for z in archive.infolist()
                               if not z.is_dir() and Path(z.filename).suffix.lower() in IMAGE_EXT]
                    if not entries:
                        raise FacyIncompleteBatch('ZIP от Facy не содержит фотографий.')
                    total_bytes = sum(z.file_size for z in entries)
                    if total_bytes > MAX_BATCH_BYTES or any(z.file_size > MAX_IMAGE_BYTES for z in entries):
                        raise FacyIncompleteBatch('ZIP превышает безопасный предел распаковки.')
                    for number, info in enumerate(entries):
                        local = temp / f'part_{number}{Path(info.filename).suffix.lower()}'
                        with archive.open(info) as src, local.open('wb') as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                        _verify_image(local)
                        target = _safe_output_path(staging, Path(info.filename).name)
                        _copy_exclusive(local, target)
                        created.append(target)
            return len(created)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise


def _upload_via_zone(page, zone_css: str, files: list[Path], *, must_be_multiple: bool):
    """Click the ACTUAL Facy dropzone and use its file chooser.

    The user's Playwright Codegen recording clicked #sourceFileDropzone and
    #targetFilesDropzone; it did not show an input nested inside the zones.
    Therefore we must not guess input[type=file].first or last.
    """
    zone = page.locator(zone_css)
    if zone.count() != 1:
        raise FacyUploadLayoutError(f'На сайте не найдена область загрузки {zone_css}.')
    try:
        with page.expect_file_chooser(timeout=15_000) as chooser_info:
            zone.click()
        chooser = chooser_info.value
    except Exception as exc:
        raise FacyUploadLayoutError(
            f'Область {zone_css} не открыла выбор файлов. Проверь страницу Facy.'
        ) from exc
    if must_be_multiple and len(files) > 1 and not chooser.is_multiple():
        raise FacyUploadLayoutError(
            'Поле целевых фотографий на Facy не принимает несколько файлов за один раз. '
            'Обработка остановлена без попыток загрузить пачку в неправильное поле.'
        )
    chooser.set_files([str(p.resolve()) for p in files])


def _publish_flat_batch(staging: Path, output: Path) -> list[Path]:
    """Publish verified downloaded images directly into user's output folder.

    Files keep Facy's names; a collision gets _2, _3, etc. Never overwrite
    existing results (including those from earlier runs). Publication tries
    hard links so every individual file becomes visible atomically on the
    staging/output filesystem. If linking is unsupported, exclusive copying
    is used instead. An ordinary failure rolls back only files THIS call added.
    """
    images = sorted(
        (p for p in staging.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT),
        key=lambda p: p.name.casefold(),
    )
    if not images:
        raise FacyIncompleteBatch('В загруженной пачке нет фотографий для сохранения.')
    published: list[Path] = []
    try:
        for image in images:
            _verify_image(image)
            attempts = 0
            while True:
                # Exclusive creation below handles races even when another
                # program chooses the same filename simultaneously.
                target = _safe_output_path(output, image.name)
                try:
                    try:
                        os.link(image, target)
                    except OSError as exc:
                        if exc.errno == errno.EEXIST:
                            raise FileExistsError(str(target)) from exc
                        if exc.errno not in {errno.EPERM, errno.EACCES, errno.EXDEV,
                                             errno.EOPNOTSUPP, errno.ENOSYS}:
                            raise
                        _copy_exclusive(image, target)
                    published.append(target)
                    break
                except FileExistsError:
                    attempts += 1
                    if attempts > 10000:
                        raise FacyError('Слишком много одинаковых имён файлов в папке результатов.')
        return published
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise


def _download_batch(page, expected: int, staging: Path, stop: threading.Event,
                    *, timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
                    on_status=None, output: Path | None = None,
                    on_saved=None, watchdog: 'StallWatchdog | None' = None) -> int:
    """Download up to expected photos; publish each verified event immediately.

    A single download event can contain an archive with many photos. Therefore
    one expect_download event is NEVER sufficient proof that a batch is saved.

    When `output` is given, each individually verified image/ZIP download is
    published immediately to the selected output folder. An incomplete batch
    must STOP subsequent batches but must NEVER discard already verified files.
    Without `output`, keep the old staging-only interface for isolated tests.
    """
    events = []

    # IMPORTANT: Playwright's event registration requires a regular Python
    # callback. Passing ``events.append`` directly (a built-in bound method)
    # caused: 'builtin_function_or_method' object has no attribute
    # '_pw_impl_instance_' on macOS with Playwright 1.63.
    def handle_download(download):
        events.append(download)

    page.on('download', handle_download)
    saved = 0
    processed = 0
    try:
        button = page.get_by_role('button', name='Download All')
        button.click(timeout=30_000)
        start = time.monotonic()
        deadline = start + timeout_seconds
        next_notice = start + 10
        if on_status:
            on_status(f'Нажали Download All. Ожидаем {expected} фотографий (до {int(timeout_seconds)} сек)…')
        while time.monotonic() < deadline:
            if stop.is_set():
                raise FacyCancelled('Остановка запрошена во время скачивания.')
            while processed < len(events):
                if on_status:
                    on_status(f'Получаем скачивание №{processed + 1} от Facy…')
                if watchdog is not None:
                    watchdog.arm(f'Скачивание №{processed + 1} из {expected}')
                try:
                    received = _save_download(events[processed], staging)
                finally:
                    if watchdog is not None:
                        watchdog.disarm()
                processed += 1
                if saved + received > expected:
                    raise FacyIncompleteBatch(
                        f'Facy вернул {saved + received} фото вместо {expected}. '
                        'Уже сохранённые результаты оставлены на диске.'
                    )
                if output is not None:
                    # Staging stores only the current download, so a failed
                    # fifth image can never erase the first four. Keep each
                    # successful image visible immediately in the flat output.
                    published = _publish_flat_batch(staging, output)
                    if len(published) != received:
                        raise FacyError('Не удалось опубликовать все проверенные изображения.')
                    for staged_image in staging.iterdir():
                        if staged_image.is_file() and staged_image.suffix.lower() in IMAGE_EXT:
                            staged_image.unlink()
                saved += received
                if on_saved is not None:
                    on_saved(saved)
                if on_status:
                    if output is None:
                        on_status(f'Проверено и временно сохранено: {saved}/{expected} фотографий.')
                    else:
                        on_status(f'Сохранено в выбранную папку: {saved}/{expected} фотографий. '
                                  'Эти файлы останутся, даже если другие не скачаются.')
            if saved == expected:
                return saved
            now = time.monotonic()
            if now >= next_notice:
                if on_status:
                    elapsed = int(now - start)
                    on_status(f'Ожидаем скачивания Facy: {saved}/{expected}; прошло {elapsed} сек. '
                              'Следующая пачка ещё не запущена.')
                next_notice = now + 10
            page.wait_for_timeout(300)
        raise FacyIncompleteBatch(
            f'Не удалось скачать все фотографии: получено {saved} из {expected} '
            f'за {int(timeout_seconds)} сек. Проверь, сколько результатов создал Facy. '
            'Следующая пачка НЕ начата. Уже скачанные и проверенные фото '
            'СОХРАНЕНЫ прямо в выбранной папке результатов. '
            'Возможные причины: Facy не создал один результат или браузер не получил '
            'одно событие скачивания. Нельзя определить причину только по счётчику.'
        )
    finally:
        page.remove_listener('download', handle_download)


def _validate_paths(source: Path, targets: list[Path], output: Path):
    if not source.is_file() or source.suffix.lower() not in IMAGE_EXT:
        raise ValueError('Исходное лицо должно быть существующим JPG, PNG или WebP.')
    if not targets or any(not t.is_file() or t.suffix.lower() not in IMAGE_EXT for t in targets):
        raise ValueError('Не найдены выбранные целевые JPG/PNG/WebP фотографии.')
    if source.resolve() in {t.resolve() for t in targets}:
        raise ValueError('Фотография исходного лица не должна быть целевой фотографией.')
    output_resolved = output.resolve()
    target_parents = {t.parent.resolve() for t in targets}
    if output_resolved == source.parent.resolve() or output_resolved in target_parents:
        raise ValueError('Папка результатов должна отличаться от папки входных фотографий.')
    if any(parent == output_resolved or parent in output_resolved.parents for parent in target_parents):
        raise ValueError('Папка результатов не должна находиться внутри папки целевых фотографий.')


def run_facy(source: Path, targets: list[Path], output: Path, state: dict,
             progress, stop: threading.Event, *, playwright_factory=None,
             download_timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
             on_stall=None, stall_seconds: float = STALL_SECONDS):
    """Public entry point. With on_stall, a single download that does not
    finish within stall_seconds triggers on_stall(label, seconds)."""
    watchdog = StallWatchdog(stall_seconds, on_stall) if on_stall is not None else None
    try:
        return _run_facy(source, targets, output, state, progress, stop,
                         playwright_factory=playwright_factory,
                         download_timeout_seconds=download_timeout_seconds,
                         watchdog=watchdog)
    finally:
        if watchdog is not None:
            watchdog.stop()


def _run_facy(source: Path, targets: list[Path], output: Path, state: dict,
              progress, stop: threading.Event, *, playwright_factory=None,
              download_timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
              watchdog=None):
    """Execute Facy batches through a hidden Chromium, with atomic batch commits.

    `playwright_factory` is for offline test doubles; the GUI uses real Playwright.
    Returns (saved_count, total_count) on success. On any failure, does not
    submit later batches; already verified photos are retained in output even
    when the current batch is incomplete. No per-batch subfolders.
    """
    _validate_paths(source, targets, output)
    if not isinstance(state, dict) or not isinstance(state.get('cookies'), list):
        raise ValueError('Нет проверенных данных входа Facy.')
    output.mkdir(parents=True, exist_ok=True)
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright
        playwright_factory = sync_playwright

    with playwright_factory() as p:
        if stop.is_set():
            return (0, len(targets))
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=state, accept_downloads=True)
            try:
                page = context.new_page()
                _status(progress, 'Проверяем авторизацию на Facy…')
                page.goto(FACY_URL, wait_until='domcontentloaded', timeout=60_000)
                if _login_required(page):
                    raise FacyError('Сессия Facy не принята. Войди заново внутри Photo Studio.')
                page.locator('#sourceFileDropzone').wait_for(state='visible', timeout=30_000)
                completed = 0
                incomplete_batches = []
                total_batches = (len(targets) + BATCH_SIZE - 1) // BATCH_SIZE
                for batch_idx, offset in enumerate(range(0, len(targets), BATCH_SIZE), 1):
                    if stop.is_set():
                        break
                    batch = targets[offset:offset+BATCH_SIZE]
                    saved_this_batch = 0
                    def record_batch_progress(saved):
                        nonlocal saved_this_batch
                        saved_this_batch = saved
                        progress('progress', (completed + saved, len(targets)))
                    _status(progress, f'Пачка {batch_idx}/{total_batches}: загружаем {len(batch)} фотографий…')
                    with tempfile.TemporaryDirectory(prefix=f'.facy_batch_{batch_idx:03d}_', dir=output) as staging_name:
                        staging = Path(staging_name)
                        try:
                            _upload_via_zone(page, '#sourceFileDropzone', [source], must_be_multiple=False)
                            _upload_via_zone(page, '#targetFilesDropzone', batch, must_be_multiple=True)
                            checkbox = page.get_by_role('checkbox', name=re.compile('I accept Facy', re.I))
                            if checkbox.count() == 1 and not checkbox.is_checked():
                                checkbox.check()
                            if stop.is_set():
                                raise FacyCancelled('Отмена перед отправкой фотографий.')
                            page.get_by_role('button', name='Upload').click()
                            _status(progress, f'Пачка {batch_idx}: ждём результат Facy…')
                            page.wait_for_url('**/result_image_batch/**', timeout=GENERATE_TIMEOUT_MS)
                            if page.get_by_text('No image available').count():
                                raise FacyIncompleteBatch(
                                    'Facy показывает «No image available» вместо результата. '
                                    'Проверь исходные изображения и доступность сервиса.'
                                )
                            page.get_by_role('button', name='Download All').wait_for(
                                state='visible', timeout=GENERATE_TIMEOUT_MS)
                            _status(progress, f'Пачка {batch_idx}: скачиваем и проверяем все {len(batch)} фото…')
                            verified = _download_batch(
                                page, len(batch), staging, stop,
                                timeout_seconds=download_timeout_seconds,
                                on_status=lambda text: _status(progress, f'Пачка {batch_idx}: {text}'),
                                output=output,
                                on_saved=record_batch_progress,
                                watchdog=watchdog,
                            )
                            if stop.is_set():
                                raise FacyCancelled('Обработка отменена до сохранения пачки.')
                            if verified != len(batch):
                                raise FacyIncompleteBatch('Количество проверенных файлов не совпало с размером пачки.')
                            completed += len(batch)
                            progress('progress', (completed, len(targets)))
                            _status(progress, f'Пачка {batch_idx}: готово {len(batch)}/{len(batch)}. '
                                              'Все файлы лежат прямо в выбранной папке (без batch-папок).')
                        except FacyCancelled:
                            _status(progress, f'Остановлено на пачке {batch_idx}; следующие не отправлялись.')
                            return (completed, len(targets))
                        except FacyIncompleteBatch as exc:
                            completed += saved_this_batch
                            progress('progress', (completed, len(targets)))
                            _status(progress,
                                f'Пачка {batch_idx}: сохранено {saved_this_batch} из {len(batch)}. '
                                'Следующие пачки не запущены, пока не проверены недостающие результаты. '
                                f'Подробности: {exc}')
                            return (completed, len(targets))
                        except Exception as exc:
                            raise FacyError(
                                f'Пачка {batch_idx} остановлена: {exc}. '
                                f'Все успешно скачанные фото, включая часть текущей пачки, остаются в {output}. '
                                'Следующая пачка НЕ запускалась.'
                            ) from exc
                    if offset + len(batch) < len(targets) and not stop.is_set():
                        # ONLY after the verified, fully committed previous batch.
                        try:
                            page.get_by_role('button', name='Start new swap').click(timeout=15_000)
                            page.locator('#sourceFileDropzone').wait_for(state='visible', timeout=30_000)
                        except Exception:
                            page.goto(FACY_URL, wait_until='domcontentloaded', timeout=60_000)
                            if _login_required(page):
                                raise FacyError('Сессия Facy истекла. Войди заново.')
                            page.locator('#sourceFileDropzone').wait_for(state='visible', timeout=30_000)
                return (completed, len(targets))
            finally:
                context.close()
        finally:
            browser.close()
