import io, json, sys, tempfile, time, threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Studio, Handler, ThreadingHTTPServer, safe_relative


def tiny_image(color='red', fmt='PNG'):
    b=io.BytesIO();Image.new('RGB',(120,80),color).save(b,format=fmt);return b.getvalue()


def test_files_and_jobs(tmp_path):
    app=Studio(tmp_path/'out')
    a=app.add_upload(io.BytesIO(tiny_image()),len(tiny_image()),'face.png')
    b=app.add_upload(io.BytesIO(tiny_image('blue')),len(tiny_image('blue')),'second.png')
    for kind,extra in [
      ('compress',{'format':'WEBP','quality':82}),
      ('crop',{'ratio':'1:1','zoom':90}),
      ('sort',{'perFolder':1})
    ]:
        job=app.create_job({'kind':kind,'files':[a['id'],b['id']],**extra})
        for _ in range(200):
            result=app.jobs[job['id']].snapshot()
            if result['status'] not in ('running','queued'): break
            time.sleep(.02)
        assert result['status']=='done',result
        images=list(Path(result['output']).rglob('*'))
        assert len([f for f in images if f.is_file()])==2,(kind,images)
        for f in images:
            if f.is_file():
                with Image.open(f) as im: im.verify()
    assert safe_relative('../etc/secret')==''
    assert (tmp_path/'out').exists()
    assert a['name']=='face.png'


def test_http_status_upload_jobs(tmp_path):
    srv=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    srv.studio=Studio(tmp_path/'exports')
    t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
    host=f'http://127.0.0.1:{srv.server_address[1]}'
    token=srv.studio.token
    def request(path,data=None,headers={}):
        h={'X-PhotoStudio-Token':token,**headers}
        if isinstance(data,dict): data=json.dumps(data).encode();h['Content-Type']='application/json'
        req=Request(host+path,data=data,headers=h)
        with urlopen(req,timeout=10) as resp:return resp.status,resp.read(),resp.headers
    try:
        code, page, heads=request('/')
        assert code==200 and b'Photo Studio' in page and token.encode() in page
        code, st, heads=request('/api/status')
        assert json.loads(st)['ok']
        with __import__('pytest').raises(HTTPError) as err:
            urlopen(host+'/api/status')
        assert err.value.code==403
        png=tiny_image()
        code, upload, _=request('/api/upload',png,{'Content-Type':'application/octet-stream','X-File-Name':'test.png'})
        image_id=json.loads(upload)['id']
        code, created, _=request('/api/jobs',{'kind':'crop','files':[image_id]})
        job_id=json.loads(created)['id']
        for _ in range(120):
            code, res, _=request('/api/jobs/'+job_id)
            job=json.loads(res)
            if job['status'] not in ('running','queued'):break
            time.sleep(.03)
        assert job['status']=='done',job
        code, names, _=request('/api/results/'+job_id)
        name=json.loads(names)['files'][0]
        code, img, _=request('/api/results/'+job_id+'/file?name='+name)
        assert code==200 and len(img)>100
    finally:
        srv.shutdown();srv.server_close()
