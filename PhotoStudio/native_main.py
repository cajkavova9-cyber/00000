"""Photo Studio native desktop entrypoint.

Loads the existing HTML UI in a self-contained Qt WebEngine window.
Never opens a Chrome/Safari tab and never requires a terminal at runtime.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _worker(name: str) -> int:
    """Run child processes *inside* the frozen application executable."""
    # PyInstaller --windowed on Windows may leave stdio unset. Worker mode
    # is launched with explicit subprocess.PIPE handles by our parent.
    if sys.platform == 'win32':
        if sys.stdin is None:
            sys.stdin = os.fdopen(0, 'r', encoding='utf-8', buffering=1, closefd=False)
        if sys.stdout is None:
            sys.stdout = os.fdopen(1, 'w', encoding='utf-8', buffering=1, closefd=False)
        if sys.stderr is None:
            sys.stderr = os.fdopen(2, 'w', encoding='utf-8', buffering=1, closefd=False)
    if name == 'login':
        from engine.facy_login_process import main
        result = main()
    elif name == 'facy':
        from engine.facy_process import main
        result = main()
    else:
        raise ValueError('Unknown worker')
    return 0 if result is None else int(result)


def _log_file() -> Path:
    if sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Logs' / 'PhotoStudio'
    elif sys.platform == 'win32':
        root = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'PhotoStudio' / 'logs'
    else:
        root = Path.home() / '.cache' / 'PhotoStudio'
    root.mkdir(parents=True, exist_ok=True)
    return root / 'desktop.log'


def _desktop() -> int:
    from http.server import ThreadingHTTPServer
    from PySide6.QtCore import Qt, QUrl, QStandardPaths
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from app import Handler, Studio, TEMP_ROOT
    import shutil

    # Playwright Chromium is an isolated external browser only for Facy login.
    if getattr(sys, 'frozen', False):
        browser_bundle = Path(getattr(sys, '_MEIPASS', ROOT)) / 'ms-playwright'
        if browser_bundle.is_dir():
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_bundle)

    # macOS is strict about GUI APIs being used from main thread.
    qt = QApplication(sys.argv[:1])
    qt.setApplicationName('Photo Studio')
    qt.setOrganizationName('Photo Studio')
    qt.setQuitOnLastWindowClosed(True)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.studio = Studio()
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': 0.2}, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_address[1]}/'

    window = QMainWindow()
    window.setWindowTitle('Photo Studio')
    window.setMinimumSize(1040, 700)
    window.resize(1440, 900)
    view = QWebEngineView(window)
    window.setCentralWidget(view)
    window.setStyleSheet('background:#080E19;')
    icon = ROOT / 'resources' / 'app_icon.png'
    if icon.is_file():
        window.setWindowIcon(QIcon(str(icon)))
    profile = view.page().profile()
    cache_root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if cache_root:
        os.makedirs(cache_root, exist_ok=True)
        profile.setPersistentStoragePath(os.path.join(cache_root, 'webengine-data'))
        profile.setCachePath(os.path.join(cache_root, 'webengine-cache'))
    view.load(QUrl(url))

    def load_finished(ok: bool) -> None:
        if not ok:
            QMessageBox.critical(window, 'Photo Studio',
                                 'Не удалось загрузить интерфейс. Перезапусти приложение.')
    view.loadFinished.connect(load_finished)

    def shutdown() -> None:
        # Stop loopback only when the native window closes.
        try:
            server.shutdown()
            server.server_close()
            server.studio.disconnect()
        finally:
            shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    qt.aboutToQuit.connect(shutdown)
    window.show()
    return qt.exec()


def _self_test() -> int:
    """Used by CI to reject incomplete native packages before upload."""
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PIL import Image  # noqa: F401
    from playwright.sync_api import sync_playwright  # noqa: F401
    from engine.facy_process import main as facy_main  # noqa: F401
    from engine.facy_login_process import main as login_main  # noqa: F401
    from app import Studio, Handler  # noqa: F401
    assert (ROOT / 'web' / 'index.html').is_file()
    assert (ROOT / 'web' / 'runtime.js').is_file()
    if getattr(sys, 'frozen', False):
        browser_bundle = Path(getattr(sys, '_MEIPASS', ROOT)) / 'ms-playwright'
        assert browser_bundle.is_dir() and any(browser_bundle.glob('chromium-*')), 'Missing bundled Chromium'
    return 0


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--internal-worker':
            return _worker(sys.argv[2])
        if len(sys.argv) == 2 and sys.argv[1] == '--self-test':
            return _self_test()
        return _desktop()
    except Exception:
        error = traceback.format_exc()
        try:
            with _log_file().open('a', encoding='utf-8') as log:
                log.write(error + '\n')
        except OSError:
            pass
        # Avoid silent Dock-bounce-and-exit on macOS.
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            qt = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, 'Photo Studio — ошибка запуска',
                                 'Не удалось запустить Photo Studio.\n\n'
                                 + error[-1100:])
        except Exception:
            pass
        return 1


if __name__ == '__main__':
    sys.exit(main())
