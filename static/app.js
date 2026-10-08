const $ = (id) => document.getElementById(id);
const state = { files: [], imageUrls: [], data: null, currentPage: 0, jobId: null, finalized: false };

function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showMessage(message, error=false) { const el=$('message'); el.textContent=message; el.classList.remove('hidden'); el.classList.toggle('error',error); el.scrollIntoView({behavior:'smooth',block:'nearest'}); }
function clearMessage() { $('message').classList.add('hidden'); }

async function loadStatus() {
  const response=await fetch('/api/status'); const data=await response.json();
  $('key-status').textContent=data.key_configured ? 'Gemini connected' : 'API key needed';
  $('key-status').classList.toggle('off',!data.key_configured);
  $('training-count').textContent=`${data.verified_examples} verified example${data.verified_examples===1?'':'s'}`;
  return data;
}

function setFinalized(value) {
  state.finalized=value;
  $('download-excel').disabled=!value;
  $('download-pdf').disabled=!value;
  $('final-button').textContent=value?'✓ Final saved — update if edited':'✓ Final — save verified example';
}

function setFiles(files) {
  const known=new Set(state.files.map(f=>`${f.name}:${f.size}:${f.lastModified}`));
  for(const file of files) { const id=`${file.name}:${file.size}:${file.lastModified}`; if(!known.has(id)){state.files.push(file);known.add(id);} }
  renderFiles();
}
function renderFiles() {
  $('file-list').innerHTML=state.files.map((file,i)=>`<div class="file-row"><span class="file-index">${i+1}</span><img class="file-thumb" src="${file.type.startsWith('image/')?escapeHtml(URL.createObjectURL(file)):''}" alt=""><span class="file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span><div class="file-controls"><button class="mini-button" data-file-action="up" data-index="${i}" title="Move up">↑</button><button class="mini-button" data-file-action="down" data-index="${i}" title="Move down">↓</button><button class="mini-button" data-file-action="remove" data-index="${i}" title="Remove">×</button></div></div>`).join('');
  $('upload-count').textContent=state.files.length?`${state.files.length} file${state.files.length===1?'':'s'} selected`:'No files selected';
  $('process-button').disabled=!state.files.length;
}

function setPage(index) {
  state.currentPage=index;
  document.querySelectorAll('.page-tab').forEach((el,i)=>el.classList.toggle('active',i===index));
  $('page-indicator').textContent=`${index+1} / ${state.imageUrls.length}`;
  const url=state.imageUrls[index];
  $('source-view').classList.remove('zoomed');
  $('source-view').innerHTML=url?`<img src="${url}" alt="Original packing list page ${index+1}">`:'<span>PDF page preview unavailable</span>';
}

function prepareImages() {
  state.imageUrls.forEach(url=>URL.revokeObjectURL(url));
  const pageCount=state.pageCount||state.files.length;
  state.imageUrls=Array.from({length:pageCount},(_,i)=>state.jobId?`/api/jobs/${state.jobId}/pages/${i}`:(state.files[i]?.type.startsWith('image/')?URL.createObjectURL(state.files[i]):null));
  $('page-tabs').innerHTML=Array.from({length:pageCount},(_,i)=>`<button type="button" class="page-tab" data-page="${i}">Page ${i+1}</button>`).join('');
  if(state.imageUrls.length) setPage(0);
}

function updateSummary() {
  if(!state.data)return;
  $('box-count').textContent=`${state.data.boxes.length} boxes`;
  const count=state.data.boxes.reduce((sum,box)=>sum+box.items.filter(item=>item.code||item.size||item.quantity||item.note).length,0);
  $('item-count').textContent=`${count} items`;
}

function rowHtml(boxIndex,item,index) {
  const uncertain=item.needs_review?' uncertain':'';
  const f=(name,value,placeholder='')=>`<input class="item-input${uncertain}" data-box="${boxIndex}" data-row="${index}" data-field="${name}" value="${escapeHtml(value)}" placeholder="${placeholder}" title="${escapeHtml(item.source_text||'')}">`;
  const source=item.source_page?`<button class="source-link" data-page="${item.source_page-1}" type="button">Page ${item.source_page}</button>`:'';
  return `<tr><td>${index+1}</td><td>${f('code',item.code,'PC-404')}</td><td>${f('size',item.size,'S / M')}</td><td>${f('quantity',item.quantity??'','Qty')}</td><td>${f('note',item.note,'Only base / cup')}</td><td>${source}</td><td><button class="mini-button" type="button" data-row-action="remove" data-box="${boxIndex}" data-row="${index}" title="Remove row">×</button></td></tr>`;
}

