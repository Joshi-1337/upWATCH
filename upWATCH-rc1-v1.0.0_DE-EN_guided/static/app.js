'use strict';

/* upwatch touch-safe widget focus v1 */
// Safari may keep :hover on a tapped widget after closing its editor.
// Keep a touch-specific class independent of CSS hover/pointer media queries.
window.addEventListener('touchstart',()=>document.documentElement.classList.add('upwatch-touch'),{passive:true});
window.addEventListener('pointerdown',event=>{
  if(event.pointerType==='touch'||event.pointerType==='pen')document.documentElement.classList.add('upwatch-touch');
  else if(event.pointerType==='mouse')document.documentElement.classList.remove('upwatch-touch');
},{passive:true});
// The dialog can restore focus to the previously tapped edit button.
window.addEventListener('DOMContentLoaded',()=>{
  const editor=document.querySelector('#editor');
  if(editor)editor.addEventListener('close',()=>{
    if(document.documentElement.classList.contains('upwatch-touch') &&
       document.activeElement instanceof HTMLElement &&
       document.activeElement.closest('#dashboard .card'))document.activeElement.blur();
  });
});

const $=s=>document.querySelector(s);
const el=(tag,cl='',text='')=>{const n=document.createElement(tag);if(cl)n.className=cl;n.textContent=text;return n};
let state={endpoints:[],groups:[],tags:[],group_preferences:{},csrf:''},editing=null,refreshing=false,toastTimer,filter='all',assigningWidget=null;
function toast(message,bad=false){const n=$('#toast');n.textContent=message;n.className=bad?'error':'';n.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.hidden=true,4000)}
function showLogin(){state={endpoints:[],csrf:''};$('#login').hidden=false;$('#dashboard').hidden=true;$('#login-password').value=''}
async function api(path,method='GET',body){const ctrl=new AbortController(),tm=setTimeout(()=>ctrl.abort(),15000);try{const r=await fetch(path,{method,credentials:'same-origin',signal:ctrl.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok){if(r.status===401&&path!='/api/login')showLogin();throw Error(window.upwatchI18n?.translate(data.error||'Anfrage fehlgeschlagen')||data.error||'Anfrage fehlgeschlagen')}return data}finally{clearTimeout(tm)}}
function date(ts){return ts?new Date(ts*1000).toLocaleString((window.upwatchI18n?.getLanguage()==='en'?'en-GB':'de-DE')):'Noch nie'}
function interval(t){return t>=3600?(t/3600).toFixed(t%3600?1:0)+' h':t>=60?(t/60).toFixed(t%60?1:0)+' min':t+' s'}
function statusText(e){return e.status==='down'&&e.last_error_code==='PROBE_ERROR'?'Prüffehler':e.maintenance_active?'Wartung':e.dependency_blocked&&e.status==='down'?'Down (abhängig)':!e.status?'Ausstehend':e.status==='up'?'Verfügbar':e.status==='down'?'Down':e.status==='warning'?'Warnung':'Fehler'}
// The WAN-DNS result detail contains the public egress IPv4 on success and on
// IP mismatches. Never mistake an arbitrary DNS answer or hostname for the WAN IP.
function wanDnsIpv4(e){
  const detail=String(e.detail||'');
  const match=detail.match(/^IPv4\s+((?:\d{1,3}\.){3}\d{1,3})(?=\s|$)/i)
    ||detail.match(/\bweicht von\s+((?:\d{1,3}\.){3}\d{1,3})(?=\s|$)/i);
  if(!match)return null;
  const parts=match[1].split('.').map(Number);
  return parts.length===4&&parts.every(n=>Number.isInteger(n)&&n>=0&&n<=255)?match[1]:null;
}
function wanDnsSummary(e){
  if(!e.checked)return 'Noch keine Prüfung';
  if(e.status==='up')return 'DNS korrekt';
  if(e.status==='warning')return 'DNS-Warnung';
  return e.status==='down'?'DNS-Abweichung':'DNS-Prüfung';
}
function action(label,ico,fn){const b=el('button','icon-button',ico);b.type='button';b.title=label;b.setAttribute('aria-label',label);b.onclick=fn;return b}
async function refresh(){if(refreshing||$('#dashboard').hidden)return;refreshing=true;try{const data=await api('/api/state');state=data;syncOrganizationFilters();applyRoleUI();render();$('#last-sync').textContent='Synchronisiert um '+date(data.now);$('#connection-warning').hidden=!!data.worker_healthy&&!(data.stale_count>0);$('#connection-warning').textContent=data.worker_healthy?`${data.stale_count} Monitor(e) ohne aktuelle Messung – gespeicherter Status möglicherweise veraltet.`:'Monitoring-Worker nicht erreichbar – bisherige UP-Zustände können veraltet sein.';$('.monitor-live').title=data.worker_healthy?'Worker aktiv':'Worker-Heartbeat fehlt';}catch(e){$('#connection-warning').hidden=false;$('#last-sync').textContent=e.message}finally{refreshing=false}}
function applyRoleUI(){
  const role=state.role||'viewer',admin=role==='admin',editable=role!=='viewer';
  for(const id of ['add','add-empty','check-all','organize-open']){const n=$('#'+id);if(n){const hide=!editable&&(id==='add'||id==='add-empty')||(!admin&&(id==='check-all'||id==='organize-open'));n.hidden=hide;n.style.display=hide?'none':''}}
  for(const id of ['users-tab','smtp-tab','appearance-tab','snmp-tab','alerts-tab','audit-tab']){const n=$('#'+id);if(n){n.hidden=!admin;n.style.display=admin?'':'none'}}
  if(!admin)$('#snmp-profiles-panel').hidden=true;
  for(const id of ['dependencies-open','maintenance-open']){const n=$('#'+id);if(n){n.hidden=!editable;n.style.display=editable?'':'none'}}
  for(const n of document.querySelectorAll('[data-tool]')){
    const id=n.dataset.tool;n.hidden=(!editable&&['organize-open','check-all','dependencies-open','maintenance-open'].includes(id))||(!admin&&['organize-open','check-all'].includes(id));n.style.display=n.hidden?'none':'';
  }
  const desktop=$('#desktop-tools');if(desktop)desktop.hidden=false;
  const preview=$('#alarm-preview-button');if(preview)preview.hidden=!editable;
  const audit=document.querySelector('[data-export="audit"]');if(audit)audit.hidden=!admin;
  // Presentation-only; backend rejects forbidden API calls independently.
}
function render(){const all=state.endpoints;$('#total').textContent=all.length;$('#up').textContent=all.filter(x=>!x.maintenance_active&&x.status==='up'&&!x.stale).length;$('#warn').textContent=all.filter(x=>!x.maintenance_active&&x.status==='warning').length;$('#down').textContent=all.filter(x=>!x.maintenance_active&&x.status==='down').length;$('#section-count').textContent=all.length;$('#maintenance-count').textContent=all.filter(x=>x.maintenance_active).length;
const needle=$('#search').value.trim().toLowerCase();const f=$('#filter').value;const gf=$('#group-filter').value,tf=$('#tag-filter').value;const selected=all.filter(x=>(x.alias.toLowerCase().includes(needle)||x.target.toLowerCase().includes(needle)||x.tags.some(t=>t.name.toLowerCase().includes(needle)))&&(f==='all'||f==='pending'&&!x.status||f==='maintenance'&&x.maintenance_active||x.status===f)&&(gf==='all'||(gf==='none'?!x.group_id:x.group_id===gf))&&(tf==='all'||x.tags.some(t=>t.id===tf)));$('#empty').hidden=all.length>0;$('#no-results').hidden=!all.length||selected.length>0;
const grid=$('#grid'),scroll=$('.widget-scroll'),oldScroll=scroll.scrollTop;grid.replaceChildren();const visibleGroups=state.groups||[];const sections=new Map();
const ensureSection=(gid)=>{const key=gid||'none';if(sections.has(key))return sections.get(key);const g=visibleGroups.find(x=>x.id===gid);const section=el('section','widget-group');section.dataset.groupId=gid||'';section.dataset.groupKey=key;const heading=el('div','widget-group-heading');const toggle=el('button','group-toggle',g?'▾':'▾');toggle.type='button';toggle.setAttribute('aria-label',g?'Gruppe '+g.name+' ein- oder ausklappen':'Ohne Gruppe ein- oder ausklappen');const title=el('strong','',g?g.name:'Ohne Gruppe');const stats=el('span','group-stats','');heading.append(toggle,title,stats);const holder=el('div','grid group-grid');section.append(heading,holder);const collapsed=!!(g&&state.group_preferences?.[g.id]);holder.hidden=collapsed;toggle.textContent=collapsed?'▸':'▾';toggle.onclick=async()=>{const next=!holder.hidden;holder.hidden=next;toggle.textContent=next?'▸':'▾';if(g){state.group_preferences[g.id]=next;try{await api('/api/widget-groups/collapse','PUT',{id:g.id,collapsed:next})}catch(err){toast(err.message,true)}}};
section.ondragover=ev=>{if(state.role==='admin')ev.preventDefault()};section.ondrop=async ev=>{if(state.role!=='admin')return;ev.preventDefault();const id=ev.dataTransfer.getData('text/plain');if(id)await moveWidgetTo(id,gid,null)};
sections.set(key,{section,holder,stats});grid.append(section);return sections.get(key)};
const groupOrder=[...visibleGroups.map(g=>g.id),null];for(const gid of groupOrder){const items=selected.filter(x=>(x.group_id||null)===gid).sort((a,b)=>a.widget_position-b.widget_position||a.alias.localeCompare(b.alias));if(!items.length)continue;const sec=ensureSection(gid);sec.stats.textContent=items.filter(x=>x.status==='up'&&!x.stale).length+' UP · '+items.filter(x=>x.status==='down').length+' DOWN · '+items.length+' Widgets';}
for(const e of selected.slice().sort((a,b)=>(groupOrder.indexOf(a.group_id||null)-groupOrder.indexOf(b.group_id||null))||(a.widget_position-b.widget_position))){const card=el('article','card'+(e.kind==='wan_dns'?' wan-dns':'')+(e.maintenance_active?' maintenance':''));const main=el('div','card-main');const head=el('div','card-head');const icons={http:'◎',tcp:'⇄',udp:'⇄',ping:'⌁',dns:'◇',tls:'♧',smtp:'✉',ssh:'⌘',snmp:'▣',ddns_bundle:'◇',wan_dns:'◇'};head.append(el('div','service-icon',icons[e.kind]||'◎'));const tt=el('div','service-title');tt.append(el('h3','',e.alias),el('p','',e.target+(['tcp','udp','ssh','smtp','snmp'].includes(e.kind)?':'+e.port:'')));head.append(tt);const grip=el('span','icon-button grip','⠿');grip.draggable=state.role==='admin';grip.hidden=state.role!=='admin';grip.title='Widget verschieben';grip.ondragstart=ev=>{ev.dataTransfer.setData('text/plain',e.id)};head.append(grip);card.ondragover=ev=>{if(state.role==='admin')ev.preventDefault()};card.ondrop=async ev=>{if(state.role!=='admin')return;ev.preventDefault();ev.stopPropagation();const source=ev.dataTransfer.getData('text/plain');if(source&&source!==e.id)await moveWidgetTo(source,e.group_id,e.id)};main.append(head);
const row=el('div','card-status'),badge=el('span','badge '+(e.maintenance_active?'maintenance':e.stale?'warning':e.status||'pending'),e.stale?'VERALTET':statusText(e));badge.title=(e.dependency_blocked?'Alarm durch Abhängigkeit unterdrückt · ':'')+(e.detail||'');const lat=el('span','latency',e.latency==null?'—':String(Math.round(e.latency)));lat.append(el('small','',' ms'));row.append(badge,lat);main.append(row);const label=el('div','history-label');label.append(el('span','',`LETZTE ${e.history.length} PRÜFUNGEN`),el('span','',e.kind==='wan_dns'?wanDnsSummary(e):(e.detail||'Noch keine Messung')));if(e.kind==='wan_dns')label.title=e.detail||'Noch keine Messung';if(['ddns_bundle','wan_dns'].includes(e.kind)){label.title='DNS-Details öffnen';label.style.cursor='pointer';label.tabIndex=0;label.setAttribute('role','button');label.setAttribute('aria-label','DNS-Diagnose für '+e.alias);label.onclick=()=>openDnsDetails(e);label.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();openDnsDetails(e)}}}main.append(label);const hist=el('div','history');for(let i=e.history.length;i<60;i++)hist.append(el('span','blank'));for(const p of e.history){const bar=el('span',p.status);bar.title=date(p.checked)+' · '+p.status+' · '+Math.round(p.latency)+' ms';hist.append(bar)}main.append(hist);if(e.tags.length||e.kind==='wan_dns'){const tags=el('div','widget-tags'+(e.kind==='wan_dns'?' wan-dns-tags':''));for(const t of e.tags){const chip=el('span','widget-tag',t.name);chip.style.setProperty('--tag-color',t.color);tags.append(chip)}if(e.kind==='wan_dns'){const ip=wanDnsIpv4(e);const chip=el('span','widget-tag wan-ip-tag',ip||'IP unbekannt');chip.title=ip?'Öffentliche WAN-IPv4: '+ip:'WAN-IPv4 im letzten Prüfergebnis nicht verfügbar';chip.setAttribute('aria-label',chip.title);tags.append(chip)}main.append(tags)}card.append(main);const bottom=el('div','card-bottom');const info=el('small','',`Alle ${interval(e.interval)} · Timeout ${e.timeout} s`);info.append(el('br'),document.createTextNode('Geprüft: '+date(e.checked)));const acts=el('div','card-actions');acts.append(el('span','type-label',e.kind==='wan_dns'?'WAN DNS':e.kind.toUpperCase()));acts.append(action('Messverlauf öffnen','▤',()=>openTrends(e)));if(state.role!=='viewer')acts.append(action('Gruppe und Tags','▦',()=>openWidgetAssign(e)));const index=all.findIndex(x=>x.id===e.id);if(state.role==='admin'){const prev=action('Nach links','←',()=>move(e.id,-1)),next=action('Nach rechts','→',()=>move(e.id,1));prev.disabled=index===0;next.disabled=index===all.length-1;acts.append(prev,next)}if(state.role!=='viewer')acts.append(action('Wartung umschalten',e.maintenance?'▶':'Ⅱ',()=>toggleMaintenance(e)),action('Bearbeiten','✎',()=>openEditor(e)),action('Löschen','×',()=>remove(e)));bottom.append(info,acts);card.append(bottom);ensureSection(e.group_id||null).holder.append(card)}scroll.scrollTop=oldScroll}
async function saveOrder(ids){try{await api('/api/order','PUT',{ids});await refresh()}catch(e){toast(e.message,true)}}
function move(id,shift){const ids=state.endpoints.map(e=>e.id),i=ids.indexOf(id),j=i+shift;if(j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];saveOrder(ids)}
function typeFields(){const f=$('#endpoint-form'),k=f.elements.kind.value,mode=f.elements.body_mode.value;$('#expected-field').hidden=k!=='http';$('#port-field').hidden=!['tcp','udp','ssh','smtp','snmp'].includes(k);$('#udp-fields').hidden=k!=='udp';$('#udp-dns-field').hidden=k!=='udp'||f.elements.udp_profile.value!=='dns';$('#udp-custom-fields').hidden=k!=='udp'||f.elements.udp_profile.value!=='custom';$('#ddns-bundle-fields').hidden=k!=='ddns_bundle';$('#wan-dns-fields').hidden=k!=='wan_dns';$('#dns-field').hidden=k!=='dns';$('#dns-expected-field').hidden=k!=='dns';$('#body-mode-field').hidden=k!=='http';$('#body-value-field').hidden=k!=='http'||!['contains','not_contains'].includes(mode);$('#json-path-field').hidden=k!=='http'||mode!=='json_key';$('#json-expected-field').hidden=k!=='http'||mode!=='json_key';$('#starttls-field').hidden=k!=='smtp';$('#snmp-fields').hidden=k!=='snmp';const sm=f.elements.snmp_mode.value;$('#snmp-interface-field').hidden=k!=='snmp'||sm!=='interface';$('#snmp-oid-field').hidden=k==='snmp'&&sm==='interface';f.elements.target.placeholder=k==='http'?'https://example.org/health':k==='tls'?'example.org':k==='ddns_bundle'?'dein-ddns-host.example.net':k==='wan_dns'?'*.deinedomain.de':'192.168.1.10 oder server.lan'}
async function openEditor(e=null){editing=e?.id||null;const f=$('#endpoint-form');f.reset();if(e)for(const field of f.elements){if(field.name&&field.name in e){if(field.type==='checkbox')field.checked=Boolean(e[field.name]);else field.value=e[field.name]??''}}$('#editor-title').textContent=e?'Endpunkt bearbeiten':'Endpunkt hinzufügen';$('#form-error').textContent='';if(e&&e.kind==='dns')f.elements.dns_expected.value=(e.expected_values||[]).join('\n');if(e&&e.kind==='ddns_bundle'){f.elements.ddns_subdomains.value=(e.subdomains||[]).join('\n');f.elements.ddns_resolver.value=e.resolver||'system';f.elements.wan_compare.checked=!!e.wan_compare;}if(e&&e.kind==='wan_dns'){f.elements.wan_dns_subdomains.value=(e.subdomains||[]).join('\n');f.elements.wan_dns_resolver.value=e.resolver||'system';}typeFields();const scope=$('#manager-scope-field');scope.hidden=state.role!=='manager'||!!e;if(!scope.hidden){const select=$('#manager-scope-group');select.replaceChildren(new Option('Gruppe auswählen',''));for(const g of state.groups||[]){if(state.role==='manager'&&!state.grants?.groups.includes(g.id))continue;select.add(new Option(g.name,g.id))}const tagselect=$('#manager-scope-tags');tagselect.replaceChildren(...(state.tags||[]).filter(t=>state.grants?.tags.includes(t.id)).map(t=>new Option(t.name,t.id)));}if(f.elements.kind.value==='snmp'||!e){await loadProfiles(e?.profile_id)}$('#editor').showModal()}
async function remove(e){if(!confirm('Endpunkt „'+e.alias+'“ dauerhaft löschen?'))return;try{await api('/api/endpoints/'+e.id,'DELETE',{});toast('Gelöscht');await refresh()}catch(err){toast(err.message,true)}}
async function toggleMaintenance(e){try{await api('/api/endpoints/'+e.id+'/maintenance','POST',{enabled:!e.maintenance});toast(e.maintenance?'Wartung beendet':'Wartung aktiviert');await refresh()}catch(err){toast(err.message,true)}}
let mfaChallenge='';let mfaRecovery=false;let mfaEnabled=false;let trendMonitor='';
async function loginFinish(){const s=await api('/api/state');state=s;syncOrganizationFilters();applyRoleUI();$('#login').hidden=true;$('#dashboard').hidden=false;$('#user-name').textContent=s.user;$('.avatar').textContent=s.user[0].toUpperCase();$('#app-version').textContent='upWATCH! '+s.version;render();$('#last-sync').textContent='Synchronisiert um '+date(s.now)}
function resetFactor(){mfaChallenge='';mfaRecovery=false;$('#login-factor').hidden=true;$('#login-credentials').hidden=false;$('#login-code').disabled=true;$('#login-code').value='';$('#login-code').pattern='[0-9]{6}';$('#login-code-label').firstChild.textContent='Authenticator-Code';}
$('#login-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget,b=f.querySelector('[type=submit]');b.disabled=true;$('#login-error').textContent='';try{if(!mfaChallenge){const ans=await api('/api/login','POST',{username:f.elements.username.value,password:f.elements.password.value,remember:$('#login-remember').checked});if(ans.mfa_required){mfaChallenge=ans.challenge;$('#login-credentials').hidden=true;$('#login-factor').hidden=false;$('#login-code').disabled=false;$('#login-code').focus();return}}else{await api('/api/login/mfa','POST',{challenge:mfaChallenge,code:$('#login-code').value.trim(),remember:$('#login-remember').checked})}resetFactor();await loginFinish()}catch(e){$('#login-error').textContent=e.message}finally{b.disabled=false}};
$('#login-back').onclick=()=>{resetFactor();$('#login-error').textContent=''};
$('#login-recovery-mode').onclick=()=>{mfaRecovery=!mfaRecovery;$('#login-code').pattern=mfaRecovery?'.*':'[0-9]{6}';$('#login-code').value='';$('#login-code-label').firstChild.textContent=mfaRecovery?'Wiederherstellungscode':'Authenticator-Code';$('#login-recovery-mode').textContent=mfaRecovery?'Authenticator-Code verwenden':'Wiederherstellungscode verwenden'};
$('#show-password').onchange=()=>$('#login-password').type=$('#show-password').checked?'text':'password';
$('#logout').onclick=async()=>{try{await api('/api/logout','POST',{});}catch{}showLogin();resetFactor()};
$('#endpoint-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget,b=f.querySelector('[type=submit]');const d=Object.fromEntries(new FormData(f));if(state.role==='manager'&&!editing){d.group_id=$('#manager-scope-group').value;d.tag_ids=[...$('#manager-scope-tags').selectedOptions].map(o=>o.value);}if(d.kind==='wan_dns'){d.ddns_subdomains=d.wan_dns_subdomains;d.ddns_resolver=d.wan_dns_resolver;}for(const key of ['interval','timeout','port','expected','failure_threshold','recovery_threshold','warning_latency_ms','critical_latency_ms'])if(d[key]!==undefined)d[key]=d[key]===''?null:Number(d[key]);for(const key of ['notify_down','notify_up','maintenance','starttls','wan_compare'])d[key]=f.elements[key]?.checked||false;b.disabled=true;try{await api('/api/endpoints'+(editing?'/'+editing:''),editing?'PUT':'POST',d);$('#editor').close();toast('Gespeichert');await refresh()}catch(err){$('#form-error').textContent=err.message}finally{b.disabled=false}};
$('#add').onclick=()=>openEditor();$('#add-empty').onclick=()=>openEditor();$('#close-editor').onclick=()=>$('#editor').close();$('#cancel-editor').onclick=()=>$('#editor').close();$('#endpoint-form').elements.kind.onchange=()=>{typeFields();if($('#endpoint-form').elements.kind.value==='snmp'){$('#endpoint-form').elements.port.value=161;loadProfiles()}if($('#endpoint-form').elements.kind.value==='udp'){$('#endpoint-form').elements.port.value=53}};$('#endpoint-form').elements.body_mode.onchange=typeFields;$('#snmp-mode').onchange=typeFields;$('#endpoint-form').elements.udp_profile.onchange=()=>{const f=$('#endpoint-form');if(f.elements.udp_profile.value==='dns')f.elements.port.value=53;if(f.elements.udp_profile.value==='ntp')f.elements.port.value=123;typeFields()};$('#search').oninput=render;$('#filter').onchange=render;$('#group-filter').onchange=render;$('#tag-filter').onchange=render;$('#overview').onclick=()=>{$('#search').value='';$('#filter').value='all';$('#group-filter').value='all';$('#tag-filter').value='all';render()};
$('#check-all').onclick=async()=>{try{await api('/api/check','POST',{});toast('Prüfungen eingeplant')}catch(e){toast(e.message,true)}};
const focusButton=$('#focus-mode'),focusHome=focusButton.parentElement,focusAnchor=focusButton.nextSibling;
$('#focus-mode').onclick=()=>{const focus=!document.body.classList.contains('focus-mode');if(focus)document.body.append(focusButton);else focusHome.insertBefore(focusButton,focusAnchor);document.body.classList.toggle('focus-mode',focus);$('#focus-mode').setAttribute('aria-pressed',String(focus));$('#focus-mode').setAttribute('aria-label',focus?'Übersicht zeigen':'Fokusansicht');$('#focus-mode').title=focus?'Übersicht zeigen':'Fokusansicht';$('#focus-label').textContent=focus?'Übersicht zeigen':'Fokusansicht';localStorage.setItem('upwatch-focus',focus?'1':'0')};if(localStorage.getItem('upwatch-focus')==='1')$('#focus-mode').click();
async function loadMfaStatus(){try{const x=await api('/api/mfa/status');mfaEnabled=x.enabled;$('#mfa-status').hidden=false;$('#mfa-status').textContent=mfaEnabled?'Zwei-Faktor-Anmeldung aktiv':'Zwei-Faktor-Anmeldung nicht aktiviert';$('#mfa-setup-form').hidden=mfaEnabled;$('#mfa-manage-form').hidden=!mfaEnabled;for(const el of document.querySelectorAll('.sensitive-mfa'))el.hidden=!mfaEnabled}catch(e){$('#mfa-status').textContent=e.message;$('#mfa-status').hidden=false}}
function showRecovery(codes){$('#recovery-codes').value=codes.join('\n');$('#recovery-dialog').showModal()}
$('#mfa-setup-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget;try{if($('#mfa-enrollment').hidden){const s=await api('/api/mfa/setup','POST',{current_password:f.elements.current_password.value});$('#mfa-secret').value=s.secret;$('#mfa-open-app').href=s.uri;$('#mfa-qr').src=s.qr_svg;$('#mfa-enrollment').hidden=false;f.elements.mfa_code.disabled=false;$('#mfa-setup-submit').textContent='2FA bestätigen';$('#mfa-cancel').hidden=false}else{const x=await api('/api/mfa/confirm','POST',{mfa_code:f.elements.mfa_code.value});$('#mfa-qr').removeAttribute('src');$('#mfa-secret').value='';$('#mfa-open-app').removeAttribute('href');showRecovery(x.recovery_codes)}$('#mfa-setup-error').textContent=''}catch(e){$('#mfa-setup-error').textContent=e.message}};
$('#mfa-cancel').onclick=()=>{const f=$('#mfa-setup-form');f.reset();$('#mfa-enrollment').hidden=true;$('#mfa-qr').removeAttribute('src');$('#mfa-secret').value='';$('#mfa-open-app').removeAttribute('href');f.elements.mfa_code.disabled=true;$('#mfa-setup-submit').textContent='2FA einrichten';$('#mfa-cancel').hidden=true};
$('#mfa-copy-secret').onclick=()=>navigator.clipboard.writeText($('#mfa-secret').value);
$('#mfa-regenerate').onclick=async()=>{const f=$('#mfa-manage-form');try{const x=await api('/api/mfa/regenerate','POST',Object.fromEntries(new FormData(f)));showRecovery(x.recovery_codes);$('#mfa-manage-error').textContent=''}catch(e){$('#mfa-manage-error').textContent=e.message}};
$('#mfa-disable').onclick=async()=>{if(!confirm('2FA deaktivieren?'))return;const f=$('#mfa-manage-form');try{await api('/api/mfa/disable','POST',Object.fromEntries(new FormData(f)));$('#settings-dialog').close();showLogin();resetFactor()}catch(e){$('#mfa-manage-error').textContent=e.message}};
$('#recovery-copy').onclick=()=>navigator.clipboard.writeText($('#recovery-codes').value);
$('#recovery-download').onclick=()=>{const blob=new Blob([$('#recovery-codes').value+'\n'],{type:'text/plain'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='upwatch-recovery-codes.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
$('#recovery-done').onclick=()=>{$('#recovery-dialog').close();$('#settings-dialog').close();showLogin();resetFactor()};
$('#settings-open').onclick=async()=>{syncAccountUsername();$('#settings-dialog').showModal();$('#system-info').textContent='upWATCH! '+state.version+' · Separate web and worker services · PostgreSQL 17';setSettingsTab('account');$('#mfa-setup-form').hidden=true;$('#mfa-manage-form').hidden=true;await loadMfaStatus();};
function syncAccountUsername(){const current=String(state.user||'');$('#account-current-username').value=current;$('#account-form').elements.username.value=current}
function setSettingsTab(tab){$('#users-panel').hidden=tab!=='users';$('#users-tab').setAttribute('aria-selected',String(tab==='users'));$('#appearance-panel').hidden=tab!=='appearance';$('#appearance-tab').setAttribute('aria-selected',String(tab==='appearance'));$('#smtp-panel').hidden=tab!=='smtp';$('#alerts-panel').hidden=tab!=='alerts';$('#audit-panel').hidden=tab!=='audit';$('#audit-tab').setAttribute('aria-selected',String(tab==='audit'));$('#reports-panel').hidden=tab!=='reports';$('#alerts-tab').setAttribute('aria-selected',String(tab==='alerts'));$('#reports-tab').setAttribute('aria-selected',String(tab==='reports'));$('#account-panel').hidden=tab!=='account';$('#snmp-profiles-panel').hidden=tab!=='snmp'||state.role!=='admin';$('#snmp-tab').setAttribute('aria-selected',String(tab==='snmp'));$('#account-tab').setAttribute('aria-selected',String(tab==='account'));$('#smtp-tab').setAttribute('aria-selected',String(tab==='smtp'));}
$('#account-tab').onclick=()=>{syncAccountUsername();setSettingsTab('account')};$('#snmp-tab').onclick=async()=>{if(state.role!=='admin')return;setSettingsTab('snmp');await loadProfiles()};$('#users-tab').onclick=async()=>{setSettingsTab('users');await loadUsers()};
$('#appearance-tab').onclick=async()=>{setSettingsTab('appearance');await loadAppearance()};
$('#reports-tab').onclick=async()=>{setSettingsTab('reports');await loadReports()};
$('#reports-refresh').onclick=loadReports;
/* upwatch rc4 report csv v1 */
function csvReportCell(value){
  let str=value===null||value===undefined?'':String(value);
  if(/^\s*[=+\-@]/.test(str))str="'"+str; // no spreadsheet formula execution
  return '"'+str.replace(/"/g,'""')+'"';
}
async function downloadAvailabilityCsv(){
  const b=$('#reports-download-csv'); b.disabled=true;
  try{
    const days=$('#reports-days').value;
    const data=await api('/api/reports/availability?days='+encodeURIComponent(days));
    const cols=['Monitor-ID','Monitor','Typ','Prüfungen','Erfolge','Erfolgsquote (%)','Ø Latenz (ms)','Incidents','Bestätigte Ausfallzeit (Sekunden)'];
    const rows=[cols,...data.monitors.map(m=>[m.monitor_id,m.alias,m.kind,m.checks,m.success,m.probe_success_pct,m.avg_latency_ms,m.incident_count,m.incident_downtime_sec])];
    const csv='\uFEFF'+rows.map(row=>row.map(csvReportCell).join(';')).join('\r\n')+'\r\n';
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a'); a.href=url;a.download='upwatch-verfuegbarkeit-'+days+'-tage.csv';document.body.append(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){toast(e.message,true)}finally{b.disabled=false}
}
$('#reports-download-csv').onclick=downloadAvailabilityCsv;

async function loadReports(){const list=$('#reports-list');list.replaceChildren();try{const r=await api('/api/reports/availability?days='+$('#reports-days').value);for(const m of r.monitors){const line=el('div','delivery-box');line.append(el('strong','',m.alias),el('div','muted',m.checks+' Prüfungen · '+(m.probe_success_pct===null?'Keine Messwerte':m.probe_success_pct+' % erfolgreich')+' · '+Math.round(m.incident_downtime_sec/60)+' Min. bestätigte Ausfälle'));list.append(line)}if(!r.monitors.length)list.append(el('p','muted','Keine freigegebenen Monitore.'))}catch(e){list.append(el('p','form-error',e.message))}}
$('#audit-tab').onclick=async()=>{if(state.role!=='admin')return;setSettingsTab('audit');const list=$('#audit-list');list.replaceChildren();try{const entries=await api('/api/audit');for(const e of entries.audit){const line=el('div','delivery-top');line.append(el('span','',new Date(e.created*1000).toLocaleString((window.upwatchI18n?.getLanguage()==='en'?'en-GB':'de-DE'))+' · '+e.actor+' · '+e.action),el('span','muted',e.object_id||''));list.append(line)}if(!entries.audit.length)list.append(el('p','muted','Keine protokollierten Aktionen.'))}catch(e){list.append(el('p','form-error',e.message))}};
$('#alerts-tab').onclick=async()=>{if(state.role!=='admin')return;setSettingsTab('alerts');await loadAlerts()};
async function loadAlerts(){const list=$('#alerts-list');list.replaceChildren();try{const [users,data]=await Promise.all([api('/api/users'),api('/api/alert-subscriptions')]);const saved=new Map(data.subscriptions.map(x=>[x.username,x]));for(const u of users.users){const old=saved.get(u.username)||{};const box=el('div','delivery-box');box.append(el('strong','',u.username));const inp=el('input');inp.type='email';inp.placeholder='Alarm-E-Mail-Adresse';inp.maxLength=254;inp.value=old.email||'';const dl=el('label','checkbox',' DOWN');const down=el('input');down.type='checkbox';down.checked=!!old.notify_down;dl.prepend(down);const ul=el('label','checkbox',' UP');const up=el('input');up.type='checkbox';up.checked=!!old.notify_up;ul.prepend(up);const btn=action('Alarmempfänger speichern','Speichern',async()=>{try{await api('/api/alert-subscriptions','PUT',{username:u.username,email:inp.value,notify_down:down.checked,notify_up:up.checked});toast('Alarmempfänger gespeichert')}catch(e){$('#alerts-error').textContent=e.message}});box.append(inp,dl,ul,btn);list.append(box)}}catch(e){$('#alerts-error').textContent=e.message}}
$('#smtp-tab').onclick=async()=>{setSettingsTab('smtp');try{const s=await api('/api/smtp');const f=$('#smtp-form');for(const x of f.elements){if(!x.name||x.name==='password'||x.name==='current_password')continue;if(x.name in s){if(x.type==='checkbox')x.checked=!!s[x.name];else x.value=s[x.name]??''}}await loadDelivery()}catch(e){$('#smtp-error').textContent=e.message}};
async function loadProfiles(selected=''){try{const p=await api('/api/snmp-profiles');const sel=$('#snmp-profile-select'),list=$('#snmp-profile-list');sel.replaceChildren(new Option('Profil auswählen …',''));list.replaceChildren();for(const x of p.profiles){sel.add(new Option(x.label+' ('+x.username+')',x.id));const line=el('div','delivery-top');line.append(el('span','',x.label+' · '+x.username+' · '+x.auth_protocol.toUpperCase()));line.append(action('Profil löschen','×',async()=>{if(!confirm('SNMP-Profil löschen?'))return;try{await api('/api/snmp-profiles/'+x.id,'DELETE',{});await loadProfiles();toast('Profil gelöscht')}catch(e){toast(e.message,true)}}));list.append(line)}sel.value=selected}catch(e){toast(e.message,true)}}
$('#snmp-profile-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget;try{await api('/api/snmp-profiles','POST',Object.fromEntries(new FormData(f)));f.reset();await loadProfiles();toast('SNMP-Profil erstellt');$('#snmp-profile-error').textContent=''}catch(e){$('#snmp-profile-error').textContent=e.message}};
$('#smtp-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget;try{const d=Object.fromEntries(new FormData(f));d.enabled=f.elements.enabled.checked;d.clear_password=f.elements.clear_password.checked;await api('/api/smtp','PUT',d);$('#smtp-error').textContent='';f.elements.password.value='';toast('SMTP-Einstellungen gespeichert');await loadDelivery()}catch(e){$('#smtp-error').textContent=e.message}};
let deliveryData={counts:{},latest:[]};
const deliveryLabels={sent:'Gesendet',pending:'Ausstehend',sending:'Wird versendet',failed:'Fehlgeschlagen'};
function renderDelivery(){
  const counts=$('#delivery-counts');counts.replaceChildren();
  for(const status of ['sent','pending','sending','failed']){
    const item=el('div','delivery-stat delivery-stat-'+status);
    item.append(el('strong','',String(deliveryData.counts?.[status]||0)),el('span','',deliveryLabels[status]));counts.append(item);
  }
  const list=$('#delivery-history');list.replaceChildren();
  const chosen=$('#delivery-filter').value;
  const entries=(deliveryData.latest||[]).filter(x=>chosen==='all'||x.status===chosen);
  if(!entries.length){list.append(el('p','muted','Keine Nachrichten in dieser Auswahl.'));return;}
  for(const msg of entries){
    const line=el('div','delivery-entry');line.setAttribute('role','listitem');
    const top=el('div','delivery-entry-top');top.append(el('span','delivery-state delivery-state-'+msg.status,deliveryLabels[msg.status]||msg.status),el('span','delivery-entry-date',date(msg.sent_at||msg.created)));
    const title=el('div','delivery-entry-subject',msg.subject||'Ohne Betreff');
    const recipient=el('div','delivery-entry-meta','An: '+(msg.recipient||'–')+' · Versuche: '+msg.attempts);
    line.append(top,title,recipient);
    if(msg.last_error && msg.status==='failed'){const err=el('div','delivery-entry-error',msg.last_error);err.title=msg.last_error;line.append(err);}
    list.append(line);
  }
}
async function loadDelivery(){
  try{deliveryData=await api('/api/smtp/delivery');$('#delivery-status').textContent='Versandübersicht · letzte 50 Nachrichten';renderDelivery()}
  catch(e){$('#delivery-status').textContent='Versandstatus konnte nicht geladen werden: '+e.message;$('#delivery-history').replaceChildren();$('#delivery-counts').replaceChildren()}
}
$('#delivery-filter').onchange=renderDelivery;
$('#smtp-test').onclick=async()=>{try{await api('/api/smtp/test','POST',{});toast('Testmail eingereiht');await loadDelivery()}catch(e){toast(e.message,true)}};
$('#delivery-refresh').onclick=loadDelivery;
$('#delivery-retry').onclick=async()=>{try{const r=await api('/api/smtp/retry','POST',{});toast(r.queued+' Nachrichten erneut eingeplant');await loadDelivery()}catch(e){toast(e.message,true)}};
$('#settings-close').onclick=()=>$('#settings-dialog').close();$('#account-form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget,d=Object.fromEntries(new FormData(f));try{await api('/api/account','PUT',d);$('#settings-dialog').close();showLogin();toast('Konto aktualisiert. Bitte erneut anmelden.')}catch(err){$('#account-error').textContent=err.message}};
$('#login-factor').hidden=true;
// Dev 6.3: preserve existing authenticated session across reloads.
// No credentials or session tokens are stored in JavaScript storage.
(async function restoreSession(){
  try {
    const r=await fetch('/api/state',{credentials:'same-origin',cache:'no-store'});
    if(r.status===401){showLogin();return;}
    if(!r.ok)throw Error('Sitzung konnte nicht geprüft werden ('+r.status+').');
    await loginFinish();
  } catch(err) {
    $('#login-error').textContent='Verbindung zur Sitzung fehlgeschlagen: '+err.message;
  }
})();
setInterval(refresh,3000);

$('#alarm-preview-button').onclick=async()=>{
  const id=$('#alarm-preview-monitor').value;
  if(!id){$('#alarm-preview-result').textContent='Bitte einen Monitor auswählen.';return}
  try{const p=await api('/api/notifications/preview','POST',{monitor_id:id});
    $('#alarm-preview-result').textContent=`Nur Simulation · SMTP: ${p.smtp_enabled?'aktiv':'aus'} · Empfänger: ${p.recipient_configured?'vorhanden':'fehlt'} · DOWN: ${p.notify_down?'aktiv':'aus'} · Recovery: ${p.notify_up?'aktiv':'aus'} · Unterdrückt: ${p.suppressed?'ja':'nein'} · Keine E-Mail versendet.`;
  }catch(e){$('#alarm-preview-result').textContent=e.message}
};
$('#logs-open').onclick=async()=>{
  const selector=$('#alarm-preview-monitor');selector.replaceChildren(new Option('Monitor wählen',''));
  for(const monitor of state.endpoints)selector.add(new Option(monitor.alias,monitor.id));
  $('#alarm-preview-result').textContent='';const dlg=$('#logs-dialog'),area=$('#logs-items');area.replaceChildren();dlg.showModal();try{const [ev,inc,diag]=await Promise.all([api('/api/events'),api('/api/incidents'),state.role==='admin'?api('/api/diagnostics'):Promise.resolve(null)]);if(diag){const info=el('div','delivery-box');info.append(el('strong','','Systemdiagnose'),el('p','',`Worker: ${diag.worker_online?'aktiv':'nicht erreichbar'}${diag.worker_stalled?' (Prüfungen möglicherweise blockiert)':''} · Letzter Abschluss: ${date(diag.worker_completed_at)} · Überfällige Monitore: ${diag.overdue_monitors} · Letzte Prüfung: ${date(diag.last_probe_at)} · Offene Incidents: ${diag.open_incidents} · Zurückgestellte Alarme: ${diag.deferred_incidents}`),el('p','',`SMTP-Outbox: ${JSON.stringify(diag.mail_outbox)} · PostgreSQL: ${diag.db_bytes==null?'unbekannt':Math.round(diag.db_bytes/1024)+' KiB'}`));area.append(info)}for(const e of ev.events){const row=el('div','delivery-box');row.append(el('strong','',date(e.created)+' · '+e.event_type),el('p','',e.detail));area.append(row)}if(!ev.events.length)area.append(el('p','muted','Noch keine Ereignisse protokolliert.'))}catch(err){area.append(el('p','form-error',err.message))}};
$('#logs-close').onclick=()=>$('#logs-dialog').close();
for(const b of document.querySelectorAll('[data-export]'))b.onclick=async()=>{try{const r=await fetch('/api/exports/'+b.dataset.export+'.csv',{credentials:'same-origin'});if(!r.ok)throw Error('Export fehlgeschlagen');const blob=await r.blob();const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='upwatch-'+b.dataset.export+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(e.message,true)}};


$('#maintenance-open').onclick=async()=>{const d=$('#maintenance-dialog'),sel=$('#maintenance-monitor');sel.replaceChildren(...state.endpoints.map(x=>new Option(x.alias,x.id)));d.showModal();await loadWindows()};
$('#maintenance-close').onclick=()=>$('#maintenance-dialog').close();
async function loadWindows(){const box=$('#maintenance-list');box.replaceChildren();try{const x=await api('/api/maintenance-windows');for(const w of x.windows){const row=el('div','delivery-box');row.append(el('span','',`${state.endpoints.find(m=>m.id===w.monitor_id)?.alias||w.monitor_id} · ${w.recurrence} · ${w.timezone} · ${date(w.starts)} bis ${date(w.ends)}`));row.append(action('Wartungsfenster löschen','×',async()=>{await api('/api/maintenance-windows/'+w.id,'DELETE',{});await loadWindows()}));box.append(row)}if(!x.windows.length)box.append(el('p','muted','Keine geplanten Wartungsfenster'))}catch(e){box.append(el('p','form-error',e.message))}}
$('#maintenance-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const d=Object.fromEntries(new FormData(f));d.starts=new Date(d.starts).getTime()/1000;d.ends=new Date(d.ends).getTime()/1000;d.weekdays=Array.from(f.elements.weekdays.selectedOptions).map(x=>Number(x.value));await api('/api/maintenance-windows','POST',d);$('#maintenance-error').textContent='';await loadWindows();toast('Wartungsfenster gespeichert')}catch(err){$('#maintenance-error').textContent=err.message}};
// Axis-based SVG trends: elapsed time is linear; missing buckets are left as gaps.
function makeChart(points,key,color,label,period){
  const wrap=el('div','delivery-box'),title=el('strong','',label);wrap.append(title);
  const rows=points.filter(p=>Number.isFinite(Number(p.bucket))&&p[key]!=null&&Number.isFinite(Number(p[key]))).sort((a,b)=>a.bucket-b.bucket);
  if(!rows.length){wrap.append(el('p','muted','Noch keine Messdaten'));return wrap}
  const pct=key==='availability_pct',unit=pct?'%':'ms';
  const W=680,H=238,left=66,right=18,top=18,bottom=43,innerW=W-left-right,innerH=H-top-bottom;
  const maxValue=Math.max(...rows.map(p=>Number(p[key])));
  // Availability always uses 0..100%; latency starts at zero with rounded ticks.
  const tickTop=pct?100:Math.max(1,Math.ceil(maxValue/(Math.pow(10,Math.floor(Math.log10(Math.max(maxValue,1)))-1)||1)*1.1)*(Math.pow(10,Math.floor(Math.log10(Math.max(maxValue,1)))-1)||1));
  const topValue=Number.isFinite(tickTop)&&tickTop>0?tickTop:1;
  const span=period==='day'?86400:3600;
  const first=Number(rows[0].bucket),last=Number(rows[rows.length-1].bucket);
  // One bucket: centered point, labeled at its actual timestamp.
  const x=t=>left+(last===first?0.5:(Number(t)-first)/(last-first))*innerW;
  const y=v=>top+(1-Number(v)/topValue)*innerH;
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('role','img');
  svg.setAttribute('aria-label',label+' mit Zeitachse, Skala in '+unit+' und '+rows.length+' Datenpunkten');
  svg.style.width='100%';svg.style.height='auto';svg.style.display='block';
  const NS='http://www.w3.org/2000/svg';
  function node(tag,attrs={},content){const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(content!==undefined)n.textContent=String(content);svg.append(n);return n}
  const fmt=v=>pct?`${Math.round(v)} %`:(v>=100?Math.round(v).toLocaleString((window.upwatchI18n?.getLanguage()==='en'?'en-GB':'de-DE')):Number(v.toFixed(v<1?2:1)).toLocaleString((window.upwatchI18n?.getLanguage()==='en'?'en-GB':'de-DE')))+' ms';
  for(let i=0;i<=4;i++){
    const val=topValue*i/4,yy=y(val);
    node('line',{x1:left,y1:yy,x2:W-right,y2:yy,stroke:'currentColor','stroke-opacity':'.17','stroke-width':1});
    node('text',{x:left-9,y:yy+4,'text-anchor':'end',fill:'currentColor','font-size':12},fmt(val));
  }
  node('line',{x1:left,y1:top,x2:left,y2:H-bottom,stroke:'currentColor','stroke-opacity':'.4'});
  node('line',{x1:left,y1:H-bottom,x2:W-right,y2:H-bottom,stroke:'currentColor','stroke-opacity':'.4'});
  const timeLabel=t=>new Date(t*1000).toLocaleString((window.upwatchI18n?.getLanguage()==='en'?'en-GB':'de-DE'),period==='day'?{day:'2-digit',month:'2-digit',year:'2-digit'}:{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const labels=last===first?[first]:Array.from({length:5},(_,i)=>first+(last-first)*i/4);
  labels.forEach(t=>{const xx=x(t);node('line',{x1:xx,y1:H-bottom,x2:xx,y2:H-bottom+5,stroke:'currentColor','stroke-opacity':'.5'});node('text',{x:xx,y:H-15,'text-anchor':t===first?'start':t===last?'end':'middle',fill:'currentColor','font-size':11},timeLabel(t))});
  // Draw separate polylines: never imply continuous measurements across empty buckets.
  let segment=[];
  function flush(){if(segment.length){node('polyline',{points:segment.map(p=>`${x(p.bucket)},${y(p[key])}`).join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round','stroke-linecap':'round'});segment=[]}}
  for(let i=0;i<rows.length;i++){
    const point=rows[i];if(i&&point.bucket-rows[i-1].bucket>span*1.5)flush();
    segment.push(point);
  }flush();
  rows.forEach(p=>{const dot=node('circle',{cx:x(p.bucket),cy:y(p[key]),r:rows.length>100?1.6:2.8,fill:color});const tip=document.createElementNS(NS,'title');tip.textContent=timeLabel(p.bucket)+' · '+fmt(p[key])+(p.total!=null?' · '+p.total+' Prüfungen':'');dot.append(tip)});
  wrap.append(svg);
  const avg=rows.reduce((sum,p)=>sum+Number(p[key]),0)/rows.length;
  wrap.append(el('p','muted',`Ø ${fmt(avg)} · Maximum ${fmt(maxValue)} · ${rows.length} Zeitintervalle · ${period==='day'?'tägliche':'stündliche'} Auflösung`));
  return wrap;
}
async function openTrends(m){trendMonitor=m.id;$('#trends-title').textContent='Messverlauf · '+m.alias;$('#trends-dialog').showModal();await refreshTrends()}
async function refreshTrends(){const box=$('#trends-body');box.replaceChildren();try{const x=await api('/api/monitors/'+trendMonitor+'/trends?period='+$('#trends-period').value+'&limit=168');box.append(makeChart(x.points,'latency_avg_ms','#5aa9e6','Ø Latenz (ms)',x.period),makeChart(x.points,'availability_pct','#4db68b','Erfolgsquote (%)',x.period));}catch(e){box.append(el('p','form-error',e.message))}}
$('#trends-period').onchange=refreshTrends;$('#trends-close').onclick=()=>$('#trends-dialog').close();

// Dev 4: dependency editor, incident statistics, SNMPv3 interface discovery.
$('#dependencies-open').onclick=async()=>{const opts=state.endpoints.map(x=>new Option(x.alias,x.id));$('#dependencies-parent').replaceChildren(...opts.map(x=>x.cloneNode(true)));$('#dependencies-child').replaceChildren(...opts.map(x=>x.cloneNode(true)));$('#dependencies-dialog').showModal();await loadDependencies()};
$('#dependencies-close').onclick=()=>$('#dependencies-dialog').close();
async function loadDependencies(){const box=$('#dependencies-list');box.replaceChildren();try{const d=await api('/api/dependencies');const byParent=new Map();for(const x of d.dependencies){if(!byParent.has(x.parent_id))byParent.set(x.parent_id,[]);byParent.get(x.parent_id).push(x)}for(const [parentId,edges] of byParent){const parent=state.endpoints.find(m=>m.id===parentId);const group=el('div','delivery-box');group.append(el('strong','',`Hauptmonitor: ${parent?.alias||parentId} (${edges.length} Abhängigkeiten)`));for(const x of edges){const child=state.endpoints.find(m=>m.id===x.child_id),row=el('div','delivery-top');row.append(el('span','',child?.alias||x.child_id));if(state.role!=='viewer')row.append(action('Abhängigkeit löschen','×',async()=>{try{await api('/api/dependencies','DELETE',x);await loadDependencies();await refresh()}catch(e){toast(e.message,true)}}));group.append(row)}box.append(group)}if(!d.dependencies.length)box.append(el('p','muted','Keine Abhängigkeiten festgelegt.'))}catch(e){box.append(el('p','form-error',e.message))}}
$('#dependencies-form').onsubmit=async e=>{e.preventDefault();const d={parent_id:$('#dependencies-parent').value,child_ids:[...$('#dependencies-child').selectedOptions].map(o=>o.value)};try{await api('/api/dependencies/bulk','POST',d);$('#dependencies-error').textContent='';await loadDependencies();await refresh();toast('Abhängigkeit gespeichert')}catch(err){$('#dependencies-error').textContent=err.message}};
$('#incidents-open').onclick=async()=>{$('#incidents-monitor').replaceChildren(new Option('Alle Monitore',''),...state.endpoints.map(x=>new Option(x.alias,x.id)));$('#incidents-dialog').showModal();await loadIncidentStats()};
$('#incidents-close').onclick=()=>$('#incidents-dialog').close();
$('#incidents-days').onchange=loadIncidentStats;$('#incidents-monitor').onchange=loadIncidentStats;
function fmtDuration(seconds){if(seconds==null)return '—';if(seconds<60)return Math.round(seconds)+' s';if(seconds<3600)return (seconds/60).toFixed(1)+' min';return (seconds/3600).toFixed(1)+' h'}
async function loadIncidentStats(){const box=$('#incidents-body');box.replaceChildren();try{const q=new URLSearchParams({days:$('#incidents-days').value});if($('#incidents-monitor').value)q.set('monitor_id',$('#incidents-monitor').value);const d=await api('/api/incidents/stats?'+q);const stats=el('div','delivery-box');for(const [label,v] of [['Vorfälle',d.total],['Aktiv',d.open],['Geschlossen',d.closed],['Erfasste Ausfallzeit',fmtDuration(d.downtime_sec)],['Ø Dauer geschlossener Vorfälle',fmtDuration(d.mttr_sec)],['Längster Vorfall',fmtDuration(d.longest_sec)]]){const r=el('div','delivery-top');r.append(el('strong','',label),el('span','',String(v)));stats.append(r)}box.append(stats);for(const i of d.incidents){const r=el('div','delivery-box');r.append(el('strong','',`${i.alias||i.monitor_id||'Gelöschter Monitor'} · ${i.open?'AKTIV':'Beendet'}`),el('p','muted',`${date(i.started)} · ${fmtDuration(i.duration_sec)} · ${i.reason||'Keine Ursache erfasst'}`));box.append(r)}if(!d.incidents.length)box.append(el('p','muted','Keine Incidents im Zeitraum'))}catch(e){box.append(el('p','form-error',e.message))}}
$('#snmp-discover').onclick=async()=>{const f=$('#endpoint-form'),out=$('#snmp-discover-results'),status=$('#snmp-discover-status');out.replaceChildren(new Option('Erkannte Interfaces auswählen …',''));status.textContent='Suche läuft …';$('#snmp-discover').disabled=true;try{const d=await api('/api/snmp/discover','POST',{target:f.elements.target.value,profile_id:f.elements.profile_id.value,port:f.elements.port.value,timeout:3});for(const x of d.interfaces)out.add(new Option(`${x.if_index} · ${x.name||x.description||'Unbenannt'}${x.description&&x.name?' · '+x.description:''}`,x.if_index));status.textContent=`${d.interfaces.length} Interfaces erkannt${d.truncated?' (max. 64)':''}`;if(!d.interfaces.length)toast('Keine Interfaces gefunden')}catch(e){status.textContent=e.message;toast(e.message,true)}finally{$('#snmp-discover').disabled=false}};
$('#snmp-discover-results').onchange=e=>{if(e.target.value)$('#endpoint-form').elements.if_index.value=e.target.value};

// Dev 6: grouping is presentational. No monitor probe configuration is changed.
function syncOrganizationFilters(){for(const [sel,arr,defaultText] of [['#group-filter',state.groups||[],'Alle Gruppen'],['#tag-filter',state.tags||[],'Alle Tags']]){const node=$(sel),old=node.value;node.replaceChildren(new Option(defaultText,'all'));if(sel==='#group-filter')node.add(new Option('Ohne Gruppe','none'));for(const x of arr)node.add(new Option(x.name,x.id));node.value=[...node.options].some(o=>o.value===old)?old:'all'}}
async function moveWidgetTo(id,groupId,beforeId){try{await api('/api/widgets/move','PUT',{monitor_id:id,group_id:groupId||null,before_id:beforeId||null});await refresh()}catch(err){toast(err.message,true)}}
function optionsForGroups(select,selected){select.replaceChildren(new Option('Ohne Gruppe',''));for(const g of state.groups||[]){if(state.role==='manager'&&!state.grants?.groups.includes(g.id))continue;select.add(new Option(g.name,g.id))}select.value=selected||''}
function openWidgetAssign(e){assigningWidget=e.id;$('#widget-assign-title').textContent=e.alias+' organisieren';optionsForGroups($('#widget-assign-group'),e.group_id);const wrap=$('#widget-assign-tags');wrap.replaceChildren();for(const t of state.tags||[]){if(state.role==='manager'&&!state.grants?.tags.includes(t.id))continue;const label=el('label','org-tag-option');const cb=document.createElement('input');cb.type='checkbox';cb.value=t.id;cb.checked=e.tags.some(x=>x.id===t.id);const dot=el('span','tag-dot');dot.style.background=t.color;label.append(cb,dot,document.createTextNode(t.name));wrap.append(label)}$('#widget-assign-error').textContent='';$('#widget-assign-dialog').showModal()}
$('#widget-assign-close').onclick=()=>$('#widget-assign-dialog').close();
$('#widget-assign-form').onsubmit=async ev=>{ev.preventDefault();try{await api('/api/widgets/assign','PUT',{monitor_id:assigningWidget,group_id:$('#widget-assign-group').value||null,tag_ids:[...$('#widget-assign-tags').querySelectorAll('input:checked')].map(x=>x.value)});$('#widget-assign-dialog').close();toast('Widget organisiert');await refresh()}catch(err){$('#widget-assign-error').textContent=err.message}};
$('#organize-close').onclick=()=>$('#organize-dialog').close();
$('#organize-open').onclick=()=>{renderOrganization();$('#organize-dialog').showModal()};
function renderOrganization(){const gl=$('#groups-list'),tl=$('#tags-list');gl.replaceChildren();tl.replaceChildren();for(const g of state.groups||[]){const item=el('div','org-item');item.append(el('strong','',g.name),action('Nach oben','↑',()=>reorderGroup(g.id,-1)),action('Nach unten','↓',()=>reorderGroup(g.id,1)),action('Umbenennen','✎',async()=>{const name=prompt('Neuer Gruppenname',g.name);if(name===null)return;try{await api('/api/widget-groups/'+g.id,'PUT',{name,description:g.description});await refresh();renderOrganization()}catch(err){toast(err.message,true)}}),action('Gruppe löschen','×',async()=>{if(!confirm('Gruppe löschen? Widgets bleiben erhalten.'))return;try{await api('/api/widget-groups/'+g.id,'DELETE',{});await refresh();renderOrganization()}catch(err){toast(err.message,true)}}));gl.append(item)}for(const t of state.tags||[]){const item=el('div','org-item');const dot=el('span','tag-dot');dot.style.background=t.color;item.append(dot,el('strong','',t.name),action('Tag bearbeiten','✎',async()=>{const name=prompt('Tag-Name',t.name);if(name===null)return;const color=prompt('Tag-Farbe als #RRGGBB',t.color);if(color===null)return;try{await api('/api/widget-tags/'+t.id,'PUT',{name,color});await refresh();renderOrganization()}catch(err){toast(err.message,true)}}),action('Tag löschen','×',async()=>{if(!confirm('Tag löschen und von allen Widgets entfernen?'))return;try{await api('/api/widget-tags/'+t.id,'DELETE',{});await refresh();renderOrganization()}catch(err){toast(err.message,true)}}));tl.append(item)}}
async function reorderGroup(id,step){const ids=state.groups.map(x=>x.id),index=ids.indexOf(id),next=index+step;if(next<0||next>=ids.length)return;[ids[index],ids[next]]=[ids[next],ids[index]];try{await api('/api/widget-groups/order','PUT',{ids});await refresh();renderOrganization()}catch(err){toast(err.message,true)}}
$('#group-form').onsubmit=async ev=>{ev.preventDefault();try{await api('/api/widget-groups','POST',{name:ev.currentTarget.elements.name.value});ev.currentTarget.reset();await refresh();renderOrganization()}catch(err){toast(err.message,true)}};
$('#tag-form').onsubmit=async ev=>{ev.preventDefault();try{await api('/api/widget-tags','POST',{name:ev.currentTarget.elements.name.value,color:ev.currentTarget.elements.color.value});ev.currentTarget.reset();await refresh();renderOrganization()}catch(err){toast(err.message,true)}};

// Dev 6.1: desktop tools invoke the existing dialogs without duplicating IDs.
const desktopTools=document.getElementById('desktop-tools');
if(desktopTools){
  desktopTools.querySelectorAll('[data-tool]').forEach(button=>button.addEventListener('click',()=>{
    desktopTools.open=false;
    document.getElementById(button.dataset.tool)?.click();
  }));
  document.addEventListener('pointerdown',event=>{if(desktopTools.open&&!desktopTools.contains(event.target))desktopTools.open=false});
}


// Dev 6.6: desktop uses the existing overview strip below the header.
// No moving, cloning or duplicating counters; the mobile structure is preserved.
(function setupDashboardMenus(){
  const filters=document.getElementById('dashboard-filters');
  const tools=document.getElementById('desktop-tools');
  const controls=['filter','group-filter','tag-filter'].map(id=>document.getElementById(id));
  const counter=document.getElementById('active-filters-count');
  function updateFilterCount(){
    const active=controls.filter(node=>node && node.value!=='all').length;
    if(counter){counter.textContent=String(active);counter.hidden=active===0;}
  }
  controls.forEach(node=>node?.addEventListener('change',updateFilterCount));
  document.getElementById('clear-dashboard-filters')?.addEventListener('click',()=>{
    controls.forEach(node=>{if(node)node.value='all'});
    updateFilterCount();
    render();
    if(filters)filters.open=false;
  });
  document.getElementById('overview')?.addEventListener('click',updateFilterCount);
  document.addEventListener('pointerdown',event=>{
    if(filters?.open && !filters.contains(event.target))filters.open=false;
    if(tools?.open && !tools.contains(event.target))tools.open=false;
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      if(filters?.open)filters.open=false;
      if(tools?.open)tools.open=false;
    }
  });
  // Both menus must never overlap each other.
  filters?.addEventListener('toggle',()=>{if(filters.open && tools)tools.open=false});
  tools?.addEventListener('toggle',()=>{if(tools.open && filters)filters.open=false});
  // On mobile the select elements are DIRECT grid children, preserving Dev 6's
  // approved two-column layout. Only desktop reparents the SAME elements into
  // the filter popover (no duplicate IDs, no duplicated event listeners).
  const phone=window.matchMedia('(max-width:900px)');
  const filterRow=document.querySelector('#dashboard .filters');
  const popoverLabels=[...document.querySelectorAll('#dashboard .filter-popover > label')];
  function syncResponsiveFilters(){
    if(!filterRow || popoverLabels.length!==3)return;
    if(phone.matches){
      controls.forEach(node=>{if(node)filterRow.append(node)});
      if(filters)filters.open=false;
    }else{
      controls.forEach((node,index)=>{if(node)popoverLabels[index].append(node)});
      if(filters)filters.open=false;
    }
  }
  syncResponsiveFilters();
  phone.addEventListener('change',syncResponsiveFilters);
  updateFilterCount();
})();

// Dev 7: persistent visual customization; safe image formats only, no HTML/SVG injection.
let currentAppearance={accent:'#a3baa5',scheme:'slate',logo:null};
function applyAppearance(a){
  currentAppearance=a;
  document.documentElement.dataset.scheme=['slate','navy','graphite'].includes(a.scheme)?a.scheme:'slate';
  const color=/^#[0-9a-fA-F]{6}$/.test(a.accent||'')?a.accent:'#a3baa5';
  document.documentElement.style.setProperty('--mint',color);
  document.documentElement.style.setProperty('--accent-custom',color);
  document.querySelectorAll('.brand .logo').forEach(node=>{
    node.querySelectorAll('img').forEach(img=>img.remove());
    const svg=node.querySelector('svg');if(svg)svg.hidden=!!a.logo;
    if(a.logo&&/^data:image\/(png|jpeg|webp);base64,/.test(a.logo)){
      const img=document.createElement('img');img.alt='';img.src=a.logo;img.className='custom-brand-logo';node.append(img);
    }
  });
}
async function loadAppearance(){
  try{const r=await api('/api/appearance');applyAppearance(r);$('#appearance-form').elements.accent.value=r.accent;$('#appearance-form').elements.scheme.value=r.scheme||'slate';$('#appearance-reset-logo').checked=false;const preview=$('#appearance-preview');preview.querySelectorAll('img').forEach(n=>n.remove());if(r.logo){const img=document.createElement('img');img.src=r.logo;img.className='custom-brand-logo';preview.prepend(img)}}catch(e){toast(e.message,true)}
}
$('#appearance-reset-color').onclick=()=>{$('#appearance-form').elements.accent.value='#a3baa5'};
$('#appearance-form').onsubmit=async ev=>{
  ev.preventDefault();const form=ev.currentTarget;
  $('#appearance-error').textContent='';
  try{
    let logo=$('#appearance-reset-logo').checked?null:currentAppearance.logo;
    const file=$('#appearance-logo-file').files[0];
    if(file&&!$('#appearance-reset-logo').checked){
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>32768)throw Error('Nur PNG, JPG oder WebP bis 32 KiB erlaubt');
      logo=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Logo konnte nicht gelesen werden'));reader.readAsDataURL(file)});
    }
    const result=await api('/api/appearance','PUT',{accent:form.elements.accent.value,scheme:form.elements.scheme.value,logo});
    applyAppearance(result);$('#appearance-logo-file').value='';toast('Darstellung gespeichert');
  }catch(e){$('#appearance-error').textContent=e.message}
};
fetch('/api/appearance',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(a=>{if(a)applyAppearance(a)}).catch(()=>{});

// Dev 7.2: Read-only DNS diagnostics live in a dialog; widget dimensions stay unchanged.
async function openDnsDetails(m){
  const dlg=document.getElementById('dns-details-dialog'), area=document.getElementById('dns-details-content');
  document.getElementById('dns-details-title').textContent=m.alias+' · DNS-Diagnose';
  area.replaceChildren(el('p','muted','Lade Prüfdaten …'));dlg.showModal();
  try{
    const data=await api('/api/monitors/'+encodeURIComponent(m.id)+'/diagnostics');area.replaceChildren();
    const latest=data.checks[0];
    if(!latest){area.append(el('p','muted','Noch keine Prüfung abgeschlossen.'));return}
    area.append(el('p','',`Letzte Prüfung: ${date(latest.checked)} · ${latest.status.toUpperCase()} · ${latest.error_code||'Kein Fehlercode'}`));
    area.append(el('p','muted',latest.detail||''));
    const metrics=latest.metrics||{};
    if(metrics.ddns_ipv4)area.append(el('p','',`DDNS IPv4: ${metrics.ddns_ipv4.join(', ')}`));
    if(metrics.egress_ipv4)area.append(el('p','',`Ausgehende WAN-IP: ${metrics.egress_ipv4}`));if(metrics.wildcard_sample)area.append(el('p','muted',`Wildcard-Testname: ${metrics.wildcard_sample}`));
    for(const item of metrics.subdomains||[]){
      const line=el('div','delivery-box');line.append(el('strong','',`${item.status==='up'?'✓':'✗'} ${item.name}`));
      line.append(el('p','muted',`${item.ipv4?'A: '+item.ipv4.join(', '):'CNAME: '+((item.cname||[]).join(', ')||'Keine Antwort')}${item.error?' · '+item.error:''}`));area.append(line);
    }
    for(const failure of metrics.failures||[])area.append(el('p','form-error',failure));
    const changes=data.events.filter(x=>x.event_type==='dns.ip_changed').slice(0,10);
    if(changes.length){area.append(el('h3','','DNS-IP-Änderungen'));for(const item of changes)area.append(el('p','muted',date(item.created)+' · '+item.detail))}
  }catch(err){area.replaceChildren(el('p','form-error',err.message))}
}
document.getElementById('dns-details-close').onclick=()=>document.getElementById('dns-details-dialog').close();


// RC3 user administration: visible, individual grants; no passwords are read back.
function userGrantValues(container){return [...container.querySelectorAll('input[type="checkbox"]:checked')].map(input=>input.value)}
function userGrantPicker(title,items,selected,changed){
  const root=el('fieldset','user-grants');
  const legend=el('legend','',title);root.append(legend);
  const controls=el('div','user-grant-controls');
  const count=el('span','user-grant-count');
  const makeButton=(label,checked)=>{const button=el('button','secondary',label);button.type='button';button.onclick=()=>{for(const input of root.querySelectorAll('input[type="checkbox"]'))input.checked=checked;update()};return button};
  controls.append(count,makeButton('Alle',true),makeButton('Keine',false));root.append(controls);
  const options=el('div','user-grant-options');
  for(const item of items){const label=el('label','user-grant-option');const input=el('input');input.type='checkbox';input.value=String(item.id);input.checked=selected.includes(String(item.id));label.append(input,el('span','',item.name));options.append(label);input.onchange=()=>update()}
  if(!items.length)options.append(el('span','muted','Noch keine Einträge vorhanden.'));
  root.append(options);
  function update(notify=true){const n=userGrantValues(root).length;count.textContent=n+' / '+items.length+' ausgewählt';if(changed&&notify)changed()}
  update(false);return root;
}
function userScopePreview(role,groupIds,tagIds){
  if(role==='admin')return 'Admin: vollständiger Zugriff auf alle Monitore (Freigaben sind für diese Rolle nicht erforderlich).';
  const groups=new Set(groupIds),tags=new Set(tagIds);
  const visible=(state.endpoints||[]).filter(m=>groups.has(String(m.group_id))||(m.tags||[]).some(t=>tags.has(String(t.id))));
  if(!groups.size&&!tags.size)return 'Keine Freigaben: dieser Benutzer sieht keine Monitore.';
  const names=visible.slice(0,5).map(m=>m.alias||m.target);
  return 'Vorschau: '+visible.length+' von '+(state.endpoints||[]).length+' Monitoren sichtbar'+(names.length?' · '+names.join(', ')+(visible.length>5?' …':''):'')+'.';
}
function userPermissionEditor(u){
  const form=el('form','user-edit-form');const roleLabel=el('label','','Rolle');const role=el('select');
  for(const [value,label] of [['viewer','Viewer – nur lesen'],['manager','Standortmanager – zugewiesene Monitore verwalten'],['admin','Admin – Vollzugriff']])role.add(new Option(label,value));
  role.value=u.role;roleLabel.append(role);form.append(roleLabel);
  const pwLabel=el('label','','Neues Passwort (optional)');const pass=el('input');pass.type='password';pass.placeholder='Leer lassen, um Passwort beizubehalten';pass.minLength=14;pass.maxLength=1024;pass.autocomplete='new-password';pwLabel.append(pass);form.append(pwLabel);
  const permissions=el('div','user-permissions');const preview=el('p','user-scope-preview');
  let groups,tags;
  const update=()=>{const isAdmin=role.value==='admin';permissions.hidden=isAdmin;preview.textContent=userScopePreview(role.value,userGrantValues(groups),userGrantValues(tags))};
  groups=userGrantPicker('Freigegebene Gruppen',state.groups||[],u.group_ids||[],update);
  tags=userGrantPicker('Freigegebene Tags',state.tags||[],u.tag_ids||[],update);
  permissions.append(groups,tags);form.append(permissions,preview);role.onchange=update;update();
  const buttons=el('div','dialog-actions');const save=el('button','primary','Änderungen speichern');save.type='submit';buttons.append(save);
  if(u.username!==state.user){const remove=el('button','danger','Benutzer löschen');remove.type='button';remove.onclick=async()=>{if(!confirm('Benutzer '+u.username+' wirklich löschen?'))return;try{await api('/api/users/'+encodeURIComponent(u.username),'DELETE',{});toast('Benutzer gelöscht');await loadUsers()}catch(e){toast(e.message,true)}};buttons.append(remove)}
  const error=el('p','form-error');error.setAttribute('role','alert');form.append(error,buttons);
  form.onsubmit=async ev=>{ev.preventDefault();save.disabled=true;error.textContent='';const data={role:role.value,password:pass.value,group_ids:userGrantValues(groups),tag_ids:userGrantValues(tags)};try{await api('/api/users/'+encodeURIComponent(u.username),'PUT',data);toast('Berechtigungen gespeichert');await loadUsers()}catch(e){error.textContent=e.message;save.disabled=false}};
  return form;
}
async function loadUsers(){
  const create=$('#user-create-form');const role=create.elements.role;
  const groupHost=$('#user-create-groups'),tagHost=$('#user-create-tags');let groups,tags;
  const preview=$('#user-create-preview');
  const changed=()=>{const admin=role.value==='admin';groupHost.hidden=admin;tagHost.hidden=admin;preview.textContent=userScopePreview(role.value,userGrantValues(groups),userGrantValues(tags))};
  groups=userGrantPicker('Freigegebene Gruppen',state.groups||[],[],changed);
  tags=userGrantPicker('Freigegebene Tags',state.tags||[],[],changed);
  groupHost.replaceChildren(groups);tagHost.replaceChildren(tags);role.onchange=changed;changed();
  const box=$('#users-list');box.replaceChildren();
  try{const d=await api('/api/users');for(const u of d.users){
    const panel=el('details','user-card');const summary=el('summary','user-card-summary');
    const main=el('span','user-card-name',u.username);const badge=el('span','user-role-badge',({admin:'Admin',manager:'Standortmanager',viewer:'Viewer'})[u.role]||u.role);
    const count=el('span','user-card-meta',u.role==='admin'?'Vollzugriff':(u.group_ids||[]).length+' Gruppen · '+(u.tag_ids||[]).length+' Tags');
    summary.append(main,badge,count);panel.append(summary,userPermissionEditor(u));box.append(panel);
  }}catch(e){box.append(el('p','form-error',e.message))}
}
$('#user-create-form').onsubmit=async ev=>{
  ev.preventDefault();const f=ev.currentTarget,button=f.querySelector('button[type="submit"]');button.disabled=true;
  const d={username:f.elements.username.value,password:f.elements.password.value,role:f.elements.role.value,group_ids:userGrantValues($('#user-create-groups')),tag_ids:userGrantValues($('#user-create-tags'))};
  try{await api('/api/users','POST',d);f.reset();$('#user-create-error').textContent='';toast('Benutzer erstellt');await loadUsers()}
  catch(e){$('#user-create-error').textContent=e.message}finally{button.disabled=false}
};
