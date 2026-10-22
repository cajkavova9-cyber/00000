"""Offline regression tests executed after repairs."""
from pathlib import Path
import os
import tempfile
import threading
from unittest.mock import patch

from PIL import Image

from engine import facy_engine as facy
from engine.size_limit_compress import compress_file


def test_compression_limits_decimal_megabytes():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        src = folder / 'photo.png'
        image = Image.frombytes('RGB', (1100, 950), os.urandom(1100 * 950 * 3))
        image.save(src)
        before = src.stat().st_size
        for mb in range(1, 6):
            out = compress_file(src, src.name, folder / str(mb), mb)
            assert 0 < out.stat().st_size <= mb * 1_000_000
            with Image.open(out) as im:
                im.verify()
            assert src.stat().st_size == before


class FakeNode:
    def count(self): return 0
    def wait_for(self, **kwargs): return None
    def click(self, **kwargs): return None


class FakePage:
    url = facy.FACY_URL
    def goto(self, *args, **kwargs): return None
    def locator(self, *args, **kwargs): return FakeNode()
    def get_by_role(self, *args, **kwargs): return FakeNode()
    def get_by_text(self, *args, **kwargs): return FakeNode()
    def wait_for_url(self, *args, **kwargs): return None


class FakeContext:
    def new_page(self): return FakePage()
    def close(self): pass


class FakeBrowser:
    def new_context(self, **kwargs): return FakeContext()
    def close(self): pass


class FakeChromium:
    def launch(self, **kwargs): return FakeBrowser()


class FakePlaywright:
    chromium = FakeChromium()
    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_facy_5000_batches_offline():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inputs = root / 'input'
        inputs.mkdir()
        source = inputs / 'source.png'
        source.touch()
        targets = []
        for index in range(5000):
            p = inputs / f'{index:05}.png'
            p.touch()
            targets.append(p)
        attempts = []
        statuses = []
        def fake_upload(*args, **kwargs): return None
        def fake_download(page, expected, staging, stop, *, on_saved, **kwargs):
            attempts.append(expected)
            on_saved(expected)
            return expected
        with patch.object(facy, '_upload_via_zone', fake_upload), patch.object(facy, '_download_batch', fake_download):
            done, total = facy.run_facy(source, targets, root / 'results', {'cookies': []},
                lambda k, v: statuses.append((k, v)) if k == 'progress' else None,
                threading.Event(), playwright_factory=FakePlaywright)
        assert (done, total) == (5000, 5000)
        assert len(attempts) == 250 and set(attempts) == {20}
        assert statuses[-1][1] == (5000, 5000)


def test_facy_incomplete_batch_stops_without_skipping():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inputs = root / 'input'
        inputs.mkdir()
        source = inputs / 'source.png'
        source.touch()
        targets = []
        for index in range(40):
            target = inputs / f'{index:05}.png'
            target.touch()
            targets.append(target)
        attempts = []
        def fake_download(page, expected, staging, stop, *, on_saved, **kwargs):
            attempts.append(expected)
            on_saved(19)
            raise facy.FacyIncompleteBatch('Simulated missing result')
        with patch.object(facy, '_upload_via_zone', lambda *a, **kw: None), patch.object(facy, '_download_batch', fake_download):
            done, total = facy.run_facy(source, targets, root / 'results', {'cookies': []},
                lambda *args: None, threading.Event(), playwright_factory=FakePlaywright)
        assert (done, total) == (19, 40)
        assert attempts == [20], 'Incorrectly skipped to a later batch'


def test_sidebar_and_language_controls():
    from bs4 import BeautifulSoup
    html = (Path(__file__).parents[1] / 'web/index.html').read_text(encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    assert soup.select_one('.rail .brand') is None
    assert soup.select_one('#psLanguageButton')
    assert soup.select_one('#psLanguageMenu')
    assert 'RU <span aria-hidden="true">⌄</span>' not in html
