import io, time
from pathlib import Path
from PIL import Image
import app

def make_pic():
 b=io.BytesIO();Image.new('RGB',(80,60),'red').save(b,'PNG');return b.getvalue()

def wait(studio, job_id, timeout=20):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  status=studio.jobs[job_id].snapshot()
  if status['status'] not in ('queued','running'):return status
  time.sleep(.02)
 raise AssertionError('timeout')

def test_batch_crop_same_frame_on_all(tmp_path):
 studio=app.Studio(tmp_path/'out')
 data=make_pic(); uploads=[]
 for i in range(40):
  uploads.append(studio.add_upload(io.BytesIO(data),len(data),f'p{i:03d}.png')['id'])
 task=studio.create_job({'kind':'crop','files':uploads,'ratio':'free','cropRect':{'x':.25,'y':.2,'w':.5,'h':.5}})
 status=wait(studio,task['id'])
 assert status['status']=='done',status
 results=list(Path(status['output']).glob('*.png'))
 assert len(results)==40
 for path in results:
  with Image.open(path) as im:assert im.size==(40,30)

def test_sort_max_250_and_min_1(tmp_path):
 data=make_pic();studio=app.Studio(tmp_path/'out')
 uploads=[studio.add_upload(io.BytesIO(data),len(data),f'p{i:03d}.png')['id'] for i in range(256)]
 for count,expected in [(250,2),(1,256),(999,2)]:
  task=studio.create_job({'kind':'sort','files':uploads,'perFolder':count})
  status=wait(studio,task['id']);assert status['status']=='done',status
  folders=[p for p in Path(status['output']).iterdir() if p.is_dir()]
  assert len(folders)==expected,(count,len(folders))
  assert sum(len(list(p.iterdir())) for p in folders)==256

def test_mixed_ratios_keeps_requested_output_ratio(tmp_path):
 studio=app.Studio(tmp_path/'out');uploads=[]
 for i,size in enumerate([(160,100),(100,160),(230,230),(180,150)]):
  b=io.BytesIO();Image.new('RGB',size,'blue').save(b,'PNG');data=b.getvalue()
  uploads.append(studio.add_upload(io.BytesIO(data),len(data),f'mix{i}.png')['id'])
 task=studio.create_job({'kind':'crop','files':uploads,'ratio':'4:5',
                        'cropRect':{'x':.25,'y':.15,'w':.5,'h':.7}})
 snap=wait(studio,task['id']);assert snap['status']=='done',snap
 for file in Path(snap['output']).glob('*.png'):
  with Image.open(file) as im: assert abs(im.width/im.height - .8)<.025,(file,im.size)
