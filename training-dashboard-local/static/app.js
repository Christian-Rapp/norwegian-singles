"use strict";
/* Training Dashboard — local frontend. Vanilla JS, inline SVG, no dependencies. */

const view = document.getElementById("view");
const tip = document.getElementById("tooltip");

/* ================= helpers ================= */
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
const MILE_M=1609.344;
// pace values are s/km throughout; shown per mile
function fmtPace(s){ if(s==null||!isFinite(s)) return "–"; const t=Math.round(s*MILE_M/1000); return Math.floor(t/60)+":"+String(t%60).padStart(2,"0")+"/mi"; }
function fmtDur(s){ if(s==null||!isFinite(s)) return "–"; const t=Math.round(s); const h=Math.floor(t/3600),m=Math.floor(t%3600/60),sec=t%60; const p=n=>String(n).padStart(2,"0"); return h>0?`${h}:${p(m)}:${p(sec)}`:`${m}:${p(sec)}`; }
function fmtClock(s){ if(s==null||!isFinite(s)) return "–"; const t=Math.round(s); return `${Math.floor(t/60)}:${String(t%60).padStart(2,"0")}`; }
function fmtMi(m){ if(m==null||!isFinite(m)) return "–"; return (m/MILE_M).toFixed(2)+" mi"; }
// total distance of a run: miles, except track races in meters
function fmtRunDist(m,trackRace){ return trackRace?fmtM(m):fmtMi(m); }
const isTrackRace=a=>a.classification==="race"&&!!a.track_detected;
function fmtM(m){ if(m==null||!isFinite(m)) return "–"; return Math.round(m)+" m"; }
// watch timer time, plus elapsed when pauses made a real difference
function fmtRunTime(a){ const mv=a.timer_s||a.elapsed_s;
  return fmtDur(mv)+(a.elapsed_s-mv>=60?` (${fmtDur(a.elapsed_s)} elapsed)`:""); }
function fmtInt(v){ return v==null||!isFinite(v) ? "–" : String(Math.round(v)); }
const MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
function fmtDate(u){ if(!u) return "–"; const d=new Date(u*1000); return `${MONTHS[d.getMonth()]} ${d.getDate()}`; }
function fmtDT(u){ if(!u) return "–"; const d=new Date(u*1000); return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; }
function mondayOfISOWeek(y,w){ const jan4=new Date(y,0,4); const back=(jan4.getDay()+6)%7; const d=new Date(y,0,4-back+(w-1)*7); d.setHours(0,0,0,0); return d; }

const CLS={
  threshold:{label:"Threshold",color:"#e8590c"},
  medium:{label:"Medium-hard",color:"#1098ad"},
  speed:{label:"Speed workout",color:"#7950f2"},
  easy:{label:"Easy",color:"#2f9e44"},
  long_run:{label:"Long run",color:"#1971c2"},
  race:{label:"Race",color:"#a61e4d"},
  other:{label:"Other",color:"#868e96"}
};
function clsBadge(c){ const m=CLS[c]||CLS.other; return `<span class="badge" style="background:${m.color}">${esc(m.label)}</span>`; }
function clsColor(c){ return (CLS[c]||CLS.other).color; }
function errHtml(e){ return `<div class="error">Couldn't load data: ${esc(e.message||e)}</div>`; }
function emptyHtml(title,sub){ return `<div class="empty"><h3 style="margin:0 0 6px">${esc(title)}</h3><div>${esc(sub)}</div></div>`; }

/* ================= api ================= */
async function jget(url){ const r=await fetch(url); if(!r.ok) throw new Error(`GET ${url} → ${r.status}`); return r.json(); }
async function jpost(url,body){ const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}); const j=await r.json().catch(()=>({})); if(!r.ok) throw new Error(j.error||`POST ${url} → ${r.status}`); return j; }

/* ================= tooltip ================= */
function showTip(html,x,y){ tip.innerHTML=html; tip.hidden=false;
  tip.style.left=Math.min(x+14,window.innerWidth-280)+"px";
  tip.style.top=Math.min(y+14,window.innerHeight-140)+"px"; }
function hideTip(){ tip.hidden=true; }
function wirePoints(root,pts,tipFn,clickFn){
  root.querySelectorAll(".pt").forEach(el=>{
    const p=pts[+el.dataset.i]; if(!p) return;
    el.addEventListener("mousemove",ev=>showTip(tipFn(p),ev.clientX,ev.clientY));
    el.addEventListener("mouseleave",hideTip);
    if(clickFn){ el.addEventListener("click",ev=>{ev.stopPropagation();hideTip();clickFn(p);}); }
  });
}

