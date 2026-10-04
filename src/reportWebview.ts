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
.summaryGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:16px 0}.summaryGrid .card{margin:0}.summaryGrid strong{display:block;font-size:20px;margin-top:6px}
#timeline svg{height:auto;max-width:none;min-height:190px}.legend{display:flex;gap:18px;flex-wrap:wrap}.legend span:before{content:' ';display:inline-block;width:14px;height:3px;margin:0 6px 3px 0;background:var(--vscode-charts-orange,#e5a248)}.legend .traced:before{background:var(--vscode-charts-blue,#58a6ff)}
.hotspot{display:grid;grid-template-columns:minmax(160px,2fr) minmax(70px,auto) minmax(90px,1fr);gap:7px 12px;align-items:center;padding:9px 0;border-top:1px solid var(--vscode-panel-border)}.hotspot:first-child{border-top:0}.hotspot .path{min-width:0}.hotspot .track{grid-column:1/-1;height:7px;border-radius:4px;background:var(--vscode-editorWidget-background,var(--vscode-input-background));overflow:hidden}.hotspot .fill{display:block;height:100%;background:var(--vscode-charts-blue,#58a6ff)}.hotspot .memory{color:var(--vscode-descriptionForeground);text-align:right}
.path{overflow-wrap:anywhere}#graph{position:relative;min-height:80px;overflow:hidden;border:1px solid var(--vscode-panel-border);border-radius:4px}
.frame{position:absolute;height:29px;text-align:left;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px;border-radius:2px;border:1px solid var(--vscode-editor-background);color:#111;padding:4px}
#details{min-height:65px;margin:12px 0;white-space:pre-wrap}
.frame.hot{outline:2px solid var(--vscode-focusBorder,#0078d4);z-index:1}#crumbs{margin:2px 0 8px;font-size:12px}#crumbs .crumb{padding:2px 6px}#crumbs .crumb:disabled{opacity:1;font-weight:600;cursor:default}
.fn{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}.fn th,.fn td{text-align:left;padding:5px 8px;border-top:1px solid var(--vscode-panel-border)}.fn .num{text-align:right;font-variant-numeric:tabular-nums}.fn th{user-select:none}.fn th.sortable{cursor:pointer}.fn tr.row{cursor:pointer}.fn tr.sel{background:var(--vscode-list-activeSelectionBackground,rgba(0,120,212,.18))}
.merged button{margin:4px 6px 0 0;padding:3px 8px}.nb{list-style:none;padding:0;margin:4px 0}.nb li{margin:3px 0}.nb button{padding:2px 8px}
.kinds{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 10px;font-size:12px}.kinds span:before{content:'';display:inline-block;width:12px;height:12px;border-radius:2px;margin:0 6px -2px 0;background:var(--c)}#notice{padding:8px 0}[hidden]{display:none!important}
.v-better{color:var(--vscode-charts-green,#3fb950)}.v-worse{color:var(--vscode-errorForeground,#f85149);font-weight:600}.v-same,.v-context{color:var(--vscode-descriptionForeground)}.v-new,.v-gone{font-style:italic}.fn td.cell{white-space:nowrap}
.warn{border-left:4px solid var(--vscode-editorWarning-foreground);padding:6px 10px;margin:6px 0}.cmpHead{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
</style></head><body>
<h1>Memory Guardian</h1><p id="summary" class="muted">Loading report…</p>
<div class="tabs" role="tablist" aria-label="Profile views"><button id="overviewTab" role="tab" aria-selected="true" class="active">Overview</button><button id="memoryTab" role="tab" aria-selected="false">Memory diagnosis</button><button id="explorerTab" role="tab" aria-selected="false">Stack Explorer</button><button id="compareTab" role="tab" aria-selected="false">Compare</button></div>
<section id="overview"><div id="summaryCards" class="summaryGrid"></div><p class="muted">Measurements include profiler overhead; precise mode can slow allocation-heavy code.</p><div id="memoryTimeline" class="card"><h2>Process memory over time</h2><div id="timeline"></div><p class="legend muted"><span>Process RSS</span><span class="traced">Traced Python allocations</span></p><p id="chartReadout" class="muted"></p><p id="timelineNote" class="muted"></p></div><div class="card"><h2>Top sampled lines</h2><p class="muted">Bars show each line's share of attributed runtime. Across threads, sampled time may exceed run duration. Select a verified source line to open it.</p><div id="hotspots"></div></div></section>
<section id="memory" hidden><p id="memoryNote"></p><div id="cards"></div></section>
<section id="explorer" hidden><p class="muted">Aggregated Python call stacks, including library frames. Width represents sampled time, not chronological order. Native time is estimated at its Python call site; C/C++ stacks are not captured. Across threads, elapsed time can exceed run duration.</p>
<div class="controls"><label>Measure <select id="metric"><option value="elapsed">Sampled elapsed time</option><option value="python">Python CPU estimate</option><option value="native">Native CPU estimate</option><option value="system">Waiting time</option><option value="unsplit">Unclassified time</option><option value="mem_peak" class="mem">Memory at peak snapshot</option><option value="mem_exit" class="mem">Memory held at exit</option></select></label>
<label>Thread <select id="thread"><option value="">All threads</option></select></label><label>Direction <select id="direction"><option value="top">Top-down (callers above)</option><option value="bottom">Bottom-up (where it is spent, callers below)</option></select></label><label>Frames <select id="frames"><option value="grouped">Group libraries and internals</option><option value="all">All frames</option><option value="mine">Only my code</option></select></label><label>Find <input id="search" type="search" placeholder="Function, file or library"></label><button id="back">Zoom out</button><button id="reset">Reset zoom</button><button id="hot">Hottest path</button></div>
<div id="notice" class="muted"></div><div class="kinds" aria-label="Frame colors"><span style="--c:hsl(212,75%,72%)">Your code</span><span style="--c:linear-gradient(90deg,hsl(28,70%,72%) 0 34%,hsl(280,70%,72%) 34% 67%,hsl(340,70%,72%) 67%)">Installed packages (one color each)</span><span style="--c:hsl(140,32%,72%)">Python standard library</span><span style="--c:hsl(0,0%,76%)">Python internals (imports, generated code)</span></div><nav id="crumbs" aria-label="Zoom path"></nav><div id="graph" aria-label="Interactive call stack chart"></div><div id="details"></div><button id="openSource" hidden>Open selected source</button>
<h2>Top functions</h2><p class="muted">Each function's time summed across every call path, so a function called from many places shows its real cost. Self time is spent in the function itself; total time also includes what it calls, counted once per stack. Click a row to highlight it in the chart and see its callers and callees.</p>
<table id="fntable" class="fn"></table><p id="fncount" class="muted"></p><button id="fnmore" hidden></button><div id="neighbors"></div></section>
<section id="compare" hidden><p class="muted">Did a change help? Save a run as a baseline, change your code, profile again with the same workload, and compare here. Functions are matched by file and name, so line numbers can move.</p>
<div class="controls"><label>Compare with <select id="baseline"></select></label><button id="compareFile">Other profile file…</button><button id="saveBaseline">Save this run as a baseline</button><label><input id="onlyChanges" type="checkbox" checked> Only changes beyond run-to-run variation</label></div>
<div id="compareBody"></div></section>
<script nonce="${nonce}">
const api=acquireVsCodeApi();const $=id=>document.getElementById(id);let data=null,focus=0,selected=null;
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function dur(s){return s>=1?s.toFixed(2)+' s':s>=.01?Math.round(s*1000)+' ms':(s*1000).toFixed(1)+' ms';}
function bytes(v){return v>=1e9?(v/1e9).toFixed(2)+' GB':v>=1e6?(v/1e6).toFixed(1)+' MB':v>=1e3?(v/1e3).toFixed(1)+' KB':Math.round(v)+' B';}
function mem(){return !!data&&data.unit==='bytes';}
function fmt(v){return mem()?bytes(v):dur(v);}
function share(v){const p=v/(data.tree.nodes[0].value||1)*100;return p<.1?'<0.1%':p.toFixed(1)+'%';}
function own(n,f){const it=f&&f.group?'these frames':'this function',calls=f&&f.group?'they call':'this one calls';
if(mem())return n.self>=n.value-1e-9?'all allocated directly in '+it:n.self<=0?'all allocated by functions '+calls:bytes(n.self)+' allocated directly in '+it;
return n.self>=n.value-1e-9?'all of it in '+it:n.self<=0?'all of it in functions '+calls:dur(n.self)+' in '+it+' itself';}
function hash(s){let h=7;for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))|0;return Math.abs(h);}
const PACKAGE_HUES=[28,280,340,48,310,8];
function color(f){if(!f)return 'hsl(0,0%,86%)';const o=originOf(f),light=(68+hash(f.name)%10)+'%';
if(o.kind==='user')return 'hsl(212,75%,'+light+')';if(o.kind==='stdlib')return 'hsl(140,32%,'+light+')';
if(o.kind==='internal')return 'hsl(0,0%,'+(72+hash(f.name)%8)+'%)';return 'hsl('+PACKAGE_HUES[hash(o.label)%PACKAGE_HUES.length]+',70%,'+light+')';}
function boxName(f){return f.group?f.name+' · '+f.group.count+' frames':f.name;}
function members(f){return f.group.names.join(', ')+(f.group.count>f.group.names.length?', …':'');}
function originOf(f){return f.origin||{kind:f.user?'user':'library',label:f.file+':'+f.line,detail:''};}
function open(file,line){api.postMessage({type:'open',file,line});}
function tabs(view){for(const [name,id]of [['overview','overviewTab'],['memory','memoryTab'],['explorer','explorerTab'],['compare','compareTab']]){const on=name===view;$(name).hidden=!on;$(id).classList.toggle('active',on);$(id).setAttribute('aria-selected',String(on));}if(view==='explorer')draw();}
$('overviewTab').onclick=()=>tabs('overview');$('compareTab').onclick=()=>tabs('compare');$('memoryTab').onclick=()=>tabs('memory');$('explorerTab').onclick=()=>tabs('explorer');
function metric(label,value){const card=el('div',null,'card');card.append(el('div',label,'muted'),el('strong',value));return card;}
function memoryChart(points){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 700 210');svg.setAttribute('role','img');svg.setAttribute('aria-label',data.overview.rssKind?'Process RSS and traced Python memory over elapsed time':'Traced Python memory over elapsed time');
const left=52,top=14,width=630,height=164,maxT=Math.max(.001,data.wall,points[points.length-1][0]);let maxMb=1;for(const p of points)maxMb=Math.max(maxMb,p[1],p[2]);
function node(tag,attrs,label){const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(label!=null)n.textContent=label;svg.append(n);return n;}
for(let i=0;i<=4;i++){const y=top+height-i*height/4;node('line',{x1:left,y1:y,x2:left+width,y2:y,stroke:'var(--vscode-panel-border,#777)','stroke-width':1});node('text',{x:2,y:y+4,fill:'var(--vscode-descriptionForeground)','font-size':11},(maxMb*(i/4)).toFixed(0)+' MB');}
node('text',{x:left,y:205,fill:'var(--vscode-descriptionForeground)','font-size':11},'0 s');node('text',{x:left+width-42,y:205,fill:'var(--vscode-descriptionForeground)','font-size':11},maxT.toFixed(1)+' s');
function series(index,color){const shown=index===1&&data.tracingLostS!=null?points.filter(p=>p[0]<=data.tracingLostS):points;if(shown.length<2)return;node('polyline',{points:shown.map(p=>(left+p[0]/maxT*width).toFixed(1)+','+(top+height-p[index]/maxMb*height).toFixed(1)).join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round'});}
if(data.overview.rssKind)series(2,'var(--vscode-charts-orange,#e5a248)');if(data.mode==='precise')series(1,'var(--vscode-charts-blue,#58a6ff)');
const readout=$('chartReadout');function describe(p){readout.textContent=p[0].toFixed(2)+' s'+(data.overview.rssKind?' · RSS '+p[2].toFixed(1)+' MB':'')+(data.mode==='precise'?' · traced '+p[1].toFixed(1)+' MB':'');}describe(points[points.length-1]);
const toT=e=>{const rect=svg.getBoundingClientRect(),x=(e.clientX-rect.left)/rect.width*700;return Math.max(0,Math.min(1,(x-left)/width))*maxT;};
const sel=node('rect',{x:left,y:top,width:0,height:height,fill:'var(--vscode-editor-selectionBackground,rgba(0,120,212,.25))',visibility:'hidden'});
const show=(a,b)=>{sel.setAttribute('x',left+a/maxT*width);sel.setAttribute('width',Math.max(1,(b-a)/maxT*width));sel.setAttribute('visibility','visible');};
if(data.window)show(data.window.from,data.window.to);let dragFrom=null;
svg.onpointerdown=e=>{if(data.mode!=='precise')return;dragFrom=toT(e);svg.setPointerCapture(e.pointerId);};
svg.onpointermove=e=>{const target=toT(e);let nearest=points[0];for(const p of points)if(Math.abs(p[0]-target)<Math.abs(nearest[0]-target))nearest=p;describe(nearest);if(dragFrom!=null)show(Math.min(dragFrom,target),Math.max(dragFrom,target));};
svg.onpointerup=e=>{if(dragFrom==null)return;const t=toT(e),a=Math.min(dragFrom,t),b=Math.max(dragFrom,t);dragFrom=null;
if(b-a<maxT*.01){if(data.window)show(data.window.from,data.window.to);else sel.setAttribute('visibility','hidden');return;}api.postMessage({type:'window',from:a,to:b});tabs('memory');};
return svg;}
function overview(){const o=data.overview,grid=$('summaryCards');grid.replaceChildren();grid.append(metric('Run duration',data.wall.toFixed(2)+' s'));
if(o.cpuS!=null)grid.append(metric('Process CPU',o.cpuS.toFixed(2)+' s'));if(o.samples!=null)grid.append(metric('Time samples',String(o.samples)));
if(data.mode!=='off'&&o.rssKind&&o.rssPeakMb!=null)grid.append(metric('Peak process RSS',o.rssPeakMb.toFixed(1)+' MB'));
if(data.mode!=='off'&&o.rssKind&&o.rssStartMb!=null&&o.rssEndMb!=null)grid.append(metric(o.rssKind==='peak'?'RSS peak start → end':'RSS start → exit',o.rssStartMb.toFixed(1)+' → '+o.rssEndMb.toFixed(1)+' MB'));
if(data.mode==='precise'&&o.tracedPeakMb!=null)grid.append(metric('Peak traced allocations',o.tracedPeakMb.toFixed(1)+' MB'));
if(data.mode==='precise'&&o.nativeUntracedMb!=null){const c=metric('Native memory (estimate)',o.nativeUntracedMb.toFixed(1)+' MB');c.title='Process memory beyond what Python objects account for: C extensions and libraries with their own allocators (NumPy, PyArrow, Polars and others). Estimated from process memory, which grows where memory is first written. Lines show their share as "native ≈".';grid.append(c);}
if(data.mode==='precise'){const finding=metric('Suspected growing lines',String(data.growingCount));const jump=el('button','Review memory diagnosis');jump.onclick=()=>tabs('memory');finding.append(jump);grid.append(finding);}
const memoryVisible=data.mode!=='off'&&o.timeline.length>1&&(o.rssKind||data.mode==='precise');$('memoryTimeline').hidden=!memoryVisible;
if(memoryVisible){$('timeline').replaceChildren(memoryChart(o.timeline));$('memoryTimeline').querySelector('.legend span').hidden=!o.rssKind;$('memoryTimeline').querySelector('.traced').hidden=data.mode!=='precise';$('timelineNote').textContent=(o.rssKind==='peak'?'This platform provides a running RSS peak, so decreases are not visible. ':o.rssKind?'RSS is process memory, not retained Python objects: it grows when memory is first written, so growth is charged to the line that writes it, and it often stays high after memory is freed. ':'')+'Current profiles include an exit sample; older profiles may end before the final snapshot.'+(data.mode==='precise'?' Drag across the chart to examine one time window in Memory diagnosis.':'');}
const list=$('hotspots');list.replaceChildren();if(!o.topLines.length){list.append(el('p','No user-code lines were sampled.','muted'));return;}
const max=Math.max(.001,o.topLines[0].timeS);for(const item of o.topLines){const row=el('div',null,'hotspot'),name=item.file.slice(Math.max(item.file.lastIndexOf('/'),item.file.lastIndexOf(String.fromCharCode(92)))+1)+':'+item.line;
const source=data.freshness[item.file]?el('button',name,'path'):el('span',name+' (historical)','path muted');source.title=item.file+':'+item.line;if(data.freshness[item.file])source.onclick=()=>open(item.file,item.line);
row.append(source,el('span',item.timeS.toFixed(2)+' s · '+(item.share*100).toFixed(1)+'%'),el('span',item.memory,'memory'));
const track=el('div',null,'track'),fill=el('span',null,'fill');fill.style.width=Math.max(1,item.timeS/max*100)+'%';track.append(fill);row.append(track);list.append(row);}}
function spark(points){const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 650 90');svg.setAttribute('role','img');svg.setAttribute('aria-label','Retained memory over sampled time');
const first=points[0][0],last=points[points.length-1][0],peak=Math.max(1,...points.map(p=>p[1]));const line=document.createElementNS(ns,'polyline');line.setAttribute('points',points.map(p=>((p[0]-first)/Math.max(.001,last-first)*630+10)+','+(78-p[1]/peak*65)).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','var(--vscode-charts-blue,#58a6ff)');line.setAttribute('stroke-width','2');svg.append(line);const title=document.createElementNS(ns,'title');title.textContent=first.toFixed(2)+'–'+last.toFixed(2)+' s; maximum '+peak.toFixed(1)+' MB';svg.append(title);return svg;}
function memory(){const cards=$('cards');cards.replaceChildren();$('memoryNote').textContent=data.mode==='precise'?'Growth and retained allocations are evidence to investigate, not proof of an unintended leak. Recommendations below depend on the intended lifetime of your data.':'Run Profile Current File in precise mode to collect retained-memory trends and holder evidence.';
if(data.window){const banner=el('div',null,'card');banner.append(el('strong','Window: '+data.window.from.toFixed(2)+' s – '+data.window.to.toFixed(2)+' s'),el('p','Each card uses only the line snapshots inside this window: its highest value there and what was held at its last snapshot. Memory stacks in Stack Explorer cover only the peak and exit snapshots.','muted'));
const clear=el('button','Show whole run');clear.onclick=()=>api.postMessage({type:'window',clear:true});banner.append(clear);cards.append(banner);}
if(data.mode==='precise'&&!data.diagnoses.length)cards.append(el('p','No retained-allocation findings above 1 MB in the recorded snapshots. This does not establish that the program is leak-free.','muted'));
for(const d of data.diagnoses){const card=el('article',null,'card '+d.status);card.append(el('div',d.status==='growing'?'Suspected growing retention':d.status==='retained'?(data.window?'Held at the end of this window':'Retained at end — leak unconfirmed'):(data.window?'Released within this window':'Released during run'),'badge'),el('h3',d.scope),el('p',d.file+':'+d.line,'path muted'));
const verified=data.freshness[d.file];if(!verified)card.append(el('p','Source changed or unavailable. These are historical measurements; re-run to match current code.','badge'));
const list=el('ul');for(const e of d.evidence)list.append(el('li',e));card.append(list);
if(d.points.length>1){card.append(spark(d.points),el('p','Retained MB over '+d.points[0][0].toFixed(2)+'–'+d.points[d.points.length-1][0].toFixed(2)+' s','muted'));}
if(d.holders.length){card.append(el('h3','Observed holders'));for(const h of d.holders)card.append(el('p',h,'path'));}
card.append(el('h3','What to check next'));const steps=el('ol');for(const r of d.recommendations)steps.append(el('li',r));card.append(steps);
if(verified){const b=el('button','Open allocation source');b.onclick=()=>open(d.file,d.line);card.append(b);}cards.append(card);}
const lo=data.largest;if(lo){const sec=el('div',null,'card');sec.append(el('h3','Largest objects alive at exit'),
el('p','Objects still referenced by module globals or attributes of your own class instances when the script ended, sized by what each object reports (sys.getsizeof); containers include their items one level deep. Libraries that do not report their memory (Polars, for one) show only a small wrapper here; their memory appears as "native ≈" on the lines that created it'+(data.mode==='precise'?'':' in a precise run')+'.'+(lo.complete?'':' The scan hit its time limit, so this list may be incomplete.'),'muted'));
if(!lo.objects.length)sec.append(el('p','No object of 1 MB or more was still referenced at exit.','muted'));
else{const t=el('table',null,'fn'),h=el('tr');for(const c of ['Held by','Type','Size','Items'])h.append(el('th',c,c==='Size'||c==='Items'?'num':''));t.append(h);
for(const o of lo.objects){const tr=el('tr');tr.append(el('td',o.holder,'path'),el('td',o.type,'muted'),el('td',(o.estimated?'≈ ':'')+o.mb.toFixed(1)+' MB','num'),el('td',o.items==null?'—':String(o.items),'num'));t.append(tr);}sec.append(t);}cards.append(sec);}
if(data.diagnosisCount>data.diagnoses.length)cards.append(el('p','Showing the first '+data.diagnoses.length+' of '+data.diagnosisCount+' findings, ordered by growing retention and retained size.'));}
const MIN_PX=40,LABEL_PX=56,MAX_DEPTH=12,ROW=31;let hot=new Set(),mark=null,markName='',sortBy='self',showRows=50;
function fkey(f){return JSON.stringify([f.file,f.first_line,f.name]);}
function measure(){return data.metric==='mem_peak'?'memory held at the peak snapshot':data.metric==='mem_exit'?'memory held at exit':data.metric==='elapsed'?'all sampled time':'this measure';}
function memoryNote(){const ms=data.memoryStacks;if(!mem()||!ms)return '';const t=data.metric==='mem_peak'?ms.peak:ms.exit;if(!t)return ' No peak snapshot was recorded.';
let n=' Each stack is a tracemalloc traceback of at most '+ms.depth+' frames: the allocating function'+(ms.depth>1?' and up to '+(ms.depth-1)+(ms.depth===2?' caller':' callers'):'')+(t.truncated?'; deeper callers are cut off':'')+'. Raise pythonMemoryGuardian.profile.frames for deeper stacks (slower).';
n+=' This snapshot was taken at '+t.t.toFixed(2)+' s and holds '+bytes(t.totalBytes)+(t.otherBytes?' ('+bytes(t.otherBytes)+' in small stacks not listed)':'')+'.';
if(data.metric==='mem_peak'&&ms.peakTracedMb!=null&&ms.peakTracedMb*1e6>1.5*t.totalBytes)n+=' Note: traced memory peaked at '+ms.peakTracedMb.toFixed(1)+' MB, more than this snapshot holds. The profiler snapshots when memory plateaus at a new high and each time it doubles, so a peak it could not see (for example inside one C call that holds the GIL) is captured at half or more; the line labels and Memory diagnosis use the same snapshots.';
if(data.metric==='mem_exit'&&data.tracingLostS!=null)n+=' The script stopped tracemalloc, so this is the last snapshot before that, not the exit.';return n;}
function where(f){const o=originOf(f);return f.group?(o.detail?o.detail+' · ':'')+f.group.count+' frames grouped: '+members(f)+'. Choose "All frames" to see each one':o.kind==='user'?'your code, '+o.label:o.label+(o.detail?' — '+o.detail:'');}
function spentIn(node){let n=node;while(data.tree.nodes[n.parent]&&n.parent!==0)n=data.tree.nodes[n.parent];return n;}
function inverseLine(node){const leaf=spentIn(node),lf=data.tree.frames[leaf.frame],name=lf?boxName(lf):'it';
return node===leaf?fmt(node.value)+(mem()?' allocated directly in this function':' spent in this function itself')+' · '+share(node.value)+' of '+measure()
:fmt(node.value)+' of '+name+(mem()?"'s memory":"'s time")+' was reached through this caller · '+share(node.value)+' of '+measure();}
function details(node){selected=node;const f=data.tree.frames[node.frame];if(!f)return;
$('details').textContent=f.name+' · '+where(f)+'\\n'+(data.inverted?inverseLine(node):fmt(node.value)+' total · '+share(node.value)+' of '+measure()+' · '+own(node,f));$('openSource').hidden=!(f.user&&data.freshness[f.file]);}
function detailsMerged(ids,value){selected=null;$('openSource').hidden=true;const d=$('details');
d.replaceChildren(el('div',ids.length+' calls too narrow to show · '+fmt(value)+' total · '+share(value)+' of '+measure()+'. Click one to zoom in:'));
const list=el('div',null,'merged');for(const id of ids.slice(0,15)){const n=data.tree.nodes[id],f=data.tree.frames[n.frame];const b=el('button',(f?boxName(f):'?')+' · '+fmt(n.value));b.onclick=()=>{focus=id;draw();details(n);};list.append(b);}
if(ids.length>15)list.append(el('span','and '+(ids.length-15)+' more','muted'));d.append(list);}
$('openSource').onclick=()=>{const f=data.tree.frames[selected.frame];open(f.file,f.line);};
function matches(f,q){return (f.name+' '+f.file+' '+originOf(f).label+' '+(f.group?f.group.names.join(' '):'')).toLowerCase().includes(q);}
function marked(f){if(!mark||!f)return false;if(!f.group)return fkey(f)===mark;const r=(data.functions||[]).find(x=>x.key===mark);return !!r&&originOf(f).label===r.origin.label&&f.group.names.includes(r.name);}
function crumbs(){const c=$('crumbs');c.replaceChildren();const path=[];for(let id=focus;id>=0;id=data.tree.nodes[id].parent)path.unshift(id);
path.forEach((id,i)=>{const n=data.tree.nodes[id],f=data.tree.frames[n.frame];if(i)c.append(el('span',' › ','muted'));const b=el('button',f?boxName(f):'All','crumb');b.disabled=id===focus;b.onclick=()=>{focus=id;draw();};c.append(b);});}
function box(x,w,depth,text,bg,title){const b=el('button',w>=LABEL_PX?text:'','frame');b.style.left=x+'px';b.style.width=Math.max(1,w-1)+'px';b.style.top=(depth*ROW)+'px';b.style.background=bg;b.title=title;$('graph').append(b);return b;}
function draw(){if(!data||$('explorer').hidden)return;const g=$('graph');g.replaceChildren();const nodes=data.tree.nodes;if(!nodes[focus])focus=0;
const root=nodes[focus],W=g.clientWidth||800,q=$('search').value.toLowerCase();let maxDepth=0;
let note=!data.stacksAvailable?'This older report has no caller stacks. Re-run profiling to use Stack Explorer.':root.value<=0?'No sampled time for this measure and thread.':'Click a frame to zoom. Ctrl/Cmd-click opens verified application source. Calls too narrow to read are merged into one striped box per caller.';
if(data.inverted)note+=' Bottom-up: each top box is where '+(mem()?'memory was allocated':'time was spent')+', merged by function; the boxes below it are the callers that led there.';if(data.frames==='mine')note+=mem()?' Memory allocated in libraries and Python internals is counted in your function that called them.':' Time spent in libraries and Python internals is counted in your function that called them.';note+=memoryNote();if(data.dropped||data.tree.omitted||data.depthLimited)note+=' Display is partial: stack collection or display limits were reached.';$('notice').textContent=note;crumbs();
if(root.value>0){const pending=[{id:focus,x:0,w:W,depth:0}];while(pending.length){const it=pending.pop();maxDepth=Math.max(maxDepth,it.depth);
if(it.cut){const b=box(it.x,it.w,it.depth,'beyond display limit · '+fmt(it.value),'repeating-linear-gradient(135deg,hsl(0,0%,70%) 0 3px,hsl(0,0%,84%) 3px 7px)','Deeper calls worth '+fmt(it.value)+' are not drawn: the chart keeps at most 25,000 call paths. Their time is still counted in every box above. Use Top functions, or narrow by thread or Frames, to see them.');b.onclick=b.onmouseenter=()=>{selected=null;$('openSource').hidden=true;$('details').textContent=b.title;};continue;}
if(it.small){const b=box(it.x,it.w,it.depth,'+'+it.small.length+' smaller calls · '+fmt(it.value),'repeating-linear-gradient(45deg,hsl(0,0%,80%) 0 4px,hsl(0,0%,89%) 4px 8px)',it.small.length+' calls too narrow to show, '+fmt(it.value)+' in total. Click to list them.');
if(it.w<LABEL_PX&&it.w>=22)b.textContent='+'+it.small.length;b.onmouseenter=b.onclick=()=>detailsMerged(it.small,it.value);continue;}
const n=nodes[it.id],f=data.tree.frames[n.frame],deeper=it.depth>=MAX_DEPTH&&n.children.length>0;
const title=!f?'All sampled stacks · '+fmt(n.value):(f.group?boxName(f)+': '+members(f)+'\\n'+fmt(n.value)+' total, '+own(n,f):f.name+' — '+originOf(f).label+'\\n'+(data.inverted?inverseLine(n):fmt(n.value)+' total, '+own(n,f))+'\\n'+f.file+':'+f.line)+(deeper?'\\nDeeper calls continue: click to zoom in.':'');
const b=box(it.x,it.w,it.depth,(f?boxName(f):'All sampled stacks')+' · '+fmt(n.value)+(deeper?' ▸':''),color(f),title);
if(q&&f&&!matches(f,q))b.style.opacity='.3';if(hot.has(it.id)||marked(f))b.classList.add('hot');
b.onclick=e=>{if(!f)return;details(n);if(e.ctrlKey||e.metaKey){if(f.user&&data.freshness[f.file])open(f.file,f.line);}else{focus=it.id;draw();}};b.onmouseenter=()=>{if(f)details(n);};
if(deeper)continue;let offset=it.x,smallValue=0;const small=[];
for(const child of n.children){const cw=nodes[child].value/root.value*W;if(cw<MIN_PX){small.push(child);smallValue+=nodes[child].value;continue;}pending.push({id:child,x:offset,w:cw,depth:it.depth+1});offset+=cw;}
if(small.length){const sw=smallValue/root.value*W;if(sw>=3)pending.push({small,value:smallValue,x:offset,w:sw,depth:it.depth+1});offset+=sw;}
if(n.omitted>0){const ow=n.omitted/root.value*W;if(ow>=3)pending.push({cut:true,value:n.omitted,x:offset,w:ow,depth:it.depth+1});}}}
g.style.height=Math.max(80,(maxDepth+1)*ROW)+'px';}
function hottest(){if(!data)return;const nodes=data.tree.nodes,path=[focus],start=nodes[focus].value;let cur=nodes[focus];
// Follow the biggest callee while it carries a real share: stop below 5% of the starting time, or where a
// function spends more time in itself than in any single callee (that function is the hotspot).
while(cur.children.length){const next=nodes[cur.children[0]];if(next.value<.05*start||cur.self>=next.value)break;path.push(next.id);cur=next;}
hot=new Set(path);focus=path[Math.max(0,path.length-MAX_DEPTH)];draw();details(nodes[path[path.length-1]]);}
function fnTable(){const t=$('fntable');t.replaceChildren();if(!data)return;const q=$('search').value.toLowerCase();
const memLabel=mem()?null:data.mode==='precise'?'Memory':data.mode==='fast'?'RSS growth':null;const nativeLabel=!mem()&&data.mode==='precise'&&(data.functions||[]).some(r=>r.nativeMb!=null)?'Native ≈':null;const head=el('tr');
for(const [k,label,tip] of [['name','Function',''],['where','Where',''],['self',mem()?'Allocated here':'Self time',mem()?'Memory allocated directly by the function':'Time spent in the function itself'],['total',mem()?'Including callees':'Total time',mem()?'Memory allocated by the function or anything it calls, counted once per stack':'Time with the function anywhere in the stack, counted once per stack'],['memory',memLabel,data.mode==='precise'?'Largest of: net traced growth while it ran, memory held at the peak snapshot, or a brief spike, for your own functions':'Process memory growth while the function ran (charged where memory is first written), for your own functions'],['native',nativeLabel,'Process memory growth beyond Python objects while it ran: native memory from C extensions, estimated, for your own functions'],['callers','Called from','Distinct functions that call it']]){
if(!label)continue;const sortable=['self','total','memory','native','callers'].includes(k);const th=el('th',label+(sortBy===k?' ▾':''),sortable?'num sortable':'');th.title=tip;if(sortable)th.onclick=()=>{sortBy=k;fnTable();};head.append(th);}t.append(head);
const value=r=>sortBy==='memory'?(r.memoryMb==null?-1:r.memoryMb):sortBy==='native'?(r.nativeMb==null?-1:r.nativeMb):r[sortBy];
const rows=(data.functions||[]).filter(r=>!q||(r.name+' '+r.file+' '+r.origin.label).toLowerCase().includes(q)).sort((a,b)=>value(b)-value(a));
for(const r of rows.slice(0,showRows)){const tr=el('tr',null,'row'+(r.key===mark?' sel':''));
tr.append(el('td',r.name),el('td',r.origin.label,'muted'),el('td',fmt(r.self)+' · '+share(r.self),'num'),el('td',fmt(r.total)+' · '+share(r.total),'num'));
const mbCell=v=>v==null?'—':v===0?'0 MB':v<.1?'<0.1 MB':v.toFixed(1)+' MB';if(memLabel)tr.append(el('td',mbCell(r.memoryMb),'num'));if(nativeLabel)tr.append(el('td',r.nativeMb==null?'—':'+'+mbCell(r.nativeMb),'num'));tr.append(el('td',r.callers?r.callers+(r.callers===1?' function':' functions'):'—','num'));
tr.onclick=()=>select(r.key,r.name);t.append(tr);}
const more=$('fnmore');more.hidden=rows.length<=showRows;more.textContent='Show '+Math.min(50,rows.length-showRows)+' more of '+rows.length;
$('fncount').textContent=rows.length?'':'No functions match.';}
$('fnmore').onclick=()=>{showRows+=50;fnTable();};
function select(key,name){mark=key;markName=name;hot=new Set();fnTable();draw();$('neighbors').replaceChildren(el('p','Loading callers and callees…','muted'));api.postMessage({type:'neighbors',key});}
function neighborsView(m){if(m.key!==mark)return;const panel=$('neighbors');panel.replaceChildren();const r=(data.functions||[]).find(x=>x.key===m.key);
panel.append(el('h3',markName+' · '+fmt(m.value)+' total, '+fmt(m.self)+(mem()?' allocated directly':' in the function itself')));
if(r&&r.user&&data.freshness[r.file]){const b=el('button','Open source');b.onclick=()=>open(r.file,r.line);panel.append(b);}
for(const [title,list,empty] of [['Called by',m.callers,'Nothing above it: sampled stacks start here.'],['Calls',m.callees,'It calls nothing that was sampled.']]){panel.append(el('h4',title));
if(!list.length){panel.append(el('p',empty,'muted'));continue;}const ul=el('ul',null,'nb');
for(const n of list){const li=el('li'),b=el('button',n.name);b.onclick=()=>select(n.key,n.name);li.append(b,el('span',' · '+n.origin.label+' · '+fmt(n.value)+' ('+Math.round(n.value/(m.value||1)*100)+'% of '+markName+')','muted'));ul.append(li);}panel.append(ul);}}
function filter(){focus=0;selected=null;$('details').textContent='';$('openSource').hidden=true;api.postMessage({type:'filter',metric:$('metric').value,thread:$('thread').value,frames:$('frames').value,inverted:$('direction').value==='bottom'});}
$('metric').onchange=filter;$('thread').onchange=filter;$('frames').onchange=filter;$('direction').onchange=filter;$('search').oninput=()=>{draw();fnTable();};$('hot').onclick=hottest;
$('reset').onclick=()=>{focus=0;hot=new Set();draw();};$('back').onclick=()=>{if(!data)return;focus=Math.max(0,data.tree.nodes[focus]?.parent??0);draw();};
const VERDICT={better:'better',worse:'worse',same:'≈ same',context:'context',new:'new',gone:'removed'};
function num(v,unit){return v==null?'—':unit==='s'?dur(v):(Math.abs(v)<.05?'0':v.toFixed(1))+' MB';}
function sign(v,unit){return (v>0?'+':v<0?'−':'±')+num(Math.abs(v),unit);}
function cell(d,unit){const td=el('td',null,'num cell v-'+d.verdict);
if(d.verdict==='new'||d.verdict==='gone')td.textContent=(d.verdict==='new'?num(d.cur,unit):num(d.base,unit))+' · '+VERDICT[d.verdict];
else td.textContent=num(d.base,unit)+' → '+num(d.cur,unit)+(d.verdict==='same'?'':' ('+sign(d.delta,unit)+')');
td.title=d.verdict==='same'?'Within run-to-run variation of ±'+num(d.noise,unit)+': not a measurable change.':d.verdict==='context'?'Changed because another part of the run changed; see the note above.':d.verdict==='better'||d.verdict==='worse'?'Change exceeds run-to-run variation (±'+num(d.noise,unit)+').':'';return td;}
function shortPath(f){return f.slice(Math.max(f.lastIndexOf('/'),f.lastIndexOf(String.fromCharCode(92)))+1);}
function deltaTable(rows,cmp,kind){const t=el('table',null,'fn'),h=el('tr');
for(const label of [kind==='line'?'Line':'Function',kind==='line'?'Function':'Where','Time',...cmp.columns.map(c=>c.label),'Leak'])h.append(el('th',label));t.append(h);
for(const r of rows){const tr=el('tr');const where=r.line!=null?shortPath(r.file)+':'+r.line+(r.baseLine!=null&&r.baseLine!==r.line?' (was '+r.baseLine+')':''):shortPath(r.file)+(r.baseLine!=null?':'+r.baseLine+' (removed)':'');
if(kind==='line'){const b=data.freshness[r.file]&&r.line!=null?el('button',where,'path'):el('span',where,'path');if(b.tagName==='BUTTON')b.onclick=()=>open(r.file,r.line);const td=el('td');td.append(b);tr.append(td,el('td',r.name,'muted'));}
else{tr.append(el('td',r.name));const td=el('td');if(r.line!=null&&data.freshness[r.file]){const b=el('button',where,'path');b.onclick=()=>open(r.file,r.line);td.append(b);}else td.append(el('span',where,'path muted'));tr.append(td);}
tr.append(cell(r.time,'s'));for(const c of cmp.columns)tr.append(cell(r.values[c.key],'MB'));
tr.append(el('td',r.leak==='new'?'new leak':r.leak==='gone'?'leak gone':r.leak==='both'?'still leaking':'',r.leak==='new'?'v-worse':r.leak==='gone'?'v-better':'muted'));
if(r.shifted&&r.shifted.length)tr.title='Attribution moved between lines of this function; its total did not change.';t.append(tr);}return t;}
function compareView(){const sel=$('baseline'),body=$('compareBody');sel.replaceChildren();body.replaceChildren();if(!data)return;
$('saveBaseline').hidden=data.baselines==null;const none=el('option','Choose a baseline…');none.value='';sel.append(none);
for(const b of data.baselines||[]){const o=el('option',b.name+' · '+new Date(b.mtime).toLocaleString());o.value=b.name;sel.append(o);}
const cw=data.compareWith;if(cw&&!(data.baselines||[]).some(b=>b.name===cw.label)){const o=el('option',cw.label);o.value='#file';sel.append(o);}
sel.value=cw?((data.baselines||[]).some(b=>b.name===cw.label)?cw.label:'#file'):'';
if(data.compareError){body.append(el('p',data.compareError,'badge'));return;}
const cmp=data.comparison;if(!cmp){body.append(el('p',(data.baselines||[]).length?'Choose a baseline to compare this run with.':'No baselines yet. Save this run as a baseline, then change your code and profile again.','muted'));return;}
const head=el('div',null,'cmpHead'),bm=cmp.baseline.meta,bc=el('div',null,'card');
bc.append(el('div','Baseline','muted'),el('strong',bm?bm.name:cw.label),el('p',[bm?'saved '+new Date(bm.saved_at).toLocaleString():'',bm&&bm.git_commit?'commit '+bm.git_commit.slice(0,10)+(bm.git_dirty?' with uncommitted changes':''):'','Python '+cmp.baseline.python,cmp.baseline.mode+' mode',cmp.baseline.wall.toFixed(2)+' s'].filter(Boolean).join(' · '),'muted'));
const cc=el('div',null,'card');cc.append(el('div','This run','muted'),el('strong',shortPath(cmp.current.script)),el('p',['Python '+cmp.current.python,cmp.current.mode+' mode',cmp.current.wall.toFixed(2)+' s'].join(' · '),'muted'));
head.append(bc,cc);body.append(head);
for(const w of cmp.warnings)body.append(el('p','⚠ '+w,'warn'));for(const n of cmp.notes)body.append(el('p',n,'muted'));
const only=$('onlyChanges').checked;
const run=el('table',null,'fn'),rh=el('tr');for(const l of ['Measure','Baseline → this run'])rh.append(el('th',l));run.append(rh);
for(const r of cmp.run){const tr=el('tr');tr.append(el('td',r.label),cell(r.value,r.unit));run.append(tr);}
const rc=el('div',null,'card');rc.append(el('h2','Whole run'),run);body.append(rc);
const fns=cmp.functions.filter(r=>!only||r.significant),fc=el('div',null,'card');
fc.append(el('h2','Functions'),el('p','Generator expressions, lambdas and comprehensions are counted in their enclosing function: samples and allocations move between the two from run to run. "≈ same" means the difference is within run-to-run variation (time: the counting error of the samples behind each value, at least 10%; traced memory: 1 MB or 15%; RSS-based values: 10 MB or 20%). Hover a value for its margin.','muted'));
if(fns.length)fc.append(deltaTable(fns,cmp,'function'));else fc.append(el('p',only?'No function changed beyond run-to-run variation.':'No functions were measured.','muted'));
if(cmp.functionCount>cmp.functions.length)fc.append(el('p','Showing the '+cmp.functions.length+' largest of '+cmp.functionCount+' changes.','muted'));body.append(fc);
if(cmp.sites.length){const sc=el('div',null,'card'),t=el('table',null,'fn'),h=el('tr');for(const l of ['Allocated in','Held at peak snapshot','Held at exit'])h.append(el('th',l));t.append(h);
const sites=cmp.sites.filter(s=>!only||[s.peak,s.exit].some(d=>d&&['better','worse','context'].includes(d.verdict)));
for(const s of sites.slice(0,30)){const tr=el('tr');tr.append(el('td',s.name+(s.file?' · '+shortPath(s.file):'')),s.peak?cell(s.peak,'MB'):el('td','—','num muted'),s.exit?cell(s.exit,'MB'):el('td','—','num muted'));t.append(tr);}
sc.append(el('h2','Memory stacks by allocating function'),el('p','Memory held at each run’s peak and exit snapshots, by the innermost of your functions on each allocation stack (library allocations count in the function that called them).','muted'));
if(sites.length)sc.append(t);else sc.append(el('p','No allocation site changed beyond run-to-run variation.','muted'));body.append(sc);}
const lines=cmp.lines.filter(r=>!only||r.significant),lc=el('div',null,'card');
lc.append(el('h2','Lines'),el('p','Lines match by number when the file is unchanged; otherwise by their function plus the names they assign and call, or their offset from the function’s first line. A line’s change counts only when its function’s total changed too. Unmatched lines: '+cmp.unmatchedLines.baseline+' in the baseline, '+cmp.unmatchedLines.current+' in this run.','muted'));
if(lines.length)lc.append(deltaTable(lines.slice(0,100),cmp,'line'));else lc.append(el('p',only?'No line changed beyond run-to-run variation.':'No lines matched.','muted'));body.append(lc);}
$('baseline').onchange=()=>{const v=$('baseline').value;if(v==='#file')return;api.postMessage({type:'compare',name:v});};
$('compareFile').onclick=()=>api.postMessage({type:'compareFile'});$('saveBaseline').onclick=()=>api.postMessage({type:'saveBaseline'});$('onlyChanges').onchange=compareView;
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(draw,120);});
window.addEventListener('message',({data:m})=>{if(m.type==='clear'){data=null;$('summary').textContent='Profile cleared. Run profiling to generate a new report.';$('summaryCards').replaceChildren();$('timeline').replaceChildren();$('hotspots').replaceChildren();$('cards').replaceChildren();$('graph').replaceChildren();$('details').textContent='';$('openSource').hidden=true;$('fntable').replaceChildren();$('neighbors').replaceChildren();$('crumbs').replaceChildren();$('compareBody').replaceChildren();return;}if(m.type==='neighbors'){neighborsView(m);return;}if(m.type!=='report')return;const changed=!data||data.script!==m.script||data.wall!==m.wall||data.metric!==m.metric||data.thread!==m.thread||data.frames!==m.frames||data.inverted!==m.inverted;data=m;if(changed){focus=0;selected=null;hot=new Set();mark=null;showRows=50;$('neighbors').replaceChildren();$('openSource').hidden=true;$('details').textContent='';}$('summary').textContent=m.script+' · '+m.wall.toFixed(2)+' s run duration · '+m.mode+' memory mode'+(m.notes?.length?' · '+m.notes.join(' '):'')+(m.monitoring?.active?' · line-event coverage':m.monitoring?.requested==='lines'?' · line-event coverage unavailable: '+m.monitoring.reason:'');for(const o of document.querySelectorAll('#metric option.mem'))o.hidden=o.disabled=!m.memoryStacks;$('metric').value=m.metric;$('frames').value=m.frames||'grouped';$('direction').value=m.inverted?'bottom':'top';const thread=$('thread');thread.replaceChildren();const all=el('option','All threads');all.value='';thread.append(all);for(const [id,name]of m.threads){const o=el('option',name+' ('+id+')');o.value=id;thread.append(o);}thread.value=m.thread;thread.disabled=m.unit==='bytes';thread.title=m.unit==='bytes'?'Memory stacks are not recorded per thread':'';overview();memory();draw();fnTable();compareView();});
api.postMessage({type:'ready'});
</script></body></html>`;
}
