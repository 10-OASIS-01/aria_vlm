"use strict";
const $ = (s) => document.querySelector(s);
const base = new URL("./", location.href);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const localURL = (p) => new URL(p, base).href;
const state = {status:null, reportKey:null, runIndices:{}, session:null, busy:false, epoch:0, token:sessionStorage.getItem("aria-token") || ""};
async function api(path, options={}) {
  const headers = {...(options.body && !(options.body instanceof FormData) ? {"Content-Type":"application/json"}:{}), ...options.headers};
  if(state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(localURL(path), {...options, headers});
  const value = await res.json();
  if(!res.ok) throw new Error(typeof value.detail === "string" ? value.detail : `Request failed (${res.status})`);
  return value;
}
let toastTimer;
function toast(message) { $("#toast").textContent=message; $("#toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("#toast").hidden=true,4500); }
function seconds(ms) { return `${(ms/1000).toFixed(2)} s`; }
function renderExperiments(report) {
  if(!report) return;
  $("#experiment-grid").innerHTML=report.tasks.map((run,i)=>{
    const idx=Math.min(state.runIndices[run.task_id] || 0,run.steps.length-1), step=run.steps[idx];
    if(!step) return `<article class="card empty">${esc(run.title)}: no completed requests</article>`;
    return `<article class="card experiment"><img class="experiment-image" src="${esc(localURL(step.preview))}" alt="${esc(run.title)} observation at step ${idx+1}"><div class="experiment-content"><div class="experiment-title"><h3>${esc(run.title)}</h3><span class="badge ${run.success?'ready':'error'}">${run.success?'Passed':'Needs review'}</span></div><p>${esc(run.goal)}</p><div class="run-controls"><span>RENDERED SCENE</span><span style="margin-left:auto">Step ${idx+1} / ${run.steps.length}</span><button data-run="${i}" data-delta="-1" aria-label="Previous observation" ${idx===0?'disabled':''}>←</button><button data-run="${i}" data-delta="1" aria-label="Next observation" ${idx===run.steps.length-1?'disabled':''}>→</button></div><div class="step-instruction">${esc(step.instruction)}</div><div class="step-stats"><span>MODEL ${seconds(step.timings.model_ms)}</span><span>HTTP ${seconds(step.client_roundtrip_ms)}</span><span>${esc(step.evaluation)}</span></div><a class="text-link clip-link" style="display:inline-block;margin-top:14px;font-size:10px" href="${esc(localURL(`api/download/${run.task_id}.mp4`))}">Download the starting clip ↘</a></div></article>`;
  }).join("");
  document.querySelectorAll("[data-run]").forEach(b=>b.onclick=()=>{ const run=report.tasks[Number(b.dataset.run)]; state.runIndices[run.task_id]=(state.runIndices[run.task_id]||0)+Number(b.dataset.delta); renderExperiments(report); });
  const checks=report.checks || [];
  $("#validation-list").innerHTML=checks.map(c=>`<span class="validation-item ${c.passed?'pass':'pending'}">${esc(c.label)}</span>`).join("");
  $("#latency-value").textContent=seconds(report.metrics.median_model_ms);
  $("#latency-caption").textContent=`Median of ${report.metrics.requests} model requests; includes first call`;
  $("#trials-value").innerHTML=`${report.tasks.filter(r=>r.success).length} / ${report.tasks.length} <em>passed</em>`;
  $("#trials-caption").textContent="Rendered scenes · actual Qwen inference";
  $("#scope-text").textContent=report.scope;
}
async function refreshStatus() {
  try {
    const data=await api("api/status"); state.status=data;
    const m=data.model, badge=$("#service-badge");
    badge.textContent=m.state==="ready"?(m.busy?"Model thinking":"Model ready"):(m.state==="loading"?"Model loading":"Model unavailable"); badge.className=`badge ${m.state==="ready"?'ready':m.state==='loading'?'loading':'error'}`;
    $("#device-label").textContent=m.device?`${m.device} · ${m.dtype}`:"Initializing model";
    $("#model-detail").textContent=m.state==="ready"?`${m.model} · resident on ${m.device} · thinking disabled`:`Qwen3.5-9B · ${m.state}`;
    $("#plan-button").disabled=state.busy || m.state!=="ready";
    $("#token-button").hidden=!data.auth_required;
    if(!state.session && !state.busy) $("#trial-status").textContent=m.state==="ready"?"Model ready. Choose a clip to begin.":"Waiting for the model to finish loading…";
    const reportKey=JSON.stringify(data.report);
    if(reportKey!==state.reportKey){ state.reportKey=reportKey; renderExperiments(data.report); }
  } catch(err) { $("#service-badge").textContent="Service offline"; $("#service-badge").className="badge error"; $("#plan-button").disabled=true; }
}
$("#token-button").onclick=()=>{const t=prompt("API token for this browser session",state.token); if(t!==null){state.token=t.trim();sessionStorage.setItem("aria-token",state.token);toast("Token saved for this session");}};
function once(target,event) { return new Promise((resolve,reject)=>{ const onError=()=>{cleanup();reject(new Error("Could not decode this video. Try an H.264 MP4."));}; const onEvent=()=>{cleanup();resolve();}; const cleanup=()=>{target.removeEventListener(event,onEvent);target.removeEventListener("error",onError);}; target.addEventListener(event,onEvent,{once:true});target.addEventListener("error",onError,{once:true}); }); }
async function sampleClip(file, epoch) {
  const video=document.createElement("video"); video.muted=true; video.playsInline=true; video.preload="auto";
  const url=URL.createObjectURL(file);
  try {
    const ready=once(video,"loadedmetadata");video.src=url;await ready;
    if(!Number.isFinite(video.duration)||video.duration<10) throw new Error("Please choose a clip at least 10 seconds long.");
    if(!video.videoWidth||!video.videoHeight) throw new Error("This file has no decodable video track.");
    const scale=Math.min(640/video.videoWidth,640/video.videoHeight,1),canvas=document.createElement("canvas");
    canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
    const context=canvas.getContext("2d"), start=Math.max(0,video.duration-10),frames=[];
    for(let i=0;i<20;i++){
      if(epoch!==state.epoch) throw new Error("Task reset");
      const when=start+i*.5;
      if(Math.abs(video.currentTime-when)<.001 && video.readyState>=2){} else {const seek=once(video,"seeked");video.currentTime=when===0?.001:when;await seek;}
      context.drawImage(video,0,0,canvas.width,canvas.height);
      const image=canvas.toDataURL("image/jpeg",.85);
      frames.push({timestamp_ms:i*500,image_base64:image});
      $("#trial-status").textContent=`Sampling frame ${i+1} / 20…`;
    }
    $("#clip-preview").src=frames[19].image_base64;$("#clip-preview").hidden=false;
    return frames;
  } finally {video.removeAttribute("src");video.load();URL.revokeObjectURL(url);}
}
$("#trial-form").onsubmit=async(e)=>{
  e.preventDefault(); if(state.busy) return;
  const file=$("#clip").files[0],goal=$("#goal").value.trim(); if(!file||goal.length<3)return;
  const epoch=state.epoch;state.busy=true;$("#plan-button").disabled=true;$("#trial-status").classList.remove("error-text");
  try {
    const frames=await sampleClip(file,epoch);
    let current=state.session;
    if(current && current.goal!==goal){await api(`api/sessions/${current.id}/feedback`,{method:"POST",body:JSON.stringify({event:"cancel"})});current=null;}
    if(epoch!==state.epoch) return;
    if(!current){
      current=await api("api/sessions",{method:"POST",body:JSON.stringify({goal,source:"replay",task_id:"uploaded-clip"})});
      if(epoch!==state.epoch){await api(`api/sessions/${current.id}/feedback`,{method:"POST",body:JSON.stringify({event:"cancel"})});return;}
      state.session=current;
    }
    else await api(`api/sessions/${current.id}/feedback`,{method:"POST",body:JSON.stringify({event:"done",text:"I acted on the previous instruction. Here is a new observation."})});
    if(epoch!==state.epoch) return;
    $("#trial-status").textContent="Qwen is examining the observation…";$("#instruction-state").textContent="Thinking";
    const started=performance.now();
    const result=await api(`api/sessions/${current.id}/plan`,{method:"POST",body:JSON.stringify({request_id:crypto.randomUUID?crypto.randomUUID():uuidFallback(),frames})});
    if(epoch!==state.epoch) return;
    $("#instruction-text").textContent=result.instruction;$("#instruction-state").textContent=result.status==="completed"?"Task complete":"Your next step";
    $("#instruction-meta").textContent=`20 frames · Model ${seconds(result.timings.model_ms)} · HTTP ${seconds(performance.now()-started)}`;
    $("#trial-status").textContent=result.status==="completed"?"Goal complete. Reset to start another task.":"Act on this instruction, then upload a new clip.";
    if(result.status==="completed") state.session=null;
    $("#clip").value="";
  } catch(err){if(epoch===state.epoch){$("#trial-status").textContent=err.message;$("#trial-status").classList.add("error-text");$("#instruction-state").textContent="Needs attention";}}
  finally {state.busy=false;$("#plan-button").disabled=state.status?.model.state!=="ready";}
};
function uuidFallback(){const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;const s=Array.from(a,x=>x.toString(16).padStart(2,"0")).join("");return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;}
$("#cancel-button").onclick=async()=>{state.epoch++;const old=state.session;state.session=null;$("#trial-status").textContent="Task reset. Start with a new goal and clip.";$("#instruction-text").textContent="Ready for a new task.";$("#instruction-state").textContent="Waiting for a clip";$("#clip-preview").hidden=true;$("#clip").value="";if(old){try{await api(`api/sessions/${old.id}/feedback`,{method:"POST",body:JSON.stringify({event:"cancel"})});}catch(err){toast(err.message);}}};
const observer=new IntersectionObserver(entries=>{for(const e of entries)if(e.isIntersecting){document.querySelectorAll(".sidebar nav a").forEach(a=>a.classList.toggle("active",a.hash===`#${e.target.id}`));}},{rootMargin:"-15% 0px -60% 0px"});
document.querySelectorAll("main section").forEach(s=>observer.observe(s));
refreshStatus();setInterval(refreshStatus,5000);
