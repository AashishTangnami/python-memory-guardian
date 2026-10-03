/** Static UI only; all report strings arrive via postMessage and use textContent. */
export function reportHtml(nonce: string): string {
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<title>Memory Guardian Report</title><style>
body{font:13px var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);padding:20px;max-width:1400px;margin:auto}
h1{font-size:24px;margin-bottom:6px}h2{font-size:17px}h3{font-size:15px;margin:0 0 8px}.muted{color:var(--vscode-descriptionForeground)}
button,select,input{font:inherit;color:var(--vscode-input-foreground);background:var(--vscode-input-background);border:1px solid var(--vscode-input-border,var(--vscode-panel-border));padding:7px 10px;border-radius:4px}
button{cursor:pointer}button:hover{outline:1px solid var(--vscode-focusBorder)}button:focus-visible{outline:2px solid var(--vscode-focusBorder)}
.tabs,.controls{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.active{background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
.card{border:1px solid var(--vscode-panel-border);border-radius:6px;padding:16px;margin:12px 0}.growing{border-left:4px solid var(--vscode-editorWarning-foreground)}
.badge{font-size:12px;margin-bottom:8px;color:var(--vscode-editorWarning-foreground)}li{margin:7px 0;line-height:1.5}p{line-height:1.5}svg{width:100%;max-width:650px;height:90px}
.path{overflow-wrap:anywhere}#graph{position:relative;min-height:80px;overflow:hidden;border:1px solid var(--vscode-panel-border);border-radius:4px}
.frame{position:absolute;height:29px;text-align:left;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px;border-radius:2px;border:1px solid var(--vscode-editor-background);color:#111;padding:4px}
#details{min-height:65px;margin:12px 0;white-space:pre-wrap}#notice{padding:8px 0}[hidden]{display:none!important}
</style></head><body>
<h1>Memory Guardian</h1><p id="summary" class="muted">Loading report…</p>
<div class="tabs" role="tablist" aria-label="Profile views"><button id="memoryTab" role="tab" aria-selected="true" class="active">Memory diagnosis</button><button id="explorerTab" role="tab" aria-selected="false">Stack Explorer</button></div>
<section id="memory"><p id="memoryNote"></p><div id="cards"></div></section>
<section id="explorer" hidden><p class="muted">Aggregated Python call stacks, including library frames. Width represents sampled time, not chronological order. Native time is estimated at its Python call site; C/C++ stacks are not captured. Across threads, elapsed time can exceed run duration.</p>
<div class="controls"><label>Measure <select id="metric"><option value="elapsed">Sampled elapsed time</option><option value="python">Python CPU estimate</option><option value="native">Native CPU estimate</option><option value="system">Waiting time</option><option value="unsplit">Unclassified time</option></select></label>
<label>Thread <select id="thread"><option value="">All threads</option></select></label><label>Find <input id="search" type="search" placeholder="Function or file"></label><button id="back">Zoom out</button><button id="reset">Reset zoom</button></div>
<div id="notice" class="muted"></div><div id="graph" aria-label="Interactive call stack chart"></div><div id="details"></div><button id="openSource" hidden>Open selected source</button></section>
<script nonce="${nonce}">
const api=acquireVsCodeApi();const $=id=>document.getElementById(id);let data=null,focus=0,selected=null;
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function open(file,line){api.postMessage({type:'open',file,line});}
function tabs(memory){$('memory').hidden=!memory;$('explorer').hidden=memory;for(const [id,on]of [['memoryTab',memory],['explorerTab',!memory]]){$(id).classList.toggle('active',on);$(id).setAttribute('aria-selected',String(on));}if(!memory)draw();}
$('memoryTab').onclick=()=>tabs(true);$('explorerTab').onclick=()=>tabs(false);
function spark(points){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 650 90');svg.setAttribute('role','img');svg.setAttribute('aria-label','Retained memory over sampled time');
const first=points[0][0],last=points[points.length-1][0],peak=Math.max(1,...points.map(p=>p[1]));const line=document.createElementNS(ns,'polyline');line.setAttribute('points',points.map(p=>((p[0]-first)/Math.max(.001,last-first)*630+10)+','+(78-p[1]/peak*65)).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','var(--vscode-charts-blue,#58a6ff)');line.setAttribute('stroke-width','2');svg.append(line);const title=document.createElementNS(ns,'title');title.textContent=first.toFixed(2)+'–'+last.toFixed(2)+' s; maximum '+peak.toFixed(1)+' MB';svg.append(title);return svg;}
function memory(){const cards=$('cards');cards.replaceChildren();$('memoryNote').textContent=data.mode==='precise'?'Growth and retained allocations are evidence to investigate, not proof of an unintended leak. Recommendations below depend on the intended lifetime of your data.':'Run Profile Current File in precise mode to collect retained-memory trends and holder evidence.';
if(data.mode==='precise'&&!data.diagnoses.length)cards.append(el('p','No retained-allocation findings above 1 MB in the recorded snapshots. This does not establish that the program is leak-free.','muted'));
for(const d of data.diagnoses){const card=el('article',null,'card '+d.status);card.append(el('div',d.status==='growing'?'Suspected growing retention':d.status==='retained'?'Retained at end — leak unconfirmed':'Released during run','badge'),el('h3',d.scope),el('p',d.file+':'+d.line,'path muted'));
const verified=data.freshness[d.file];if(!verified)card.append(el('p','Source changed or unavailable. These are historical measurements; re-run to match current code.','badge'));
const list=el('ul');for(const e of d.evidence)list.append(el('li',e));card.append(list);
if(d.points.length>1){card.append(spark(d.points),el('p','Retained MB over '+d.points[0][0].toFixed(2)+'–'+d.points[d.points.length-1][0].toFixed(2)+' s','muted'));}
if(d.holders.length){card.append(el('h3','Observed holders'));for(const h of d.holders)card.append(el('p',h,'path'));}
card.append(el('h3','What to check next'));const steps=el('ol');for(const r of d.recommendations)steps.append(el('li',r));card.append(steps);
if(verified){const b=el('button','Open allocation source');b.onclick=()=>open(d.file,d.line);card.append(b);}cards.append(card);}
if(data.diagnosisCount>data.diagnoses.length)cards.append(el('p','Showing the first '+data.diagnoses.length+' of '+data.diagnosisCount+' findings, ordered by growing retention and retained size.'));}
function details(node){selected=node;const f=data.tree.frames[node.frame];if(!f)return;$('details').textContent=f.name+' — '+node.value.toFixed(3)+' s including callees; '+node.self.toFixed(3)+' s self\\n'+f.file+':'+f.line;$('openSource').hidden=!(f.user&&data.freshness[f.file]);}
$('openSource').onclick=()=>{const f=data.tree.frames[selected.frame];open(f.file,f.line);};
function draw(){if(!data)return;const g=$('graph');g.replaceChildren();const nodes=data.tree.nodes,root=nodes[focus]||nodes[0];let maxDepth=0;const q=$('search').value.toLowerCase();
let note=!data.stacksAvailable?'This older report has no caller stacks. Re-run profiling to use Stack Explorer.':root.value<=0?'No sampled time for this measure and thread.':'Click a frame to zoom. Ctrl/Cmd-click opens verified application source.';
if(data.dropped||data.tree.omitted||data.depthLimited)note+=' Display is partial: stack collection or display limits were reached.';$('notice').textContent=note;
if(root.value>0){const pending=[{id:root.id,x:0,w:100,depth:0}];while(pending.length){const {id,x,w,depth}=pending.pop(),n=nodes[id];if(w<.08)continue;maxDepth=Math.max(maxDepth,depth);const f=data.tree.frames[n.frame];const b=el('button',(f?f.name:'All sampled stacks')+' · '+n.value.toFixed(3)+' s','frame');b.style.left=x+'%';b.style.width=w+'%';b.style.top=(depth*31)+'px';b.style.backgroundColor='hsl('+((n.frame*41+37)%360)+',65%,72%)';if(q&&f&&!((f.name+' '+f.file).toLowerCase().includes(q)))b.style.opacity='.3';b.title=(f?f.name+' '+f.file+':'+f.line:'All stacks')+'; '+n.value.toFixed(3)+' s total, '+n.self.toFixed(3)+' s self';b.onclick=e=>{if(!f)return;details(n);if(e.ctrlKey||e.metaKey){if(f.user&&data.freshness[f.file])open(f.file,f.line);}else{focus=id;draw();}};b.onmouseenter=()=>{if(f)details(n);};g.append(b);let offset=x;for(const child of n.children){const cw=nodes[child].value/root.value*100;pending.push({id:child,x:offset,w:cw,depth:depth+1});offset+=cw;}}}g.style.height=Math.max(80,(maxDepth+1)*31)+'px';}
function filter(){focus=0;selected=null;$('details').textContent='';$('openSource').hidden=true;api.postMessage({type:'filter',metric:$('metric').value,thread:$('thread').value});}
$('metric').onchange=filter;$('thread').onchange=filter;$('search').oninput=draw;$('reset').onclick=()=>{focus=0;draw();};$('back').onclick=()=>{if(!data)return;focus=Math.max(0,data.tree.nodes[focus]?.parent??0);draw();};
window.addEventListener('message',({data:m})=>{if(m.type==='clear'){data=null;$('summary').textContent='Profile cleared. Run profiling to generate a new report.';$('cards').replaceChildren();$('graph').replaceChildren();$('details').textContent='';$('openSource').hidden=true;return;}if(m.type!=='report')return;const changed=!data||data.script!==m.script||data.wall!==m.wall||data.metric!==m.metric||data.thread!==m.thread;data=m;if(changed){focus=0;selected=null;$('openSource').hidden=true;$('details').textContent='';}$('summary').textContent=m.script+' · '+m.wall.toFixed(2)+' s run duration · '+m.mode+' memory mode';$('metric').value=m.metric;const thread=$('thread');thread.replaceChildren();const all=el('option','All threads');all.value='';thread.append(all);for(const [id,name]of m.threads){const o=el('option',name+' ('+id+')');o.value=id;thread.append(o);}thread.value=m.thread;memory();draw();});
api.postMessage({type:'ready'});
</script></body></html>`;
}
