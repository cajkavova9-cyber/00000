import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from engine.size_limit_compress import compress_file

class TargetCompressionTests(unittest.TestCase):
    def test_small_file_is_copied_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.png'
            output = root / 'results'
            Image.new('RGB', (100, 80), '#347cf4').save(source, 'PNG')
            original = source.read_bytes()
            result = compress_file(source, 'input.png', output, 2)
            self.assertEqual(result.name, 'input.png')
            self.assertEqual(result.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)

    def test_large_image_fits_one_megabyte(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'large.png'
            output = root / 'results'
            pixels = os.urandom(1100 * 900 * 3)
            image = Image.frombytes('RGB', (1100, 900), pixels)
            image.save(source, 'PNG')
            self.assertGreater(source.stat().st_size, 1024 * 1024)
            original_size = source.stat().st_size
            result = compress_file(source, 'large.png', output, 1)
            self.assertLessEqual(result.stat().st_size, 1024 * 1024)
            self.assertEqual(result.suffix, '.webp')
            with Image.open(result) as compressed:
                compressed.verify()
            self.assertEqual(source.stat().st_size, original_size)

    def test_reject_invalid_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'photo.png'
            Image.new('RGB', (10, 10)).save(source, 'PNG')
            with self.assertRaises(ValueError):
                compress_file(source, 'photo.png', Path(tmp) / 'out', 9)

if __name__ == '__main__':
    unittest.main()
