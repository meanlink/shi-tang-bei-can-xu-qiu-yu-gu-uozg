import {reports} from '../api/entities.js';
import {download} from '../api/client.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {DeliveryState,CleanupStatus,CleanupItemResult,bytes} from '../constants/enums.js';

const isAdmin=()=>session.user.role==='admin';

export async function archivePage(){
 const [preview,deliveries,blobs,runs]=await Promise.all([
   reports.preview(),reports.deliveries(),reports.blobs(),reports.cleanupRuns()]);
 const stat=(label,val,hint='')=>'<div><span>'+esc(label)+'</span><strong>'+esc(val)+'</strong><small>'+esc(hint)+'</small></div>';
 const stateBadge=s=>'<span class="status '+ (s==='failed'?'failed':'')+(s==='cleaned'?'cleaned':'')+'">'+esc(DeliveryState[s]||s)+'</span>';
 const rows=deliveries.map(d=>'<tr>'
   +'<td>#'+d.id+'</td>'
   +'<td>'+stateBadge(d.state)+(d.held?' <span class="tag tag-hold">保全</span>':'')+'</td>'
   +'<td><code>'+esc(d.checksum.slice(0,16))+'…</code><br><small>blob #'+esc(d.blob_id??'—')+' · 引用 '+esc(d.size_bytes?bytes(d.size_bytes):'—')+'</small></td>'
   +'<td>'+(d.expires_at?date(d.expires_at):'长期保留')+'</td>'
   +'<td>'+date(d.created_at)+'</td>'
   +'<td>'+(d.last_error?'<span class="err">'+esc(d.last_error)+'</span>':(d.cleaned_at?date(d.cleaned_at):'—'))+'</td>'
   +'<td class="row-actions">'
   +(d.state==='active'
      ? '<button class="text-button" data-download="'+d.id+'" data-format="csv">CSV↓</button>'
        +'<button class="text-button" data-download="'+d.id+'" data-format="json">JSON↓</button>'
        +(isAdmin()?'<button class="text-button" data-hold="'+d.id+'" data-holdflag="'+(d.held?'false':'true')+'">'+(d.held?'解除保全':'保全')+'</button>'
           +'<button class="text-button" data-ret="'+d.id+'">保留期</button>':'')
      : '<span class="muted">引用已释放 · 记录可查</span>')
   +'</td></tr>').join('');
 const blobRows=blobs.map(b=>'<tr><td>#'+b.id+'</td><td><code>'+esc(b.checksum.slice(0,18))+'…</code></td>'
   +'<td>'+b.ref_count+'</td><td>'+bytes(b.size_bytes)+'</td>'
   +'<td>'+(b.reaped?'<span class="tag tag-reaped">已回收</span>':b.orphan?'<span class="tag tag-orphan">待回收</span>':'<span class="tag tag-live">在档</span>')+'</td>'
   +'<td>'+date(b.last_referenced_at)+'</td></tr>').join('');
 const runCards=runs.map(r=>'<article class="run-card" data-run="'+r.id+'">'
   +'<div><strong>批次 #'+r.id+' · '+esc(CleanupStatus[r.status]||r.status)+'</strong>'
   +'<p>'+esc(r.triggered_by)+' · 开始 '+date(r.started_at)+(r.finished_at?' · 结束 '+date(r.finished_at):' · 可续跑')+'</p>'
   +'<small>释放引用 '+r.cleaned_count+' · 缺失 '+r.missing_count+' · 失败 '+r.failed_count+' · 跳过 '+r.skipped_count
   +' · 回收 '+bytes(r.reclaimed_bytes)+'</small></div>'
   +'<button class="button small" data-run-detail="'+r.id+'">追查明细</button></article>').join('');

 return heading('06 / ARCHIVE GOVERNANCE','归档保留与空间治理',
   '同内容简报共用一份物理文件，投递记录各自可查；负责人可设保留期限或保全，到期仅释放该投递引用，最后一个引用释放才回收文件。',
   '<button class="button" id="refresh-archive">刷新 ↻</button>')
 +'<section class="panel reclaim-panel"><div class="section-title"><h2>可回收空间预览</h2><span>'+date(preview.at)+' 实时统计</span></div>'
 +'<div class="metrics reclaim-metrics">'
 +stat('在档投递',preview.active_total)
 +stat('待清理（已到期未保全）',preview.due_total)
 +stat('保全中',preview.held_total)
 +stat('文件异常',preview.failed_total)
 +stat('已清理记录',preview.cleaned_total)
 +stat('预计可回收',bytes(preview.reclaimable_bytes),'到期释放+待物理回收')
 +'</div>'
 +(preview.missing_delivery_ids.length?'<p class="warning">检测到 '+preview.missing_delivery_ids.length+' 个缺物理文件的投递（#'+preview.missing_delivery_ids.slice(0,10).join(', #')+'），清理时会标记失败而非冒充完成。</p>':'')
 +(isAdmin()?'<div class="action-row"><button class="primary" id="btn-cleanup">执行到期清理（仅释放引用）</button><button class="button" id="btn-reap">物理回收孤儿文件</button><p class="hint">清理中断后再次点击会从原批次断点续跑；物理回收只删除引用已归零的文件。</p></div>':'<p class="hint">仅业务负责人可执行清理、保全与保留期设置。</p>')
 +'</section>'

 +'<section class="panel"><div class="section-title"><h2>投递记录与生命周期</h2><span>待清理 / 保全 / 失败 / 完成 可追查</span></div>'
 +'<div class="filter-row" id="delivery-filters">'
 +['','state=active','state=cleaned','state=failed','held=true'].map((q,i)=>'<button class="button small '+(i===0?'primary':'')+'" data-filter="'+q+'">'+['全部','在档','已清理','文件异常','保全中'][i]+'</button>').join('')
 +'</div>'
 +'<div class="table-wrap"><table><thead><tr><th>投递</th><th>状态</th><th>内容指纹 / 存储</th><th>到期时间</th><th>归档时间</th><th>清理/异常</th><th>操作</th></tr></thead><tbody id="delivery-rows">'+rows+'</tbody></table></div>'
 +'</section>'

 +'<div class="two-columns"><section class="panel"><div class="section-title"><h2>内容去重存储</h2><span>'+blobs.length+' 个内容 blob</span></div>'
 +'<div class="table-wrap"><table><thead><tr><th>Blob</th><th>SHA256</th><th>引用数</th><th>大小</th><th>状态</th><th>最近引用</th></tr></thead><tbody>'+blobRows+'</tbody></table></div></section>'

 +'<section class="panel"><div class="section-title"><h2>清理批次</h2><span>可恢复 · 幂等</span></div>'
 +'<div class="schedule-list run-list">'+(runCards||'<p class="empty">尚未执行过清理。</p>')+'</div>'
 +'<div id="run-detail"></div></section></div>';
}