/* ================= svg chart pieces ================= */
// ticks for s/km values placed on round per-mile paces
function paceTicks(lo,hi,n){ const k=MILE_M/1000; return niceTicks(lo*k,hi*k,n).map(v=>v/k); }
function niceTicks(lo,hi,n){
  n=n||5; if(!(hi>lo)) return [lo];
  const raw=(hi-lo)/n, mag=Math.pow(10,Math.floor(Math.log10(raw))), norm=raw/mag;
  const step=(norm<1.5?1:norm<3.5?2:norm<7.5?5:10)*mag;
  const out=[]; for(let v=Math.ceil(lo/step)*step; v<=hi+1e-9; v+=step) out.push(+v.toFixed(6));
  return out.length?out:[lo];
}
// rep length → color on a log scale: 400 m red through to 2 mi blue
const REP_M_LO=400, REP_M_HI=2*1609.344;
function repLenColor(m){
  if(m==null||!(m>0)) return "#adb5bd";
  let t=Math.log(m/REP_M_LO)/Math.log(REP_M_HI/REP_M_LO); t=Math.max(0,Math.min(1,t));
  return `hsl(${Math.round(t*215)},75%,${Math.round(48-t*6)}%)`;
}
/* Scatter of pace-over-time. Y inverted: faster is up. No connecting lines — no interpolation across gaps. */
function paceScatterSVG(o){
  const W=o.W||780,H=o.H||360, ml=58,mr=o.showHR?54:14,mt=16,mb=38;
  const iw=W-ml-mr, ih=H-mt-mb;
  const pts=o.series;
  let x0=Math.min(...pts.map(p=>p.x)), x1=Math.max(...pts.map(p=>p.x));
  if(x0===x1){ x0-=86400; x1+=86400; }
  const xp=(x1-x0)*0.04; x0-=xp; x1+=xp;
  let y0=Math.min(...pts.map(p=>p.y))-(o.yPad||8), y1=Math.max(...pts.map(p=>p.y))+(o.yPad||8);
  const X=x=>ml+(x-x0)/(x1-x0)*iw, Y=y=>mt+(y-y0)/(y1-y0)*ih;
  let s=`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  for(const t of (o.yTicks||niceTicks)(y0,y1,6)){ const y=Y(t);
    s+=`<line class="grid" x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}"/><text x="${ml-8}" y="${y+3.5}" text-anchor="end">${esc(o.yFmt?o.yFmt(t):t)}</text>`; }
  const spanDays=(x1-x0)/86400, stepD=Math.max(1,Math.ceil(spanDays/6));
  for(let dx=Math.ceil(x0/86400)*86400; dx<=x1; dx+=stepD*86400){ const x=X(dx);
    s+=`<line class="grid" x1="${x}" y1="${mt}" x2="${x}" y2="${H-mb}"/><text x="${x}" y="${H-mb+18}" text-anchor="middle">${esc(fmtDate(dx))}</text>`; }
  s+=`<line class="axis" x1="${ml}" y1="${mt}" x2="${ml}" y2="${H-mb}"/><line class="axis" x1="${ml}" y1="${H-mb}" x2="${W-mr}" y2="${H-mb}"/>`;
  // race markers
  for(const r of (o.races||[])){ if(r.start_time<x0||r.start_time>x1) continue; const x=X(r.start_time);
    s+=`<line x1="${x}" y1="${mt}" x2="${x}" y2="${H-mb}" stroke="#a61e4d" stroke-width="1.5" stroke-dasharray="5 3" opacity="0.7"/><text x="${x+4}" y="${mt+12}" fill="#a61e4d" font-weight="700">R</text>`; }
  // HR secondary axis
  if(o.showHR){ const hrs=pts.map(p=>p.hr).filter(h=>h!=null&&isFinite(h));
    if(hrs.length){ const h0=Math.min(...hrs)-5,h1=Math.max(...hrs)+5;
      const HY=h=>mt+(h-h0)/(h1-h0)*ih;
      const d=pts.filter(p=>p.hr!=null).map(p=>`${X(p.x).toFixed(1)},${HY(p.hr).toFixed(1)}`).join(" ");
      s+=`<polyline points="${d}" fill="none" stroke="#e03131" stroke-width="1.5" opacity="0.55"/>`;
      for(const t of niceTicks(h0,h1,5)){ const y=HY(t);
        s+=`<text x="${W-mr+8}" y="${y+3.5}" fill="#e03131">${Math.round(t)}</text>`; }
      s+=`<text x="${W-6}" y="${mt-2}" text-anchor="end" fill="#e03131" font-size="10">rep HR bpm</text>`;
    } }
  // points
  pts.forEach((p,i)=>{ const cx=X(p.x).toFixed(1), cy=Y(p.y).toFixed(1), c=o.colorFn?o.colorFn(p):"#1971c2";
    if(p.track){ const r=6;
      s+=`<polygon class="pt" data-i="${i}" points="${cx},${cy-r} ${+cx+r},${cy} ${cx},${+cy+r} ${cx-r},${cy}" fill="${c}" stroke="#212529" stroke-width="1.5"/>`;
    } else {
      s+=`<circle class="pt" data-i="${i}" cx="${cx}" cy="${cy}" r="5.5" fill="${c}" stroke="#fff" stroke-width="1.5"/>`;
    } });
  s+=`<text x="${ml}" y="${mt-4}" font-size="10">${esc(o.yLabel||"")}</text></svg>`;
  return s;
}
function barChartSVG(o){
  // o: {labels:[], values:[], color, yFmt, W,H, band?:{lo,hi,color}, line?:{y,color,label}}
  const W=o.W||780,H=o.H||260, ml=48,mr=14,mt=16,mb=44;
  const iw=W-ml-mr, ih=H-mt-mb, n=o.labels.length;
  let y1=Math.max(1,...o.values.filter(v=>v!=null));
  if(o.ymax) y1=o.ymax;
  const Y=v=>mt+(1-v/y1)*ih;
  let s=`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  if(o.band){ const yA=Y(o.band.hi), yB=Y(o.band.lo);
    s+=`<rect x="${ml}" y="${yA}" width="${iw}" height="${yB-yA}" fill="${o.band.color}" opacity="0.3"/>`; }
  for(const t of niceTicks(0,y1,5)){ const y=Y(t);
    s+=`<line class="grid" x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}"/><text x="${ml-8}" y="${y+3.5}" text-anchor="end">${esc(o.yFmt?o.yFmt(t):t)}</text>`; }
  if(o.line){ const y=Y(o.line.y);
    s+=`<line x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}" stroke="${o.line.color}" stroke-width="1.5" stroke-dasharray="5 3"/><text x="${W-mr}" y="${y-5}" text-anchor="end" fill="${o.line.color}" font-size="10">${esc(o.line.label||"")}</text>`; }
  const bw=Math.min(46, iw/n*0.55);
  o.labels.forEach((lb,i)=>{ const v=o.values[i]; if(v==null) return;
    const x=ml+iw*(i+0.5)/n-bw/2, y=Y(v);
    s+=`<rect class="pt" data-i="${i}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(mt+ih-y).toFixed(1)}" rx="3" fill="${o.color}"/>`;
    if(n<=16) s+=`<text x="${(x+bw/2).toFixed(1)}" y="${H-mb+16}" text-anchor="middle">${esc(lb)}</text>`; });
  s+=`<line class="axis" x1="${ml}" y1="${mt}" x2="${ml}" y2="${H-mb}"/><line class="axis" x1="${ml}" y1="${H-mb}" x2="${W-mr}" y2="${H-mb}"/></svg>`;
  return s;
}

