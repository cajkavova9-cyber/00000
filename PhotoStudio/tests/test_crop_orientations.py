import io
import time
import unittest
import tempfile
from pathlib import Path
from PIL import Image
import app

class CropOrientationTests(unittest.TestCase):
    def test_two_separate_rectangles_and_preserved_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            studio=app.Studio(root/'results')
            uploads=[]
            for name,size in [
                ('horizontal.png',(200,100)),
                ('vertical.png',(100,200)),
            ]:
                stream=io.BytesIO()
                Image.new('RGB',size,'#3388cc').save(stream,'PNG')
                raw=stream.getvalue()
                uploads.append(studio.add_upload(
                    io.BytesIO(raw),len(raw),name
                )['id'])
            job=studio.create_job({
                'kind':'crop',
                'files':uploads,
                'ratio':'free',
                'cropRects':{
                    'landscape':{'x':0,'y':0,'w':1,'h':.5},
                    'portrait':{'x':0,'y':0,'w':.5,'h':1}
                },
                'cropRatios':{
                    'landscape':'free',
                    'portrait':'free'
                }
            })
            for _ in range(200):
                snap=studio.jobs[job['id']].snapshot()
                if snap['status'] not in ('queued','running'):
                    break
                time.sleep(.05)
            self.assertEqual(snap['status'],'done',snap)
            output=Path(snap['output'])
            with Image.open(output/'horizontal.png') as image:
                self.assertEqual(image.size,(200,50))
            with Image.open(output/'vertical.png') as image:
                self.assertEqual(image.size,(50,200))
            self.assertEqual(snap['done'],2)

    def test_orientation_gui_is_present(self):
        html=(Path(app.__file__).parent/'web/index.html').read_text(encoding='utf-8')
        runtime=(Path(app.__file__).parent/'web/runtime.js').read_text(encoding='utf-8')
        self.assertIn('psCropOrientationTabs',html)
        self.assertIn('psCropPreviewLandscape',html)
        self.assertIn('psCropPreviewPortrait',html)
        self.assertIn('cropRects:window.PhotoStudioCropRects',runtime)

if __name__=='__main__':
    unittest.main()