function renderBoxes() {
  const boxes=state.data.boxes;
  $('boxes').innerHTML=boxes.map((box,bi)=>{
    const rows=Math.max(5,box.items.length);
    const body=Array.from({length:rows},(_,i)=>rowHtml(bi,box.items[i]||{code:'',size:'',quantity:null,note:''},i)).join('');
    const needs=box.needs_review||box.items.some(x=>x.needs_review);
    return `<article class="box-card${needs?' review':''}"><div class="box-header"><div class="box-title">Box <input class="box-number" type="number" min="1" value="${box.number}" data-box-number="${bi}">${needs?'<span class="review-chip">Check handwriting</span>':''}</div><div class="box-actions">${needs?`<button class="link-button" type="button" data-box-action="checked" data-box="${bi}">Mark checked</button>`:''}<button class="link-button" type="button" data-box-action="add" data-box="${bi}">+ Add row</button><button class="link-button danger" type="button" data-box-action="remove" data-box="${bi}">Remove box</button></div></div><table class="entry-table"><thead><tr><th>No.</th><th>Item code</th><th>Size</th><th>Qty.</th><th>Note</th><th>Source</th><th></th></tr></thead><tbody>${body}</tbody></table><div class="box-footer"><span>${box.items.length} detected item${box.items.length===1?'':'s'}</span><span>${Math.max(0,5-box.items.length)} blank print row${Math.max(0,5-box.items.length)===1?'':'s'} minimum</span></div></article>`;
  }).join('');
  updateSummary();
}

function renderWarnings() {
  const warnings=state.data.warnings||[];
  $('warnings').classList.toggle('hidden',!warnings.length);
  $('warnings').innerHTML=warnings.length?`<strong>Items to check</strong><ul>${warnings.map(w=>`<li>${escapeHtml(w)}</li>`).join('')}</ul>`:'';
}

function showReview(data) {
  state.data=data;
  setFinalized(false);
  $('customer').value=data.customer||'';
  $('packing-date').value=data.packing_date||new Date().toLocaleDateString('en-GB');
  $('private-mark').value=data.private_mark||'';
  $('transport').value=data.transport||'';
  $('review-section').classList.remove('hidden');
  document.querySelectorAll('.step').forEach((el,i)=>el.classList.toggle('active',i===1));
  prepareImages();renderWarnings();renderBoxes();
  $('review-section').scrollIntoView({behavior:'smooth'});
}

async function processFiles() {
  clearMessage();
  const form=new FormData(); state.files.forEach(f=>form.append('files',f));
  $('process-button').disabled=true;$('progress').classList.remove('hidden');$('progress-text').textContent='Uploading pages…';
  try {
    const response=await fetch('/api/recognize',{method:'POST',body:form});
    const info=await response.json();
    if(!response.ok)throw new Error(info.detail||'Upload failed');
    state.jobId=info.job_id;
    state.pageCount=info.total;
    while(true){
      await new Promise(resolve=>setTimeout(resolve,1100));
      const poll=await fetch(`/api/jobs/${state.jobId}`); const job=await poll.json();
      $('progress-fill').style.width=`${Math.round(100*job.completed/job.total)}%`;
      $('progress-text').textContent=`Read ${job.completed} of ${job.total} pages…`;
      if(job.status==='done'){showReview(job.result);if(job.cache_hit)showMessage('These exact photos were finalized before. The verified list was loaded without a new AI scan.');break;}
      if(job.status==='error') {if(job.result)showReview(job.result);showMessage(job.error||'Recognition failed',true);break;}
    }
  }catch(error){alert(error.message);}finally{$('process-button').disabled=false;$('progress').classList.add('hidden');}
}

async function importTraining() {
  clearMessage();
  const sheet=$('training-sheet').files[0], photos=Array.from($('training-photos').files);
  if(!sheet||!photos.length){showMessage('Choose a finished Excel and its matching photos.',true);return;}
  const form=new FormData();form.append('sheet',sheet);photos.forEach(photo=>form.append('photos',photo));
  $('import-training').disabled=true;
  try{
    const response=await fetch('/api/training/import',{method:'POST',body:form});
    const info=await response.json();
    if(!response.ok)throw new Error(info.detail||'Import failed');
    state.jobId=info.job_id;state.pageCount=info.total;
    showReview(info.result);
    showMessage(`Imported ${info.result.boxes.length} filled boxes. Compare with photos, then press Final to add this verified example.`);
  }catch(error){showMessage(error.message,true);}finally{$('import-training').disabled=false;}
}