/* ================= DASHBOARD ================= */
let selWeek=null;
// At-a-glance day strip text: ["10x800m", "10.1 mi total"], ["6.2 mi easy", ""]
function daySummary(a){
  const mi=((a.distance_m||0)/MILE_M).toFixed(1)+" mi";
  switch(a.classification){
    case "threshold": return [a.template_label||"Threshold", mi+" total"];
    case "race": return [fmtRunDist(a.distance_m,isTrackRace(a))+" race", fmtClock(a.elapsed_s)];
    case "long_run": return [mi+" long run", ""];
    case "easy": return [mi+" easy", ""];
    default: return [mi+" "+((CLS[a.classification]||CLS.other).label||"").toLowerCase(), ""];
  }
}
async function viewDashboard(){
  let ov; try{ ov=await jget("/api/overview"); }catch(e){ view.innerHTML=errHtml(e); return; }
  if(!ov.weeks.length){ view.innerHTML=emptyHtml("No training data yet","Upload a .fit, .gpx, .tcx or .zip file to get started."); return; }
  const weeks=[...ov.weeks].sort((a,b)=>a.week<b.week?-1:1);
  if(!selWeek||!weeks.some(w=>w.week===selWeek)) selWeek=weeks[weeks.length-1].week;
  const idx=weeks.findIndex(w=>w.week===selWeek), wk=weeks[idx];
  let weekRuns; try{ weekRuns=(await jget("/api/week/"+encodeURIComponent(selWeek))).runs; }catch(e){ view.innerHTML=errHtml(e); return; }
  const m=/^(\d{4})-W(\d{2})$/.exec(wk.week);
  const mon=m?mondayOfISOWeek(+m[1],+m[2]):new Date();
  const days=[]; for(let i=0;i<7;i++){ const d=new Date(mon); d.setDate(d.getDate()+i); days.push(d); }
  const today=new Date(); today.setHours(0,0,0,0);
  const dayRuns=days.map(d=>{ const t0=new Date(d); t0.setHours(0,0,0,0); const t1=new Date(t0.getTime()+86400000);
    return weekRuns.filter(a=>a.start_time>=t0.getTime()/1000&&a.start_time<t1.getTime()/1000).sort((a,b)=>a.start_time-b.start_time); });

  const share=wk.share_pct||0;
  const sharePos=Math.max(0,Math.min(100,share/40*100));
  const qTarget=(wk.q_sessions>=2&&wk.q_sessions<=3)?"on target":"target 2–3";

  view.innerHTML=`
  <div class="weeknav">
    <button class="btn ghost small" id="wprev" ${idx<=0?"disabled":""}>← Prev</button>
    <select id="wsel">${weeks.map(w=>`<option value="${esc(w.week)}" ${w.week===selWeek?"selected":""}>Week ${esc(w.week.slice(6))} · ${esc(w.week.slice(0,4))}</option>`).join("")}</select>
    <button class="btn ghost small" id="wnext" ${idx>=weeks.length-1?"disabled":""}>Next →</button>
    <span class="muted small">${esc(fmtDate(mon.getTime()/1000))} – ${esc(fmtDate(days[6].getTime()/1000))}</span>
  </div>

  <div class="daystrip">${days.map((d,i)=>{
    const isToday=new Date(d).setHours(0,0,0,0)===today.getTime();
    const runs=dayRuns[i].map(a=>{
      const [head,sub]=daySummary(a);
      return `<div class="run" style="border-color:${clsColor(a.classification)}"><a href="#workout/${a.id}" title="${esc(a.name||"Run")}">${esc(head)}</a>${sub?`<div class="stat">${esc(sub)}</div>`:""}</div>`;
    }).join("");
    return `<div class="day${isToday?" today":""}"><div class="dow">${DOW[i]}</div><div class="dnum">${d.getDate()}</div>${runs}</div>`;
  }).join("")}</div>

  <div class="totals">
    <div class="total"><div class="k">Total time</div><div class="v">${esc(fmtDur((wk.total_min||0)*60))}</div></div>
    <div class="total"><div class="k">Quality</div><div class="v">${esc(fmtDur((wk.quality_min||0)*60))}</div></div>
    <div class="total"><div class="k">Threshold share</div><div class="v">${share.toFixed(0)}%</div></div>
    <div class="total"><div class="k">Q sessions</div><div class="v">${wk.q_sessions||0} <span class="small muted">(${qTarget})</span></div></div>
    <div class="total"><div class="k">Distance</div><div class="v">${((wk.km||0)*1000/MILE_M).toFixed(1)} <span class="small muted">mi</span></div></div>
  </div>

  <div class="card">
    <h2>Threshold share — ${share.toFixed(1)}%</h2>
    <div class="sharebar"><div class="band"></div><div class="caution"></div><div class="marker" style="left:${sharePos}%"></div></div>
    <div class="shareaxis"><span style="left:0%">0</span><span style="left:25%">10</span><span style="left:50%">20</span><span style="left:62.5%">25</span><span style="left:75%">30</span><span style="left:100%">40%</span></div>
    <div class="sharelegend">
      <span><span class="swatch" style="background:#b2d8b2"></span>target 20–25%</span>
      <span><span class="swatch" style="background:#e03131"></span>caution at 30%</span>
      <span><span class="swatch" style="background:#d6246e"></span>this week</span>
    </div>
  </div>

  <div class="card">
    <h2>Workouts this week</h2>
    <table><thead><tr><th>Date</th><th>Session</th><th>Type</th><th class="num">Dist</th><th class="num">Time</th><th class="num">Pace</th><th class="num">HR</th><th class="num">Q min</th></tr></thead>
    <tbody>${weekRuns.map(a=>`<tr class="clickable" data-id="${a.id}">
      <td>${esc(fmtDate(a.start_time))}</td><td><strong>${esc(a.name||"Run")}</strong>${a.track_detected?' <span class="badge track">track</span>':""}<div class="small muted">${a.classification==="threshold"?esc(a.template_label||""):""}</div></td>
      <td>${clsBadge(a.classification)}</td>
      <td class="num">${esc(fmtRunDist(a.distance_m,isTrackRace(a)))}</td><td class="num">${esc(fmtDur(a.timer_s||a.elapsed_s))}</td>
      <td class="num">${esc(fmtPace(a.avg_pace))}</td><td class="num">${a.avg_hr?Math.round(a.avg_hr):"–"}</td>
      <td class="num">${a.classification==="threshold"&&a.quality_minutes?Math.round(a.quality_minutes):"–"}</td></tr>`).join("")}</tbody></table>
    ${weekRuns.length?"":'<p class="muted">No runs this week.</p>'}
  </div>`;

  document.getElementById("wprev").onclick=()=>{ if(idx>0){ selWeek=weeks[idx-1].week; viewDashboard(); } };
  document.getElementById("wnext").onclick=()=>{ if(idx<weeks.length-1){ selWeek=weeks[idx+1].week; viewDashboard(); } };
  document.getElementById("wsel").onchange=e=>{ selWeek=e.target.value; viewDashboard(); };
  view.querySelectorAll("tr.clickable").forEach(tr=>tr.onclick=()=>location.hash="#workout/"+tr.dataset.id);
}