export function bindArchive(refresh){
 document.querySelector('#refresh-archive').onclick=refresh;
 const btnC=document.querySelector('#btn-cleanup');
 if(btnC)btnC.onclick=async()=>{try{const r=await reports.cleanupRun();notify('清理完成：释放引用 '+r.cleaned_count+' 个，缺失 '+r.missing_count+' 个，状态 '+r.status);await refresh();}catch(e){notify(e.message,true);}};
 const btnR=document.querySelector('#btn-reap');
 if(btnR)btnR.onclick=async()=>{try{const s=await reports.cleanupReap();notify('物理回收 '+s.reaped+' 个文件，释放 '+bytes(s.bytes_freed));await refresh();}catch(e){notify(e.message,true);}};
 document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=async()=>{
   document.querySelectorAll('[data-filter]').forEach(x=>x.classList.remove('primary'));b.classList.add('primary');
   try{const list=await reports.deliveries(b.dataset.filter?('?'+b.dataset.filter):'');
     const tbody=document.querySelector('#delivery-rows');
     tbody.innerHTML=list.map(d=>renderDeliveryRow(d)).join('')||'<tr><td colspan="7" class="empty">没有匹配的投递记录</td></tr>';
     bindRowActions(refresh);
   }catch(e){notify(e.message,true);}});
 bindRowActions(refresh);
 document.querySelectorAll('[data-run-detail]').forEach(b=>b.onclick=async()=>{
   try{const {run,items}=await reports.cleanupRunDetail(b.dataset.runDetail);
   document.querySelector('#run-detail').innerHTML='<h3>批次 #'+run.id+' 明细（最近 '+items.length+' 条）</h3><div class="table-wrap"><table><thead><tr><th>投递</th><th>结果</th><th>说明</th><th>回收字节</th><th>时间</th></tr></thead><tbody>'
     +items.map(i=>'<tr><td>#'+i.delivery_id+'</td><td>'+esc(CleanupItemResult[i.result]||i.result)+'</td><td>'+esc(i.detail||'—')+'</td><td>'+bytes(i.reclaimed_bytes)+'</td><td>'+date(i.processed_at)+'</td></tr>').join('')
     +'</tbody></table></div>';
   }catch(e){notify(e.message,true);}});
}

