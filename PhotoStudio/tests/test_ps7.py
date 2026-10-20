import io,time,pytest
from pathlib import Path
from PIL import Image
import app

def make_image():
    im=Image.new('RGB',(200,100));p=im.load()
    for y in range(100):
      for x in range(200): p[x,y]=(x,y,40)
    b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()

def wait(studio,jid):
    for _ in range(400):
      v=studio.jobs[jid].snapshot()
      if v['status'] not in ('queued','running'):return v
      time.sleep(.015)
    raise AssertionError('job timed out')

def test_manual_crop_exact_dimensions_and_area(tmp_path):
    studio=app.Studio(tmp_path/'out'); b=make_image()
    u=studio.add_upload(io.BytesIO(b),len(b),'gradient.png')
    j=studio.create_job({'kind':'crop','files':[u['id']], 'ratio':'free',
                          'cropRect':{'x':.25,'y':.2,'w':.5,'h':.5}})
    v=wait(studio,j['id']);assert v['status']=='done',v
    imgs=list(Path(v['output']).glob('*'));assert len(imgs)==1
    with Image.open(imgs[0]) as im:
       assert im.size==(100,50)
       assert im.getpixel((0,0))[:2]==(50,20)

def test_crop_rejects_invalid_selection(tmp_path):
    studio=app.Studio(tmp_path/'out');b=make_image();u=studio.add_upload(io.BytesIO(b),len(b),'im.png')
    job=studio.create_job({'kind':'crop','files':[u['id']], 'cropRect':{'x':.8,'y':.2,'w':.5,'h':.4}})
    snap=wait(studio,job['id']);assert snap['status']=='error';assert 'rectangle' in snap['error']

def test_all_tool_folders_and_file_stats(tmp_path,monkeypatch):
    studio=app.Studio(tmp_path/'out'); b=make_image(); u=studio.add_upload(io.BytesIO(b),len(b),'im.png')
    for kind in ('crop','compress','sort','face'):
      target=tmp_path/kind;target.mkdir()
      monkeypatch.setattr(app,'choose_folder_native',lambda path,target=target:target)
      assert studio.select_output(kind)['path']==str(target)
      assert studio.output_snapshot(kind)['custom']
      if kind!='face':
       request={'kind':kind,'files':[u['id']]}
       if kind=='compress':request.update(format='WEBP',quality=72)
       if kind=='sort':request.update(perFolder=1)
       job=studio.create_job(request);snap=wait(studio,job['id']);assert snap['status']=='done',snap
       assert Path(snap['output']).parent==target
       assert snap['bytes_in']==len(b)
       assert snap['bytes_out']>0
      assert studio.reset_output(kind)['path']==str(tmp_path/'out')
    with pytest.raises(ValueError):studio.create_job({'kind':'shuffle','files':[u['id']]})

def test_html_four_modules_and_safe_dom():
    from bs4 import BeautifulSoup
    s=BeautifulSoup((Path(__file__).parents[1]/'web/index.html').read_text(),'html.parser')
    assert s.select_one('#cropFree')
    assert len(s.select('#page-home .module-card:not([data-jump="shuffle"])'))==4
    assert len(s.select('.tool-output'))==3
    assert s.select_one('#compressStatsDetail')
    assert len(s.select('#cropGuide [data-corner]'))==4