/* ================= WORKOUT DETAIL ================= */
const VERDICT={
  in_zone:{cls:"in_zone",text:"Executed in zone."},
  drifted_hot:{cls:"drifted_hot",text:"Drifted hot — consider easing the next one."},
  overcooked:{cls:"overcooked",text:"Too hard — this cost more than it gave."}
};
async function viewWorkout(id){
  let data; try{ data=await jget("/api/activity/"+id); }catch(e){ view.innerHTML=errHtml(e); return; }
  const a=data.activity, bouts=data.bouts||[];
  const v=VERDICT[a.verdict];
  const tracked=bouts.some(b=>b.nominal_m);
  const hrReps=bouts.filter(b=>b.avg_hr!=null);
  // verdict, HR-per-rep and reps only mean something for a workout
  const isWorkout=a.classification==="threshold";
  // a race gets the same execution card as the Races page
  let raceHtml="";
  if(a.classification==="race"){
    try{
      const [lt,st]=await Promise.all([jget("/api/lt"),jget("/api/settings")]);
      const rp=(lt.race_execution||[]).find(r=>r.activity_id===a.id);
      if(rp) raceHtml=`<div class="card racecard">
        <div class="row spread"><h2 style="margin:0">Race execution</h2><div>${raceQualifiers(rp)}</div></div>
        ${raceExecutionHtml(rp,st)}
        <p class="small"><a href="#races">All races →</a></p></div>`;
    }catch(e){ /* fall back to the plain race summary */ }
  }

  // HR execution gauge
  let gauge="";
  if(!hrReps.length){
    gauge=`<div class="verdict none">No heart-rate data recorded for this session.</div>`;
  } else {
    let s; try{ s=await jget("/api/settings"); }catch(e){ s={lthr:null}; }
    const lthr=s.lthr;
    const W=560,H=240, ml=44,mr=14,mt=14,mb=30, iw=W-ml-mr, ih=H-mt-mb;
    const hrs=hrReps.flatMap(b=>b.max_hr!=null?[b.avg_hr,b.max_hr]:[b.avg_hr]);
    const hasMax=hrReps.some(b=>b.max_hr!=null);
    let y0=Math.min(...hrs,lthr||Infinity)-6, y1=Math.max(...hrs,lthr||-Infinity)+6;
    if(!isFinite(y0)){ y0=Math.min(...hrs)-6; y1=Math.max(...hrs)+6; }
    const X=i=>ml+iw*(hrReps.length===1?0.5:i/(hrReps.length-1));
    const Y=h=>mt+(1-(h-y0)/(y1-y0))*ih;
    let g=`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
    for(const t of niceTicks(y0,y1,5)){ const y=Y(t);
      g+=`<line class="grid" x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}"/><text x="${ml-6}" y="${y+3.5}" text-anchor="end">${Math.round(t)}</text>`; }
    hrReps.forEach((b,i)=>{ const x=X(i);
      g+=`<text x="${x}" y="${H-mb+16}" text-anchor="middle">R${b.idx+1}</text>`; });
    if(lthr){ const y=Y(lthr);
      g+=`<line x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}" stroke="#e03131" stroke-width="1.5" stroke-dasharray="6 3"/><text x="${W-mr}" y="${y-6}" text-anchor="end" fill="#e03131" font-weight="700">LTHR ${lthr}</text>`; }
    const tip=b=>`Rep ${b.idx+1}: avg ${Math.round(b.avg_hr)}${b.max_hr!=null?` · max ${Math.round(b.max_hr)}`:""} bpm · ${fmtPace(b.pace_s_per_km)}`;
    // avg→max spread per rep
    hrReps.forEach((b,i)=>{ if(b.max_hr!=null) g+=`<line x1="${X(i).toFixed(1)}" y1="${Y(b.avg_hr).toFixed(1)}" x2="${X(i).toFixed(1)}" y2="${Y(b.max_hr).toFixed(1)}" stroke="#adb5bd" stroke-width="2"/>`; });
    const line=(key,color)=>{ const ps=hrReps.filter(b=>b[key]!=null).map(b=>`${X(hrReps.indexOf(b)).toFixed(1)},${Y(b[key]).toFixed(1)}`).join(" ");
      return `<polyline points="${ps}" fill="none" stroke="${color}" stroke-width="2"/>`; };
    if(hasMax) g+=line("max_hr","#f08c00");
    g+=line("avg_hr","#1971c2");
    hrReps.forEach((b,i)=>{
      if(b.max_hr!=null) g+=`<circle cx="${X(i).toFixed(1)}" cy="${Y(b.max_hr).toFixed(1)}" r="4.5" fill="#f08c00" stroke="#fff" stroke-width="1.5"><title>${tip(b)}</title></circle>`;
      g+=`<circle cx="${X(i).toFixed(1)}" cy="${Y(b.avg_hr).toFixed(1)}" r="4.5" fill="#1971c2" stroke="#fff" stroke-width="1.5"><title>${tip(b)}</title></circle>`; });
    g+=`</svg>`;
    if(hasMax) g+=`<div class="legend"><span><span class="swatch" style="background:#1971c2"></span>avg HR</span><span><span class="swatch" style="background:#f08c00"></span>max HR</span></div>`;
    gauge=g+(lthr?"":`<p class="note"><a href="#settings">Set your LTHR in Settings</a> to draw the threshold line on this gauge.</p>`);
  }

  const verdictBanner=v
    ? `<div class="verdict ${v.cls}">${esc(v.text)}</div>`
    : `<div class="verdict none">No verdict — ${a.classification==="threshold"?"needs LTHR set and 3+ reps with HR.":"not a threshold session."}</div>`;

  let ctxLine="";
  if(a.classification==="easy"&&a.easy_discipline!=null)
    ctxLine=`<p><strong>Easy discipline:</strong> ${a.easy_discipline.toFixed(0)}% of the run under the easy cap.</p>`;
  if(a.classification==="long_run")
    ctxLine=`<p><strong>Long run:</strong> ${esc(fmtDur(a.timer_s||a.elapsed_s))} · ${esc(fmtMi(a.distance_m))}.</p>`;
  if(a.classification==="race")
    ctxLine=`<p><strong>Race:</strong> ${esc(fmtClock(a.elapsed_s))} for ${esc(fmtRunDist(a.distance_m,isTrackRace(a)))} — ${esc(fmtPace(a.avg_pace))}. Avg HR ${a.avg_hr?Math.round(a.avg_hr):"–"}${a.max_hr?`, max ${Math.round(a.max_hr)}`:""}.</p>`;
  if(a.hr_drift!=null)
    ctxLine+=`<p><strong>HR drift:</strong> ${a.hr_drift>=0?"+":""}${a.hr_drift.toFixed(0)} bpm (last third of reps vs first third).</p>`;

  view.innerHTML=`
  <p><a href="#dashboard">← Dashboard</a></p>
  <div class="card">
    <div class="row spread">
      <div><h2 style="margin:0">${esc(a.name||"Run")}</h2>
      <div class="muted small">${esc(fmtDT(a.start_time))} · ${esc(fmtRunDist(a.distance_m,isTrackRace(a)))} · ${esc(fmtRunTime(a))} · avg ${esc(fmtPace(a.avg_pace))}${a.avg_hr?` · HR ${Math.round(a.avg_hr)}`:""}</div></div>
      <div class="row">${clsBadge(a.classification)}${a.track_detected?'<span class="badge track">track</span>':""}</div>
    </div>
  </div>
  ${isWorkout?`${verdictBanner}
  <div class="card">
    <h2>HR execution <span class="muted small">avg &amp; max HR per rep</span></h2>
    ${gauge}
    ${ctxLine}
  </div>
  <div class="card">
    <h2>Reps</h2>
    ${bouts.length?`<table><thead><tr><th>Rep</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Pace</th><th class="num">Avg HR</th><th class="num">Max HR</th></tr></thead><tbody>
    ${bouts.map(b=>`<tr><td>${b.idx+1}</td>
      <td class="num">${b.nominal_m?`<strong>${esc(fmtM(b.nominal_m))}</strong>*`:`<span title="GPS read ${esc(fmtM(b.distance_m))}">${esc(fmtM(b.distance_m))}</span>`}</td>
      <td class="num">${esc(fmtDur(b.duration_s))}</td><td class="num">${esc(fmtPace(b.pace_s_per_km))}</td>
      <td class="num">${b.avg_hr!=null?Math.round(b.avg_hr):"–"}</td><td class="num">${b.max_hr!=null?Math.round(b.max_hr):"–"}</td></tr>`).join("")}</tbody></table>
    ${tracked?`<p class="note">* Pace from nominal track distance — hover for the GPS reading. Track-snapped within ~10% of a standard distance.</p>`:""}`
    :`<p class="muted">No reps detected for this session.</p>`}
  </div>`:raceHtml||(ctxLine?`<div class="card">${ctxLine}</div>`:"")}
  ${splitsHtml(data.splits,isTrackRace(a))}
  <div class="card">
    <h2>Correct classification</h2>
    <div class="row">
      <select id="recls">${["threshold","speed","medium","easy","long_run","race","other"].map(c=>`<option value="${c}" ${c===a.classification?"selected":""}>${CLS[c].label}</option>`).join("")}</select>
      <button class="btn small" id="reclsbtn">Save correction</button>
      <span class="muted small" id="reclsmsg"></span>
    </div>
    <p class="note">Manual corrections stick — re-running classification won't overwrite them.</p>
  </div>`;

  document.getElementById("reclsbtn").onclick=async()=>{
    const c=document.getElementById("recls").value, msg=document.getElementById("reclsmsg");
    try{ await jpost("/api/reclassify",{id:+id,classification:c}); msg.textContent="Saved."; viewWorkout(id); }
    catch(e){ msg.textContent="Failed: "+e.message; }
  };
}

