'use strict';
// Production bridge. Keeps the original HyperMotion CSS/motions intact.
(() => {
 const token='__PS_RUNTIME_TOKEN__';
 const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
 const chosen=window.PhotoStudioSelected||{home:[],faceSource:[],faceTarget:[],compress:[],sort:[],shuffle:[]};
 if(!chosen.crop)chosen.crop=[];
 let currentJob=null, currentResult=null, authTimer=null, uploadController=null;
 let pollFailures=0;
 const inputMap={homeFiles:'home',faceSourceInput:'faceSource',faceTargetInput:'faceTarget',cropFile:'crop',compressFile:'compress',sortFiles:'sort',shuffleFiles:'shuffle',sortFolderInput:'sort',shuffleFolderInput:'shuffle'};
 const lng=()=>{try{return localStorage.getItem('photo.studio.language')||'ru'}catch(_){return document.getElementById('psLanguage')?.value||'ru'}};
 const tx=(ru,en,uk)=>lng()==='en'?en:lng()==='uk'?uk:ru;
 async function call(path,data=null,options={}){
   const h={'X-PhotoStudio-Token':token};
   let req={headers:h,cache:'no-store'};
   if(data!==null){req.method='POST';h['Content-Type']='application/json';req.body=JSON.stringify(data)}
   if(options.blob){req.method='POST';req.signal=options.signal;h['Content-Type']='application/octet-stream';h['X-File-Name']=encodeURIComponent(options.blob.name);
     h['X-Relative-Path']=encodeURIComponent(options.blob.webkitRelativePath||options.blob.name);req.body=options.blob}
   const response=await fetch(path,req);
   let body;
   try{body=await response.json()}catch(e){throw Error(tx('Некорректный ответ приложения','Invalid application response','Некоректна відповідь застосунку'))}
   if(!response.ok)throw Error(body.error||`HTTP ${response.status}`);
   return body;
 }
 function notify(message,bad=false){
   const stack=$('#toastStack');const box=document.createElement('div');box.className='toast';box.setAttribute('role','status');
   box.textContent=(bad?'⚠  ':'✓  ')+message;stack.append(box);
   setTimeout(()=>{box.classList.add('toast-out');setTimeout(()=>box.remove(),280)},5000);
 }
 function setContent(sel,text){const el=$(sel);if(el)el.textContent=text}
 function setWidth(sel,p){const el=$(sel);if(el){el.style.width=p+'%';el.style.transform='none'}}
 function setBusy(b){for(const id of ['faceRun','cropDemo','compressDemo','sortPreview','shuffleDo']){const el=$('#'+id);if(el)el.disabled=b}}
 function updateHomeStatus(status,result=''){
   const values=$$('#page-home .dashboard-side .session-stat .session-value');
   if(values[0]) values[0].textContent=status;
   if(values[1]&&result) values[1].textContent=result;
 }
 function stateProgress(kind,p){
   if(kind==='face'){setContent('#facePct',p+'%');setWidth('#faceProgress',p)}
   if(kind==='compress'){setContent('#compressPct',p+'%');setWidth('#compressProgress',p)}
 }
 // Capture selections before demo handlers reset <input> values.
 document.addEventListener('change',e=>{
   if(!Object.hasOwn(inputMap,e.target.id))return;
   const group=inputMap[e.target.id];
   // Native HyperMotion handlers own file selection for regular inputs.
   if(!['sortFolderInput','shuffleFolderInput'].includes(e.target.id))return;
   const selection=Array.from(e.target.files||[]);
   if(!selection.length)return;
   chosen[group]=selection;
   if(e.target.id==='sortFolderInput'){setContent('#sortFileList',chosen.sort.length+' files selected');}
   if(e.target.id==='shuffleFolderInput'){setContent('#shuffleFileStatus',chosen.shuffle.length+' files selected');}
   if(group==='crop')notify(tx('Изображение готово к кадрированию','Image ready for crop','Зображення готове до кадрування'));
 },true);
 document.addEventListener('drop',e=>{
   const zone=e.target.closest?.('#homeDropZone,#faceSourceDrop,#faceTargetDrop,#compressDrop,#sortDrop');
   if(!zone||!e.dataTransfer)return;
   const files=Array.from(e.dataTransfer.files||[]);
   const idMap={homeDropZone:'home',faceSourceDrop:'faceSource',faceTargetDrop:'faceTarget',compressDrop:'compress',sortDrop:'sort'};
   // The original app's drop handler synchronizes real files and visual queue.
   // Do not replace its array from an earlier capture handler.
   if(files.length && !window.PhotoStudioSelected)chosen[idMap[zone.id]]=files;
 },true);
 function files(group){return chosen[group]||[]}
 async function upload(group){
   const arr=files(group);
   if(!arr.length)throw Error(tx('Сначала выбери изображения','Select images first','Спочатку обери зображення'));
   if(arr.length>10000)throw Error('Maximum batch: 10 000 images');
   const results=new Array(arr.length);
   const status=document.createElement('div');status.className='toast';status.id='psUploadProgress';
   $('#toastStack').append(status);
   const controller=new AbortController();uploadController=controller;
   let next=0,done=0;
   async function worker(){
     while(next<arr.length && !controller.signal.aborted){
       const i=next++,f=arr[i];
       results[i]=(await call('/api/upload',null,{blob:f,signal:controller.signal})).id;
       done++;
       if(done===arr.length||done%Math.max(1,Math.floor(arr.length/100))===0)
         status.textContent=tx('Загружено: ','Uploaded: ','Завантажено: ')+done+'/'+arr.length;
     }
   }
   try{await Promise.all(Array.from({length:Math.min(4,arr.length)},()=>worker()));return results;}
   catch(err){controller.abort();throw err}
   finally{status.remove();if(uploadController===controller)uploadController=null}
 }
 async function create(kind,groups,options={}){
   if(currentJob&&['running','queued'].includes(currentJob.status))throw Error(tx('Дождись завершения текущей задачи','Another task is still running','Інше завдання ще виконується'));
   setBusy(true); currentResult=null;updateHomeStatus(tx('Выполняется','Running','Виконується'));
   try{
     let uploaded={};
     for(const group of groups)uploaded[group]=await upload(group);
     const fileGroup=kind==='face'?'faceTarget':groups[0];
     const job=await call('/api/jobs',{kind,files:uploaded[fileGroup],...(kind==='face'?{source:uploaded.faceSource[0]}:{}),...options});
     currentJob=job;stateProgress(kind,0);
     poll(job.id,kind);
   }catch(e){setBusy(false);updateHomeStatus(tx('Ошибка','Error','Помилка'));notify(e.name==='AbortError'?tx('Загрузка отменена','Upload cancelled','Завантаження скасовано'):e.message,e.name!=='AbortError');throw e}
 }
 async function poll(id,kind){
   try{
     const p=await call('/api/jobs/'+id);
     pollFailures=0;
     if(currentJob?.id!==id)return;
     currentJob=p;
     const percent=p.percent||0;stateProgress(kind,percent);
     if(kind==='face')setContent('#faceStatus',(p.status||'').toUpperCase()+' · '+(p.message||''));
     if(kind==='compress'&&p.status==='running')setContent('#compressPct',percent+'%');
     if(['queued','running'].includes(p.status))setTimeout(()=>poll(id,kind),kind==='face'?1000:500);
     else{
       setBusy(false);
       if(p.status==='done'){
         currentResult=p.output;updateHomeStatus(tx('Готово','Done','Готово'),p.done+'/'+p.total);
         stateProgress(kind,100);
         notify(tx('Готово! Результаты сохранены: ','Done! Results saved: ','Готово! Результати збережені: ')+p.output);
         if(kind==='compress')setContent('#compressPct',tx('Готово','Done','Готово'));
         if(kind==='face'){setContent('#faceStatus',tx('ГОТОВО','DONE','ГОТОВО'));setContent('#facePct','100%')}
         if(p.bytes_in&&kind==='compress'){
           const saved=100*(1-p.bytes_out/p.bytes_in);
           const info=$('#page-compress .panel-desc');
           if(info)info.textContent=tx('Обработано ','Processed ','Оброблено ')+p.done+tx(' файлов, изменение размера: ',' files, size difference: ',' файлів, зміна розміру: ')+saved.toFixed(1)+'%';
         }
         if(kind!=='face')showResultAction(p.output);
         if(kind==='face'){setContent('#faceResultText',tx('Свапы сохранены в выбранной папке','Swaps saved in selected folder','Свапи збережено у вибраній теці'));$('#faceResultOpen').disabled=false;}

else if(kind==='crop'){
  const oldPreview=document.getElementById('psPreviewResult');
  if(oldPreview){
    try{URL.revokeObjectURL(oldPreview.src)}catch(_){}
    oldPreview.remove();
  }
  document.getElementById('cropClearBatch')?.click();
}else showPreview(id,kind).catch(()=>{});

       } else if(p.status==='partial'){
         currentResult=p.output;
         updateHomeStatus(tx('Частично готово','Partially complete','Частково готово'),p.done+'/'+p.total);
         setContent('#faceStatus',tx('ЧАСТИЧНО ГОТОВО','PARTIALLY DONE','ЧАСТКОВО ГОТОВО'));
         setContent('#faceResultText',tx('Скачанные фото сохранены в папке результатов','Downloaded photos are saved in the results folder','Завантажені фото збережені у теці результатів'));
         const resultButton=$('#faceResultOpen');
         if(resultButton)resultButton.disabled=false;
         if(p.output)showResultAction(p.output);
         notify(tx('Facy сохранил не все результаты: ','Facy did not deliver every result: ','Facy зберіг не всі результати: ')+p.done+'/'+p.total,true);
} else if(p.status==='cancelled'){updateHomeStatus(tx('Отменено','Cancelled','Скасовано'));notify(tx('Задача отменена','Task cancelled','Завдання скасовано'))}
       else{updateHomeStatus(tx('Ошибка','Error','Помилка'));notify(p.error||p.message||'Operation failed',true)}
     }
   }catch(err){
     if(currentJob?.id!==id)return;
     pollFailures++;
     if(pollFailures===1 || pollFailures%15===0)
       notify(tx('Временно нет связи с приложением. Проверка продолжится автоматически.',
                 'Connection interrupted. Automatically retrying status updates.',
                 'Зв’язок перервано. Перевірка стану продовжиться автоматично.'),true);
     if(kind==='face')setContent('#faceStatus',tx(
       'Проверяем соединение. Свапы могут продолжать обрабатываться.',
       'Reconnecting. Face Swap may still be processing.',
       'Відновлюємо зв’язок. Обробка облич може тривати.'));
     const retryMs=Math.min(15000,1000*Math.pow(2,Math.min(pollFailures,4)));
     setTimeout(()=>poll(id,kind),retryMs);
   }
 }

 async function showPreview(id,kind){
   const info=await call('/api/results/'+id);
   if(!info.files?.length)return;
   const name=info.files[0];
   const response=await fetch('/api/results/'+id+'/file?name='+encodeURIComponent(name),{headers:{'X-PhotoStudio-Token':token}});
   if(!response.ok)return;
   const image=URL.createObjectURL(await response.blob());
   const zone=kind==='crop'?'#cropArea':null;
   const host=zone?$(zone):null;
   if(!host)return;
   let img=$('#psPreviewResult');if(img){URL.revokeObjectURL(img.src);img.remove()}
   img=document.createElement('img');img.id='psPreviewResult';img.src=image;img.alt=tx('Обработанное изображение','Processed image','Оброблене зображення');
   img.style.cssText='display:block;width:100%;max-height:320px;object-fit:contain;border-radius:12px;border:1px solid var(--c1);';
   host.append(img);
 }

 function showResultAction(path){
   let el=$('#psOpenResults');
   if(!el){el=document.createElement('button');el.id='psOpenResults';el.className='btn primary';
     el.textContent=tx('Открыть папку результатов','Open results folder','Відкрити папку результатів');
     const el0=$('#page-home .workspace-actions')||$('#page-face .toolbar-inline');
     (el0||document.querySelector('.top-actions')).append(el);
     el.addEventListener('click',()=>reveal());
   }
   el.hidden=false;
 }
 async function reveal(){if(!currentResult){notify(tx('Пока нет готовых результатов','No results yet','Поки немає готових результатів'));return}
   try{await call('/api/reveal',{path:currentResult})}catch(e){notify(e.message,true)} }
 const actionMap={
  faceRun:()=>create('face',['faceSource','faceTarget']),
  cropDemo:()=>{
    const active=$$('#cropRatios button').find(b=>b.classList.contains('active'));
    return create('crop',['crop'],{ratio:'free',cropRect:{...window.PhotoStudioCropSelection},cropRects:window.PhotoStudioCropRects,cropRatios:window.PhotoStudioCropRatios})},
  compressDemo:()=>{
    const active=$$('#formatTabs button').find(b=>b.classList.contains('active'));
    return create('compress',['compress'],{maxMB:Number(document.querySelector('#compressLimits .active')?.dataset.maxMb||2)})},
  sortPreview:()=>create('sort',['sort'],{perFolder:Number($('#perFolder').value)}),
  shuffleDo:()=>create('shuffle',['shuffle'],{withinFolders:!!$$('#shuffleModes button').find(b=>b.classList.contains('active')&&b.dataset.mode==='folders')})
 };
 // Capture on the document BEFORE the demo's click handlers on buttons.
 document.addEventListener('click',e=>{
   const target=e.target.closest('button,a');if(!target)return;
   if(target.id==='sortFolderPick'){e.preventDefault();$('#sortFolderInput').click();return;}
   if(target.id==='shuffleFolderPick'){e.preventDefault();$('#shuffleFolderInput').click();return;}
   if(Object.hasOwn(actionMap,target.id)){
     e.preventDefault();e.stopImmediatePropagation();actionMap[target.id]().catch(()=>{});return;
   }
   if(target.id==='homeToCompress'&&chosen.home.length)chosen.compress=[...chosen.home];
   if(target.id==='faceCancel'){
     e.preventDefault();e.stopImmediatePropagation();
     if(uploadController)uploadController.abort();
     if(currentJob)call('/api/jobs/'+currentJob.id+'/cancel',{}).then(()=>notify(tx('Остановка запрошена','Cancellation requested','Скасування запитано'))).catch(err=>notify(err.message,true));
     return;
   }
   if(target.id==='psOpenResults'||target.id==='faceResultOpen'){
     e.preventDefault();e.stopImmediatePropagation();reveal();return;
   }
   if(target.id==='facyConnect'){
     e.preventDefault();e.stopImmediatePropagation();startFacy();return;
   }
   if(target.id==='facyHow'){
     e.preventDefault();e.stopImmediatePropagation();verifyFacy();return;
   }
   if(target.id==='facyDisconnect'){
     e.preventDefault();e.stopImmediatePropagation();disconnectFacy();return;
   }
   if(target.id==='faceChooseOutput'){
     e.preventDefault();e.stopImmediatePropagation();chooseOutput();return;
   }
   if(target.id==='faceRestoreOutput'){
     e.preventDefault();e.stopImmediatePropagation();resetOutput();return;
   }
 },true);
 function facyBanner(mode,title,description){
   const el=$('#facyInlineNote'); if(!el)return;
   el.hidden=false;el.dataset.state=mode;
   $('#facyInlineTitle').textContent=title;
   $('#facyInlineDetail').textContent=description;
   el.querySelector('.facy-state-icon').textContent=mode==='success'?'✓':mode==='error'?'!':'·';
   // New state replays an entry animation; successful confirmation stays visible.
   el.style.animation='none';void el.offsetWidth;el.style.animation='';
 }
 async function startFacy(){
   try{const auth=await call('/api/facy/connect',{});
     facyBanner('working',tx('Открываем Facy AI','Opening Facy AI','Відкриваємо Facy AI'),tx('Войди в появившемся окне, затем нажми «Проверить вход».','Sign in in the opened browser, then select Verify sign-in.','Увійди в браузері, потім натисни «Перевірити вхід».'));
     watchFacy();
   }catch(e){facyBanner('error',tx('Не удалось подключиться','Connection failed','Не вдалося підключитися'),e.message)}
 }
 async function verifyFacy(){
   try{await call('/api/facy/complete',{});
     facyBanner('working',tx('Проверяем вход…','Verifying sign-in…','Перевіряємо вхід…'),tx('Ожидаем подтверждения сессии от браузера Facy.','Waiting for the Facy session verification.','Очікуємо підтвердження сесії Facy.'));
     watchFacy();
   }catch(e){facyBanner('error',tx('Вход не подтверждён','Sign-in not verified','Вхід не підтверджено'),e.message)}
 }
 async function disconnectFacy(){
   try{await call('/api/facy/disconnect',{});
     if(authTimer){clearInterval(authTimer);authTimer=null}
     setContent('#facyConnectionState',tx('НЕ ПОДКЛЮЧЕНО','NOT CONNECTED','НЕ ПІДКЛЮЧЕНО'));
     facyBanner('working',tx('Facy AI отключён','Facy AI disconnected','Facy AI відключено'),tx('Сессию можно подключить снова.','You can reconnect whenever needed.','Сесію можна підключити знову.'));
   }catch(e){facyBanner('error',tx('Ошибка','Error','Помилка'),e.message)}
 }
 function watchFacy(){if(authTimer)clearInterval(authTimer);
   let attempts=0;
   const pollAuth=async()=>{try{
     const auth=await call('/api/facy/status');
     const states={connected:tx('ПОДКЛЮЧЕНО','CONNECTED','ПІДКЛЮЧЕНО'),starting:tx('ЗАПУСК','STARTING','ЗАПУСК'),browser_open:tx('ОЖИДАЕМ ВХОД','WAITING FOR SIGN-IN','ОЧІКУЄМО ВХОДУ'),disconnected:tx('НЕ ПОДКЛЮЧЕНО','NOT CONNECTED','НЕ ПІДКЛЮЧЕНО'),error:tx('ОШИБКА','ERROR','ПОМИЛКА')};
     setContent('#facyConnectionState',states[auth.status]||auth.status);
     if(auth.detail)setContent('#facyHint',auth.detail);
     if(auth.connected){
       if(authTimer)clearInterval(authTimer);authTimer=null;
       facyBanner('success',tx('Успешно! Facy AI подключён','Success! Facy AI connected','Успішно! Facy AI підключено'),tx('Сессия подтверждена. Теперь можно запускать Face Swap.','Session verified. You can start Face Swap.','Сесію підтверджено. Можна запускати Face Swap.'));
     }else if(auth.status==='error'){
       if(authTimer)clearInterval(authTimer);authTimer=null;
       facyBanner('error',tx('Ошибка подключения Facy','Facy connection error','Помилка підключення Facy'),auth.detail||'');
     }else if(++attempts>450){clearInterval(authTimer);authTimer=null;}
   }catch(_){if(authTimer)clearInterval(authTimer);authTimer=null}};
   pollAuth();authTimer=setInterval(pollAuth,850)
 }
 function refreshFaceCount(){
   const n=(chosen.faceTarget||[]).length;
   setContent('#faceTargetCount',n+' '+(lng()==='en'?'files':lng()==='uk'?'файлів':'файлов'));
 }
 const queue=$('#faceTargetList');if(queue){
   new MutationObserver(()=>refreshFaceCount()).observe(queue,{childList:true});
   refreshFaceCount();
 }
 function updateOutput(data){
   const el=$('#faceOutputPath');if(el){el.textContent=data.path||'—';el.title=data.path||'';}
 }
 async function fetchOutput(){try{updateOutput(await call('/api/face/output'))}catch(e){notify(e.message,true)}}
 async function chooseOutput(){
   const btn=$('#faceChooseOutput');btn.disabled=true;
   try{const data=await call('/api/face/output/select',{});updateOutput(data);
     if(!data.cancelled){setContent('#faceResultText',tx('Новая папка выбрана. Следующий Face Swap сохранится в отдельной подпапке.','New folder selected. The next Face Swap will use a new subfolder.','Нову теку вибрано. Наступний Face Swap збереже файли в окремій підтеці.'));notify(tx('Папка сохранения обновлена','Output folder updated','Теку збереження оновлено'));}
   }catch(e){notify(e.message,true)}finally{btn.disabled=false}
 }
 async function resetOutput(){
   try{updateOutput(await call('/api/face/output/reset',{}));notify(tx('Папка по умолчанию восстановлена','Default output folder restored','Типову теку відновлено'))}
   catch(e){notify(e.message,true)}
 }
 fetchOutput();
 // Re-opened Face Swap view reflects existing authenticated runtime state.
 call('/api/facy/status').then(auth=>{if(auth.connected){setContent('#facyConnectionState',tx('ПОДКЛЮЧЕНО','CONNECTED','ПІДКЛЮЧЕНО'));facyBanner('success',tx('Facy AI подключён','Facy AI connected','Facy AI підключено'),tx('Сессия подтверждена.','Session verified.','Сесію підтверджено.'));}}).catch(()=>{});
 function setLabel(id,text){const b=$('#'+id);if(b)b.textContent=text}
 setLabel('faceRun',tx('Начать Face Swap','Start Face Swap','Почати Face Swap'));
 setLabel('cropDemo',tx('Кадрировать и сохранить','Crop and save','Обрізати та зберегти'));
 setLabel('compressDemo',tx('Сжать и сохранить','Compress and save','Стиснути та зберегти'));
 setLabel('sortPreview',tx('Распределить и сохранить','Sort and save','Розподілити та зберегти'));
 setLabel('shuffleDo',tx('Перемешать и сохранить','Shuffle and save','Перемішати та зберегти'));
 setLabel('facyConnect',tx('Подключить Facy AI','Connect Facy AI','Підключити Facy AI'));
 setLabel('facyHow',tx('Проверить вход','Verify sign-in','Перевірити вхід'));
 setLabel('facyDisconnect',tx('Отключить','Disconnect','Відключити'));
 setLabel('faceChooseOutput',tx('Выбрать папку','Choose folder','Обрати теку'));
 setLabel('faceRestoreOutput',tx('По умолчанию','Default','Типова тека'));
 // Make initial state precise: no fake output claims.
 for(const p of $$('.page p,.page .panel-desc,.page .empty-state')){
   const txt=p.textContent.trim();
   if(/демо|демонстрац|прототип|будет реализован|не выполняется|не сохраня|недоступн|пример отклика|ничего на диск|browser/i.test(txt)){
     if(txt.includes('Facy'))p.textContent=tx('Для обработки нужен интернет и авторизация на facy.ai.','Requires an internet connection and Facy sign-in.','Для обробки потрібні інтернет і вхід на facy.ai.');
     else if(p.closest('#page-motion')){} // explain interactive effects there
     else p.textContent=tx('Оригиналы не изменяются. Результаты сохраняются в папку «Изображения / Photo Studio».','Originals are never modified. Results are saved under Pictures / Photo Studio.','Оригінали не змінюються. Результати зберігаються в Зображення / Photo Studio.');
   }
 }
 const statusEl=$('.rail-end .tiny-row span:last-child');if(statusEl)statusEl.textContent=tx('Локальное приложение','Local application','Локальний застосунок');
 const note=$('.workspace-status');if(note)note.innerHTML='<span class="online-dot"></span> '+tx('Готово к работе · Без облачного хранения','Ready · No cloud storage','Готово · Без хмарного зберігання');
 // User chooses images, not stock content. No embedded stock photographs.
 call('/api/status').then(s=>{window.PhotoStudioRuntime={connected:true,version:s.version,chosen};}).catch(err=>notify(err.message,true));
})();