function renderDeliveryRow(d){
 const stateBadge='<span class="status '+ (d.state==='failed'?'failed':'')+(d.state==='cleaned'?'cleaned':'')+'">'+esc(DeliveryState[d.state]||d.state)+'</span>';
 return '<tr><td>#'+d.id+'</td><td>'+stateBadge+(d.held?' <span class="tag tag-hold">保全</span>':'')+'</td>'
 +'<td><code>'+esc(d.checksum.slice(0,16))+'…</code><br><small>blob #'+esc(d.blob_id??'—')+' · '+esc(d.size_bytes?bytes(d.size_bytes):'—')+'</small></td>'
 +'<td>'+(d.expires_at?date(d.expires_at):'长期保留')+'</td><td>'+date(d.created_at)+'</td>'
 +'<td>'+(d.last_error?'<span class="err">'+esc(d.last_error)+'</span>':(d.cleaned_at?date(d.cleaned_at):'—'))+'</td>'
 +'<td class="row-actions">'+(d.state==='active'
   ? '<button class="text-button" data-download="'+d.id+'" data-format="csv">CSV↓</button><button class="text-button" data-download="'+d.id+'" data-format="json">JSON↓</button>'
     +(isAdmin()?'<button class="text-button" data-hold="'+d.id+'" data-holdflag="'+(d.held?'false':'true')+'">'+(d.held?'解除保全':'保全')+'</button><button class="text-button" data-ret="'+d.id+'">保留期</button>':'')
   : '<span class="muted">引用已释放 · 记录可查</span>')+'</td></tr>';
}

function bindRowActions(refresh){
 document.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(b.dataset.download,b.dataset.format).catch(e=>notify(e.message,true)));
 document.querySelectorAll('[data-hold]').forEach(b=>b.onclick=async()=>{
   const hold=b.dataset.holdflag==='true';
   const reason=hold?(prompt('保全原因（可留空）：')||'负责人保全'):null;
   try{await reports.hold(b.dataset.hold,hold,reason);notify(hold?'已保全，到期清理将跳过该投递':'已解除保全');await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-ret]').forEach(b=>b.onclick=async()=>{
   const v=prompt('设置保留天数（1~3650；留空表示长期保留）：');
   if(v===null)return;
   try{const retention_days=v.trim()===''?null:Number(v);await reports.setDeliveryRetention(b.dataset.ret,retention_days);notify('保留期限已更新');await refresh();}catch(e){notify(e.message,true);}});
}