/* ================= PROGRESSION ================= */
let progShowHR=false;
async function viewProgression(){
  let p; try{ p=await jget("/api/progression"); }catch(e){ view.innerHTML=errHtml(e); return; }
  const series=Object.values(p.by_template).flat().sort((a,b)=>a.date-b.date);
  if(!series.length){ view.innerHTML=emptyHtml("No threshold sessions yet","Upload more sessions to see trends."); return; }
  const pts=series.map(s=>({x:s.date,y:s.avg_rep_pace,hr:s.avg_rep_hr,track:!!s.track,label:s.label,qmin:s.quality_min,id:s.activity_id,med:s.med_rep_s,medm:s.med_rep_m}));

  const heroSvg=paceScatterSVG({series:pts,races:p.races||[],showHR:progShowHR,colorFn:p=>repLenColor(p.medm),yFmt:fmtPace,yTicks:paceTicks,yLabel:"avg work-rep pace — faster ↑"});

  const weeks=[...p.weeks].sort((a,b)=>a.week<b.week?-1:1);
  const wlabels=weeks.map(w=>"W"+w.week.slice(6));
  const qSvg=weeks.length?barChartSVG({labels:wlabels,values:weeks.map(w=>w.quality_min||0),color:"#e8590c",yFmt:v=>Math.round(v)+"m"}):"";
  const kmSvg=weeks.length?barChartSVG({labels:wlabels,values:weeks.map(w=>(w.km||0)*1000/MILE_M),color:"#1971c2",yFmt:v=>v.toFixed(0)+"mi"}):"";
  const shareSvg=weeks.length?barChartSVG({labels:wlabels,values:weeks.map(w=>w.share_pct),color:"#868e96",ymax:40,yFmt:v=>v+"%",
    band:{lo:20,hi:25,color:"#2f9e44"},line:{y:30,color:"#e03131",label:"caution 30%"}}):"";

  view.innerHTML=`
  <div class="card">
    <div class="row spread">
      <h2 style="margin:0">Rep pace over time <span class="muted small">— pace is the trend signal</span></h2>
      <label class="small"><input type="checkbox" id="hrtoggle" ${progShowHR?"checked":""}> show avg rep HR (right axis)</label>
    </div>
    <div id="hero">${heroSvg}</div>
    <div class="legend">
      <span>rep length: 400 m <span class="swatch gradbar"></span> 2 mi</span>
      <span>◆ track-snapped rep</span>
      <span style="color:#a61e4d;font-weight:700">R race</span>
    </div>
    <p class="note">Points only — never interpolated across gaps. Click a point to open the session.</p>
  </div>
  <div class="grid2">
    <div class="card"><h2>Quality minutes / week</h2>${qSvg||'<p class="muted">No data.</p>'}</div>
    <div class="card"><h2>Distance / week</h2>${kmSvg||'<p class="muted">No data.</p>'}</div>
  </div>
  <div class="card"><h2>Threshold share history</h2>${shareSvg||'<p class="muted">No data.</p>'}
    <div class="sharelegend"><span><span class="swatch" style="background:#2f9e44"></span>target 20–25%</span><span><span class="swatch" style="background:#e03131"></span>caution 30%</span></div>
  </div>`;

  document.getElementById("hrtoggle").onchange=e=>{ progShowHR=e.target.checked; viewProgression(); };
  wirePoints(document.getElementById("hero"),pts,p=>
    `<div class="tt-title">${esc(p.label||"Threshold")} · ${esc(fmtDate(p.x))}</div>
     pace ${esc(fmtPace(p.y))}${p.track?" ◆track":""}<br>
     <span class="mut">rep HR ${p.hr!=null?Math.round(p.hr)+" bpm":"–"} · Q ${p.qmin!=null?Math.round(p.qmin)+" min":"–"} · med rep ${p.medm?esc(splitDist(p.medm,p.track))+" / ":""}${p.med!=null?Math.round(p.med)+"s":"–"}</span>`,
    p=>location.hash="#workout/"+p.id);
}