async function finalizeList() {
  clearMessage();
  if(!state.jobId){showMessage('Process or import source photos first.',true);return;}
  const button=$('final-button');button.disabled=true;
  try{
    const response=await fetch('/api/finalize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:state.jobId,data:collectData()})});
    const info=await response.json();
    if(!response.ok)throw new Error(info.detail||'Could not finalize');
    setFinalized(true);
    $('training-count').textContent=`${info.verified_examples} verified example${info.verified_examples===1?'':'s'}`;
    showMessage('Final saved. Excel and PDF are ready. This confirmed result will guide future photo recognition.');
  }catch(error){showMessage(error.message,true);}finally{button.disabled=false;}
}

function collectData() {
  const data=structuredClone(state.data);
  data.customer=$('customer').value.trim(); data.packing_date=$('packing-date').value.trim(); data.private_mark=$('private-mark').value.trim(); data.transport=$('transport').value.trim();
  data.boxes=data.boxes.map(box=>({...box,items:box.items.filter(item=>item.code||item.size||item.quantity!==null&&item.quantity!==''||item.note).map(item=>({...item,quantity:item.quantity===''?null:item.quantity}))}));
  return data;
}

async function exportFile(kind,preview=false) {
  clearMessage();
  try{
    if(!preview&&!state.finalized)throw new Error('Check the list and press Final before downloading.');
    const data=collectData();
    const response=await fetch(`/api/export/${kind}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!response.ok){const error=await response.json();throw new Error(error.detail||'Export failed');}
    const blob=await response.blob();const url=URL.createObjectURL(blob);
    if(preview){const opened=window.open(url,'_blank');if(!opened)showMessage('Popup blocked. Allow popups to preview PDF.',true);}
    else{const filename=(response.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/)?.[1]||`packing-list.${kind}`;const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();showMessage(`${kind.toUpperCase()} downloaded.`);}
    setTimeout(()=>URL.revokeObjectURL(url),60000);
  }catch(error){showMessage(error.message,true);}
}

$('file-input').addEventListener('change',e=>{setFiles(e.target.files);e.target.value='';});
const drop=$('drop-zone');
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('dragging');});
drop.addEventListener('dragleave',()=>drop.classList.remove('dragging'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('dragging');setFiles(e.dataTransfer.files);});
$('file-list').addEventListener('click',e=>{const button=e.target.closest('[data-file-action]');if(!button)return;const i=Number(button.dataset.index),action=button.dataset.fileAction;if(action==='remove')state.files.splice(i,1);else{const j=i+(action==='up'?-1:1);if(j>=0&&j<state.files.length)[state.files[i],state.files[j]]=[state.files[j],state.files[i]];}renderFiles();});
$('process-button').addEventListener('click',processFiles);
$('import-training').addEventListener('click',importTraining);
$('final-button').addEventListener('click',finalizeList);
$('settings-button').addEventListener('click',()=>$('settings-dialog').showModal());
$('save-key').addEventListener('click',async()=>{const key=$('api-key').value.trim();$('settings-error').textContent='';try{const response=await fetch('/api/settings/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});if(!response.ok)throw new Error((await response.json()).detail||'Could not save key');$('api-key').value='';$('settings-dialog').close();await loadStatus();}catch(error){$('settings-error').textContent=error.message;}});
$('page-tabs').addEventListener('click',e=>{const button=e.target.closest('[data-page]');if(button)setPage(Number(button.dataset.page));});
$('source-view').addEventListener('click',()=>{$('source-view').classList.toggle('zoomed');});
document.querySelectorAll('#customer,#packing-date,#private-mark,#transport').forEach(input=>input.addEventListener('input',()=>setFinalized(false)));
$('boxes').addEventListener('input',e=>{const target=e.target;setFinalized(false);if(target.dataset.boxNumber!==undefined){state.data.boxes[Number(target.dataset.boxNumber)].number=Number(target.value);updateSummary();return;}if(target.dataset.field!==undefined){const bi=Number(target.dataset.box),ri=Number(target.dataset.row);const box=state.data.boxes[bi];while(box.items.length<=ri)box.items.push({code:'',size:'',quantity:null,note:'',source_page:null,source_text:'',needs_review:false});box.items[ri][target.dataset.field]=target.dataset.field==='quantity'?(target.value===''?null:Number(target.value)):target.value;updateSummary();}});
$('boxes').addEventListener('click',e=>{const page=e.target.closest('[data-page]');if(page){setPage(Number(page.dataset.page));return;}const row=e.target.closest('[data-row-action]');if(row){setFinalized(false);const box=state.data.boxes[Number(row.dataset.box)];box.items.splice(Number(row.dataset.row),1);renderBoxes();return;}const button=e.target.closest('[data-box-action]');if(!button)return;setFinalized(false);const bi=Number(button.dataset.box);if(button.dataset.boxAction==='remove'){state.data.boxes.splice(bi,1);}else if(button.dataset.boxAction==='checked'){state.data.boxes[bi].needs_review=false;state.data.boxes[bi].items.forEach(item=>item.needs_review=false);}else{state.data.boxes[bi].items.push({code:'',size:'',quantity:null,note:'',source_page:null,source_text:'',needs_review:false});}renderBoxes();});
$('add-box').addEventListener('click',()=>{setFinalized(false);const number=state.data.boxes.reduce((m,b)=>Math.max(m,b.number),0)+1;state.data.boxes.push({number,items:[],needs_review:false});renderBoxes();});
$('preview-pdf').addEventListener('click',()=>exportFile('pdf',true));
$('download-excel').addEventListener('click',()=>exportFile('xlsx'));
$('download-pdf').addEventListener('click',()=>exportFile('pdf'));
loadStatus().catch(()=>{$('key-status').textContent='App disconnected';});

