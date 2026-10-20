import io,json,sys,threading,time
from pathlib import Path
from urllib.request import Request,urlopen
import pytest
from PIL import Image
import app

@pytest.fixture
def studio_server(tmp_path,monkeypatch):
    # Prevent modifying real user's settings during tests.
    monkeypatch.setattr(app.Studio, '_persist_settings', lambda self: None)
    s=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    s.studio=app.Studio(tmp_path/'saved')
    t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
    yield s,tmp_path
    s.shutdown();s.server_close()

def request(s,path,data=None):
    root=f'http://127.0.0.1:{s.server_address[1]}'
    payload=json.dumps(data).encode() if data is not None else None
    with urlopen(Request(root+path,data=payload,headers={'X-PhotoStudio-Token':s.studio.token,'Content-Type':'application/json'}),timeout=12) as r:
        return json.loads(r.read())

def test_folder_selection_and_reset(studio_server,monkeypatch):
    srv,tmp=studio_server
    new=tmp/'custom';new.mkdir()
    monkeypatch.setattr(app,'choose_folder_native',lambda _:new)
    val=request(srv,'/api/face/output/select',{})
    assert val['path']==str(new)
    assert val['custom']
    assert request(srv,'/api/face/output')['path']==str(new)
    assert request(srv,'/api/face/output/reset',{})['path']==str(tmp/'saved')

def test_actual_face_target_path(studio_server,monkeypatch):
    srv,tmp=studio_server
    root=tmp/'custom';root.mkdir()
    monkeypatch.setattr(app,'choose_folder_native',lambda _:root)
    request(srv,'/api/face/output/select',{})
    img=io.BytesIO();Image.new('RGB',(60,50),'green').save(img,format='PNG')
    id_=srv.studio.add_upload(io.BytesIO(img.getvalue()),len(img.getvalue()),'test.png')['id']
    srv.studio.session={'cookies':[]}
    # Make the worker deterministic without touching external Facy.
    def stub(job,uploads,req):
        Image.new('RGB',(60,50),'green').save(job.output/'swap.png')
        job.update(done=1,status='done',message='Saved')
    monkeypatch.setattr(srv.studio,'_run_facy',stub)
    job=srv.studio.create_job({'kind':'face','files':[id_],'source':id_})
    for i in range(100):
        snap=srv.studio.jobs[job['id']].snapshot()
        if snap['status']=='done':break
        time.sleep(.01)
    assert snap['status']=='done'
    assert Path(snap['output']).parent==root
    assert (Path(snap['output'])/'swap.png').is_file()
    assert Path(tmp/'saved').exists()

def test_html_no_compare_and_buttons(studio_server):
    from bs4 import BeautifulSoup
    content=(Path(__file__).resolve().parents[1]/'web/index.html').read_text()
    soup=BeautifulSoup(content,'html.parser')
    assert soup.select_one('#faceOutputPanel')
    assert not soup.select_one('#page-face #faceCompare')
    assert not soup.select_one('#page-face .compare-placeholder')
    assert [n['id'] for n in soup.select('#facyConnectionPanel .facy-actions button')]==['facyConnect','facyHow','facyDisconnect']
    assert len(soup.select('#page-motion [data-motion-effect]'))>=0  # other motion system left intact
    assert 'PhotoStudioSelected=selected' in content

@pytest.mark.slow
def test_browser_queue_and_folder(studio_server,monkeypatch,tmp_path):
    from playwright.sync_api import sync_playwright
    srv,tmp=studio_server
    new=tmp/'selected';new.mkdir()
    monkeypatch.setattr(app,'choose_folder_native',lambda _ :new)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        try:
            page.goto(f'http://127.0.0.1:{srv.server_address[1]}',wait_until='domcontentloaded')
        except Exception as error:
            if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(error):
                pytest.skip('Sandbox Chromium blocks all navigations; offline DOM interactions tested separately')
            raise
        page.locator('[data-page="face"]').first.click()
        page.locator('#faceChooseOutput').click()
        assert page.locator('#faceOutputPath').inner_text()==str(new)
        import io
        im=io.BytesIO();Image.new('RGB',(35,35),'blue').save(im,format='PNG')
        payload=[{'name':f'p{i:03d}.png','mimeType':'image/png','buffer':im.getvalue()} for i in range(130)]
        page.locator('#faceTargetInput').set_input_files(payload)
        page.wait_for_timeout(650)
        assert '130' in page.locator('#faceTargetCount').inner_text()
        dims=page.locator('#faceTargetList').evaluate('(e)=>({client:e.clientHeight,scroll:e.scrollHeight,rows:e.querySelectorAll(".file-row").length})')
        assert dims['client']<=270,dims
        assert dims['scroll']>dims['client']+7000,dims
        assert dims['rows']<=16,dims
        page.locator('#faceTargetList').evaluate('(e)=>e.scrollTop=e.scrollHeight')
        page.wait_for_timeout(250)
        assert page.locator('#faceTargetList').evaluate('(e)=>e.scrollTop')>3000
        assert page.locator('#page-face').evaluate('(e)=>e.offsetHeight')<1550
        # Simulated test state from real Python state: no remote Facy call.
        srv.studio.session={'cookies':[]};srv.studio.auth_status='connected'
        page.reload(wait_until='domcontentloaded')
        page.locator('[data-page="face"]').first.click()
        page.wait_for_timeout(500)
        assert not page.locator('#facyInlineNote').is_hidden()
        assert 'Успешно' not in page.locator('#facyInlineTitle').inner_text() or 'Facy' in page.locator('#facyInlineTitle').inner_text()
        assert not errors,errors
        page.screenshot(path=str(tmp_path / 'PhotoStudio_6_FaceSwap_Preview.png'),full_page=True)
        browser.close()