/* ================= RACES ================= */
function raceDistLabel(rd){
  if(rd==null) return "race";
  if(Math.abs(rd-5000)<50) return "5k";
  if(Math.abs(rd-10000)<100) return "10k";
  if(Math.abs(rd-21097.5)<200) return "half-marathon";
  if(Math.abs(rd-42195)<400) return "marathon";
  return (rd/1000).toFixed(1)+"k";
}
function ltStepChart(dots,steps){
  // dots: [{x,y,tip}], steps: [{x0,x1,y}] — step line never starts before first race
  const W=780,H=340, ml=58,mr=14,mt=16,mb=38, iw=W-ml-mr, ih=H-mt-mb;
  const all=[...dots.map(d=>d.y),...steps.map(s=>s.y)];
  if(!all.length) return "";
  let x0=Math.min(...dots.map(d=>d.x),...steps.map(s=>s.x0)), x1=Math.max(...dots.map(d=>d.x),...steps.map(s=>s.x1));
  if(x0===x1){x0-=86400;x1+=86400;}
  const xp=(x1-x0)*0.05; x0-=xp; x1+=xp;
  let y0=Math.min(...all)-8, y1=Math.max(...all)+8;
  const X=x=>ml+(x-x0)/(x1-x0)*iw, Y=y=>mt+(y-y0)/(y1-y0)*ih;
  let s=`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">`;
  for(const t of paceTicks(y0,y1,6)){ const y=Y(t);
    s+=`<line class="grid" x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}"/><text x="${ml-8}" y="${y+3.5}" text-anchor="end">${esc(fmtPace(t))}</text>`; }
  const spanDays=(x1-x0)/86400, stepD=Math.max(1,Math.ceil(spanDays/6));
  for(let dx=Math.ceil(x0/86400)*86400; dx<=x1; dx+=stepD*86400){ const x=X(dx);
    s+=`<text x="${x}" y="${H-mb+18}" text-anchor="middle">${esc(fmtDate(dx))}</text>`; }
  s+=`<line class="axis" x1="${ml}" y1="${mt}" x2="${ml}" y2="${H-mb}"/><line class="axis" x1="${ml}" y1="${H-mb}" x2="${W-mr}" y2="${H-mb}"/>`;
  for(const st of steps){ const y=Y(st.y).toFixed(1);
    s+=`<line x1="${X(st.x0).toFixed(1)}" y1="${y}" x2="${X(st.x1).toFixed(1)}" y2="${y}" stroke="#a61e4d" stroke-width="2.5"/>`; }
  steps.forEach((st,i)=>{ const y=Y(st.y);
    s+=`<circle cx="${X(st.x0).toFixed(1)}" cy="${y.toFixed(1)}" r="4" fill="#a61e4d"><title>Race-predicted LT from ${esc(st.name||"race")}: ${esc(fmtPace(st.y))}</title></circle>`; });
  dots.forEach((d,i)=>{ s+=`<circle class="pt" data-i="${i}" cx="${X(d.x).toFixed(1)}" cy="${Y(d.y).toFixed(1)}" r="4.5" fill="#1971c2" stroke="#fff" stroke-width="1.5"/>`; });
  s+=`<text x="${ml}" y="${mt-4}" font-size="10">LT pace — faster ↑</text></svg>`;
  return s;
}
// track races in meters; otherwise meters for sub-mile laps (e.g. an 800
// rep), miles for mile-ish and longer splits
function splitDist(m,track){ return track||m<1500?fmtM(m):fmtMi(m); }
function splitsHtml(sp,track){
  if(!sp||!sp.rows||!sp.rows.length) return "";
  const title=sp.source==="watch"?"Watch splits"
    :`Splits <span class="muted small">every ${track?"400 m":"mile"}, from GPS — no watch laps</span>`;
  return `<div class="card"><h2>${title}</h2>
    <table><thead><tr><th>#</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Pace</th><th class="num">Avg HR</th><th class="num">Max HR</th></tr></thead><tbody>
    ${sp.rows.map((r,i)=>`<tr><td>${i+1}</td>
      <td class="num">${esc(splitDist(r.distance_m,track))}</td>
      <td class="num">${esc(fmtClock(r.duration_s))}</td><td class="num">${esc(fmtPace(r.pace_s_per_km))}</td>
      <td class="num">${r.avg_hr!=null?Math.round(r.avg_hr):"–"}</td><td class="num">${r.max_hr!=null?Math.round(r.max_hr):"–"}</td></tr>`).join("")}
    </tbody></table></div>`;
}
/* Race execution: training prediction vs result, race-predicted LT, HR.
   Shared by the Races page and a race's own session page. */
function raceQualifiers(rp){
  return (rp.qualifier==="partial"?'<span class="qual">partial effort — true LT likely faster</span>':"")
    +(rp.qualifier==="no-hr"?'<span class="qual nohr">no HR data</span>':"");
}
function raceExecutionHtml(rp,s){
  const sgn=v=>(v<0?"-":"+")+Math.abs(v);
  let predLine;
  if(rp.predicted_time!=null&&rp.time_s!=null){
    const shown=rp.predicted_time-rp.time_s; // negative = slower than training suggested
    const pct=(100*shown/rp.predicted_time).toFixed(1);
    const dv=rp.vdot-rp.training_vdot;
    predLine=`<p><strong>Training suggested ${esc(fmtClock(rp.predicted_time))}; you ran ${esc(fmtClock(rp.time_s))}</strong> (${sgn(Math.round(shown))}s, ${shown<0?"-":"+"}${Math.abs(pct)}%)</p>
    <p><strong>Race VDOT ${rp.vdot.toFixed(1)}</strong> vs training VDOT ${rp.training_vdot.toFixed(1)} (${sgn(+dv.toFixed(1))})</p>
    <p class="note"><span class="formula">training LT ${esc(fmtPace(rp.training_lt))} as 10k pace → VDOT ${rp.training_vdot.toFixed(1)} → ${(rp.distance_m/MILE_M).toFixed(2)} mi in ${esc(fmtClock(rp.predicted_time))}</span><br>
    <span class="muted">training LT = mean workout-derived LT over ${rp.n_prior} threshold sessions in the 6 weeks before the race.</span></p>`;
  } else {
    predLine=`<p>Race VDOT <strong>${rp.vdot!=null?rp.vdot.toFixed(1):"–"}</strong></p>
    <p class="muted">No prediction — ${esc(rp.note||"not enough pre-race training data")}.</p>`;
  }
  const hrCtx=rp.avg_hr!=null
    ? `<p>Race avg HR <strong>${Math.round(rp.avg_hr)} bpm</strong>${s.lthr?` vs LTHR ${s.lthr} bpm`:""}.</p>`
    : `<p class="muted">No HR data for this race.</p>`;
  let formula="";
  if(rp.lt_method==="factor")
    formula=`<p class="note"><span class="formula">predicted LT = ${raceDistLabel(rp.race_dist)} pace × ${rp.factor}</span> = ${esc(fmtPace(rp.race_pace))} × ${rp.factor} = <strong>${esc(fmtPace(rp.predicted_lt))}</strong></p>`;
  else if(rp.lt_method==="vdot")
    formula=`<p class="note"><span class="formula">predicted LT = 10k-equivalent pace at VDOT ${rp.vdot.toFixed(1)}</span> = <strong>${esc(fmtPace(rp.predicted_lt))}</strong></p>`;
  else if(rp.distance_m<3000)
    formula=`<p class="note">Under 3 km — too speed-biased to update LT.</p>`;
  return predLine+formula+hrCtx;
}
async function viewRaces(){
  let lt,s;
  try{ [lt,s]=await Promise.all([jget("/api/lt"),jget("/api/settings")]); }
  catch(e){ view.innerHTML=errHtml(e); return; }
  const preds=(lt.race_predictions||[]).filter(r=>r.predicted_lt!=null).sort((a,b)=>a.date-b.date);
  const dots=(lt.workout_lt||[]).map(p=>({x:p.date,y:p.lt_pace,tip:
    `<div class="tt-title">${esc(p.label||"Threshold")} · ${esc(fmtDate(p.date))}</div>workout LT ${esc(fmtPace(p.lt_pace))}<br><span class="mut">rep pace ${esc(fmtPace(p.rep_pace))} ÷ ${p.factor!=null?p.factor.toFixed(3):"–"}</span>`}));
  const chartEnd=Date.now()/1000;
  const steps=preds.map((r,i)=>({x0:r.date,x1:i+1<preds.length?preds[i+1].date:chartEnd,y:r.predicted_lt,name:r.name}));
  const chart=(dots.length||steps.length)?ltStepChart(dots,steps):"";

  const adv=lt.advisory;
  const advHtml=adv?`
    <div class="card advisory">
      <h2>Suggested LT pace going forward</h2>
      <div><span class="big">${esc(fmtPace(adv.lt_pace))}</span>
      <span class="muted">from ${esc(adv.from_race)} on ${esc(fmtDate(adv.date))}</span>
      ${adv.qualifier==="partial"?'<span class="qual">partial effort — true LT likely faster</span>':""}
      ${adv.qualifier==="no-hr"?'<span class="qual nohr">no HR data</span>':""}</div>
      <p class="note" style="margin:8px 0 0">Read-only — this changes nothing. It only informs the pace suggestions below.</p>
    </div>`
    :emptyHtml("No race-derived LT yet","Race a standard distance (5k, 10k, half, marathon) to get a suggested LT pace.");

  const exec=[...(lt.race_execution||[])].sort((a,b)=>b.date-a.date);
  const cards=exec.length?exec.map(rp=>`<div class="card racecard">
      <div class="row spread"><div><h2 style="margin:0">${esc(rp.name||"Race")}</h2>
        <div class="muted small">${esc(fmtDT(rp.date))} · ${esc(fmtRunDist(rp.distance_m,rp.track))} in ${esc(fmtClock(rp.time_s))} · ${esc(fmtPace(rp.race_pace))}</div></div>
        <div>${raceQualifiers(rp)}</div></div>
      ${raceExecutionHtml(rp,s)}
      <p class="small"><a href="#workout/${rp.activity_id}">Open session →</a></p>
    </div>`).join(""):emptyHtml("No races yet","Classify a session as a race (or correct one in its detail view) to evaluate execution.");

  view.innerHTML=`
  ${advHtml}
  <div class="card">
    <h2>LT over time <span class="muted small">workout-derived dots + race-predicted step line</span></h2>
    ${chart||'<p class="muted">Not enough data yet.</p>'}
    <div class="legend"><span><span class="swatch" style="background:#1971c2"></span>workout-derived LT</span>
    <span><span class="swatch" style="background:#a61e4d"></span>race-predicted LT (flat between races, never backfilled)</span></div>
    <p class="note">Workout LT = avg rep pace ÷ Bakken duration factor. Race LT = race pace × race-distance factor, or the VDOT 10k-equivalent pace for other distances (races under 3 km don't count). Hover any dot for its formula.</p>
  </div>
  <h2 style="margin:18px 0 10px">Race execution</h2>
  ${cards}`;

  const chartEl=view.querySelector(".card svg");
  if(chartEl&&dots.length) wirePoints(chartEl,dots,d=>d.tip,d=>location.hash="#workout/"+(lt.workout_lt[dots.indexOf(d)]||{}).activity_id);
}

/* ================= SETTINGS ================= */
function bandLabel(keys,i){
  const k=+keys[i];
  if(i===0) return `≤ ${k} s`;
  return `${+keys[i-1]+1}–${k} s`;
}
const RACE_LABELS={"5000":"5 km","10000":"10 km","21097.5":"Half marathon","42195":"Marathon"};
async function viewSettings(){
  let s; try{ s=await jget("/api/settings"); }catch(e){ view.innerHTML=errHtml(e); return; }
  const bfKeys=Object.keys(s.bakken_factors||{}).sort((a,b)=>+a-+b);
  const rfKeys=Object.keys(s.race_lt_factors||{}).sort((a,b)=>+a-+b);
  view.innerHTML=`
  <div class="card">
    <h2>Physiology</h2>
    <div class="frow">
      <div class="fcol"><label class="f" for="f-lthr">Lactate threshold HR (bpm)</label>
        <input id="f-lthr" type="number" min="100" max="220" value="${esc(s.lthr_raw)}" placeholder="unset">
        <div class="hint">Drives workout verdicts and race-effort qualifiers.</div></div>
      <div class="fcol"><label class="f" for="f-maxhr">Max HR (bpm)</label>
        <input id="f-maxhr" type="number" min="150" max="230" value="${esc(s.max_hr_raw)}" placeholder="190">
        <div class="hint">Basis for the auto easy cap.</div></div>
      <div class="fcol"><label class="f" for="f-easycap">Easy cap (bpm)</label>
        <input id="f-easycap" type="number" min="100" max="200" value="${esc(s.easy_cap_raw)}" placeholder="auto">
        <div class="hint">Leave empty for auto = 70% of max HR${s.easy_cap_auto?` (now ${s.easy_cap_hr} bpm)`:""}.</div></div>
    </div>
  </div>
  <div class="card">
    <h2>Bakken Golden-Zone duration factors</h2>
    <p class="note">Estimated LT pace = avg rep pace ÷ factor. Interpolated between bands for in-between rep durations.</p>
    <table><thead><tr><th>Rep duration</th><th>Upper bound (s)</th><th class="num">Factor</th></tr></thead><tbody>
    ${bfKeys.map((k,i)=>`<tr><td>${esc(bandLabel(bfKeys,i))}</td>
      <td><input type="number" data-bf-k value="${esc(k)}" style="width:90px"></td>
      <td class="num"><input type="number" data-bf-v value="${s.bakken_factors[k]}" step="0.001" min="0.8" max="1.3" style="width:90px"></td></tr>`).join("")}
    </tbody></table>
    <div class="about" style="margin-top:12px">
      <strong>About the zones.</strong> These factors come from <a href="https://www.mariusbakken.com/vdot.html" target="_blank" rel="noopener">Marius Bakken's Golden Zone table</a>
      (mariusbakken.com/vdot.html): roughly 45s–1min reps run at threshold pace, 1–3min ~1–3% slower, 4–5min ~3–6% slower, 8–12min ~6–9% slower.
      They are tunable estimates, not physiological facts — adjust them to match your own sessions.
    </div>
  </div>
  <div class="card">
    <h2>Race → LT factors</h2>
    <p class="note">Predicted LT = race pace × factor at these distances. Other distances (3 km and up) convert through VDOT to a 10k-equivalent pace; races under 3 km never set LT. Race-time predictions always use VDOT.</p>
    <table><thead><tr><th>Distance</th><th class="num">Factor</th></tr></thead><tbody>
    ${rfKeys.map(k=>`<tr><td>${esc(RACE_LABELS[k]||(k+" m"))}<input type="hidden" data-rf-k value="${esc(k)}"></td>
      <td class="num"><input type="number" data-rf-v value="${s.race_lt_factors[k]}" step="0.000001" min="0.5" max="1.5" style="width:110px"></td></tr>`).join("")}
    </tbody></table>
  </div>
  <div class="card">
    <div class="row"><button class="btn" id="save-settings">Save settings</button><span class="muted small" id="save-msg"></span></div>
    <p class="note">Saving re-runs classification over all stored sessions. Manual classification corrections are kept.</p>
  </div>
  <div class="card">
    <h2>Strava</h2>
    ${s.strava_connected?`
    <p>✅ Connected${s.strava_athlete?` as <strong>${esc(s.strava_athlete)}</strong>`:""}. <button class="btn ghost small" id="st-disconnect">Disconnect</button></p>
    <div class="frow">
      <div class="fcol"><label class="f" for="st-since">Import since</label>
        <input id="st-since" type="date" value="2026-04-01"></div>
    </div>
    <div class="row" style="margin-top:10px"><button class="btn" id="st-go">Import from Strava</button><span class="muted small" id="st-msg"></span></div>
    <p class="note">Imports new runs and adds laps to earlier ones. Already-imported runs are skipped.</p>`:`
    <div class="frow">
      <div class="fcol"><label class="f" for="st-cid">Client ID</label>
        <input id="st-cid" type="text" autocomplete="off" value="${esc(s.strava_client_id)}"></div>
      <div class="fcol"><label class="f" for="st-secret">Client Secret</label>
        <input id="st-secret" type="password" autocomplete="off" placeholder="${s.strava_has_secret?"saved — leave blank to keep":""}"></div>
    </div>
    <div class="row" style="margin-top:10px"><button class="btn" id="st-connect">♥ Connect Strava</button><span class="muted small" id="st-msg"></span></div>
    <p class="note">One-time setup: at <a href="https://www.strava.com/settings/api" target="_blank" rel="noopener">strava.com/settings/api</a>, set <strong>Authorization Callback Domain</strong> to <code>localhost</code>, then copy the Client ID and Client Secret here. Open this app at <code>http://localhost:8000</code> (not 127.0.0.1) when connecting. Your credentials and tokens stay in this app's local database.</p>`}
  </div>
  <div class="card">
    <h2>Data</h2>
    <p class="note">Uploads are stored locally in this app's data directory. Re-running classification happens automatically whenever settings are saved.</p>
  </div>`;

  document.getElementById("save-settings").onclick=async()=>{
    const msg=document.getElementById("save-msg"); msg.textContent="Saving…";
    const numOrEmpty=id=>{ const v=document.getElementById(id).value.trim(); return v===""?"":parseInt(v,10); };
    const bf={}, rf={};
    let ok=true;
    document.querySelectorAll("[data-bf-k]").forEach((kEl,i)=>{
      const vEl=document.querySelectorAll("[data-bf-v]")[i];
      const k=parseFloat(kEl.value), v=parseFloat(vEl.value);
      if(isFinite(k)&&isFinite(v)&&k>0) bf[String(k)]=v; else ok=false;
    });
    document.querySelectorAll("[data-rf-k]").forEach((kEl,i)=>{
      const vEl=document.querySelectorAll("[data-rf-v]")[i];
      const v=parseFloat(vEl.value);
      if(kEl.value&&isFinite(v)) rf[kEl.value]=v; else ok=false;
    });
    if(!ok){ msg.textContent="Check the factor values — all must be numbers."; return; }
    try{
      await jpost("/api/settings",{lthr:numOrEmpty("f-lthr"),max_hr:numOrEmpty("f-maxhr"),easy_cap_hr:numOrEmpty("f-easycap"),bakken_factors:bf,race_lt_factors:rf});
      msg.textContent="Saved — classification re-run.";
    }catch(e){ msg.textContent="Save failed: "+e.message; }
  };
  const stGo=document.getElementById("st-go");
  if(stGo) stGo.onclick=async()=>{
    const msg=document.getElementById("st-msg"); msg.textContent="Importing…";
    try{
      const r=await jpost("/api/strava/import",{since:document.getElementById("st-since").value||"2026-04-01"});
      msg.textContent=`Imported ${r.imported}, skipped ${r.skipped}.`+(r.laps_added?` Added laps to ${r.laps_added} earlier imports.`:"")+(r.note?" "+r.note:"");
    }catch(e){ msg.textContent="Import failed: "+e.message; }
  };
  const stConnect=document.getElementById("st-connect");
  if(stConnect) stConnect.onclick=async()=>{
    const msg=document.getElementById("st-msg");
    const cid=document.getElementById("st-cid").value.trim(), secret=document.getElementById("st-secret").value.trim();
    if(!cid||(!secret&&!s.strava_has_secret)){ msg.textContent="Enter your Client ID and Client Secret first."; return; }
    try{ await jpost("/api/strava/credentials",{client_id:cid,client_secret:secret}); location.href="/strava/connect"; }
    catch(e){ msg.textContent="Couldn't save: "+e.message; }
  };
  const stDisc=document.getElementById("st-disconnect");
  if(stDisc) stDisc.onclick=async()=>{ await jpost("/api/strava/disconnect",{}); viewSettings(); };
}

/* ================= router / init ================= */
function setTab(name){ document.querySelectorAll("#tabs a").forEach(a=>a.classList.toggle("active",a.dataset.tab===name)); }
function route(){
  hideTip();
  const h=location.hash||"#dashboard";
  const m=/^#([^\/]+)(?:\/(.*))?$/.exec(h);
  const page=m?m[1]:"dashboard", arg=m?m[2]:null;
  window.scrollTo(0,0);
  if(page==="workout"&&arg){ setTab(""); viewWorkout(arg); }
  else if(page==="progression"){ setTab("progression"); viewProgression(); }
  else if(page==="races"){ setTab("races"); viewRaces(); }
  else if(page==="settings"){ setTab("settings"); viewSettings(); }
  else { setTab("dashboard"); viewDashboard(); }
}
window.addEventListener("hashchange",route);
document.getElementById("upload-files").addEventListener("change",async e=>{
  const files=e.target.files; if(!files.length) return;
  const st=document.getElementById("upload-status"); st.textContent="Uploading…";
  const fd=new FormData(); for(const f of files) fd.append("files",f,f.name);
  try{
    const r=await fetch("/api/upload",{method:"POST",body:fd});
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    st.textContent=`Imported ${j.imported}, skipped ${j.skipped}.`;
    selWeek=null; route();
  }catch(err){ st.textContent="Upload failed: "+err.message; }
  e.target.value="";
});
route();
