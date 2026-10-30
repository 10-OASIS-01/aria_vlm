"use strict";
const $ = (s) => document.querySelector(s);
const base = new URL("./", location.href);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const localURL = (p) => new URL(p, base).href;
const state = {status:null, session:null, busy:false, epoch:0, token:sessionStorage.getItem("aria-token") || ""};
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
async function refreshStatus() {
  try {
    const data=await api("api/status"); state.status=data;
    const m=data.model, badge=$("#service-badge");
    badge.textContent=m.state==="ready"?(m.busy?"Model thinking":"Model ready"):(m.state==="loading"?"Model loading":"Model unavailable"); badge.className=`badge ${m.state==="ready"?'ready':m.state==='loading'?'loading':'error'}`;
    $("#plan-button").disabled=state.busy || m.state!=="ready";
    $("#token-button").hidden=!data.auth_required;
    if(!state.session && !state.busy) $("#trial-status").textContent=m.state==="ready"?"Model ready. Choose a clip to begin.":"Waiting for the model to finish loading…";
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
  const epoch=state.epoch;state.busy=true;$("#plan-button").disabled=true;$("#confirm-button").hidden=true;$("#trial-status").classList.remove("error-text");
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
    const proposed=result.status==="done_proposed";
    $("#instruction-text").textContent=result.instruction;$("#instruction-state").textContent=proposed?"Looks complete":result.status==="clarification"?"Qwen needs clarification":"Your next step";
    $("#instruction-meta").textContent=`20 frames · Model ${seconds(result.timings.model_ms)} · HTTP ${seconds(performance.now()-started)}`;
    // Qwen only proposes completion; the person confirms, or uploads a new clip to continue.
    $("#trial-status").textContent=proposed?"Qwen thinks the goal is complete. Confirm, or upload a new clip to continue.":"Act on this instruction, then upload a new clip.";
    $("#confirm-button").hidden=!proposed;
    $("#clip").value="";
  } catch(err){if(epoch===state.epoch){$("#trial-status").textContent=err.message;$("#trial-status").classList.add("error-text");$("#instruction-state").textContent="Needs attention";}}
  finally {state.busy=false;$("#plan-button").disabled=state.status?.model.state!=="ready";}
};
function uuidFallback(){const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;const s=Array.from(a,x=>x.toString(16).padStart(2,"0")).join("");return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;}
$("#confirm-button").onclick=async()=>{const current=state.session;if(!current||state.busy)return;state.busy=true;try{await api(`api/sessions/${current.id}/feedback`,{method:"POST",body:JSON.stringify({event:"confirm"})});state.session=null;$("#confirm-button").hidden=true;$("#instruction-state").textContent="Task complete";$("#trial-status").textContent="Goal complete. Reset to start another task.";}catch(err){toast(err.message);}finally{state.busy=false;}};
$("#cancel-button").onclick=async()=>{state.epoch++;const old=state.session;state.session=null;$("#confirm-button").hidden=true;$("#trial-status").textContent="Task reset. Start with a new goal and clip.";$("#instruction-text").textContent="Ready for a new task.";$("#instruction-state").textContent="Waiting for a clip";$("#clip-preview").hidden=true;$("#clip").value="";if(old){try{await api(`api/sessions/${old.id}/feedback`,{method:"POST",body:JSON.stringify({event:"cancel"})});}catch(err){toast(err.message);}}};
// Live: the operator console. guidance_node owns the task; the backend relays its state and our commands.
const live={state:null,visible:false,talking:false,editingGoal:false};
const HELP_TEXT="I need more help with this step.";
// [label, tone, detail, primary button]; detail null keeps the node's own text.
const TASK_BANDS={
  idle:["No goal yet","off","Type a goal below, or hold Talk and let the wearer say it.",null],
  ready:["Observing","info",null,null],planning:["Thinking","wait","Choosing the next step.",null],
  awaiting_feedback:["Act on the instruction","go","When the wearer has done it, press Done with step. If they are stuck, Need help.","#live-done"],
  awaiting_confirmation:["Finished?","ask","The model thinks the goal is reached. Yes, finish closes the trial; Not yet continues.","#live-yes"],
  completed:["Complete","go","The trial is saved. Set a new goal to start another.",null],
  error:["Didn't work: press Need help","stop",null,"#live-help"],cancelled:["Cancelled","off","The trial is saved. Set a new goal to start another.",null]};
// The node's momentary state wins over the task state; null keeps the task band's value.
const STATE_BANDS={
  recording:["Listening","stop","Release to send.",null],transcribing:["Hearing","wait","Recognizing speech.",null],
  clarification:["Answer the question","ask","The model asked something. Hold Talk so the wearer can answer, or press Need help.","#live-talk"],
  speech_guard:["Not spoken: press Repeat","ask","The previous instruction was still playing.","#live-repeat"],
  stale:["Took too long: press Need help","stop",null,"#live-help"],silence:["Nothing heard","ask","No speech came through. Hold Talk and try again.",null],
  talk_refused:["Can't listen yet","ask",null,null],invalid_goal:["Goal not accepted","stop",null,null],
  image_error:["Camera error","stop",null,null],audio_error:["Microphone error","stop",null,null],
  busy:[null,"wait",null,null],note:[null,null,(s)=>`Note saved: ${s.detail}`,null]};
const CAMERAS={glasses:"Aria glasses",fake:"TEST CAMERA · not the glasses"};
const ACTIVE=(s)=>Boolean(s&&s.node_online&&!["idle","completed","cancelled"].includes(s.task));
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
function cameraOk(s){const c=s&&s.camera;return Boolean(c&&c.source&&c.age_s<=3);}
function band(s){
  if(!s)return ["Console offline","off","The guidance service is not answering. Is scripts/service.sh running?",null];
  if(!s.node_online)return ["Guidance stopped","off","Press Start in the row below.",null];
  if(!cameraOk(s)&&s.task!=="completed"&&s.task!=="cancelled")return ["No camera","stop","Choose Glasses or Test camera in the row below.",null];
  let [label,tone,detail,primary]=TASK_BANDS[s.task]||["Working","info",null,null];
  // The model needs 10 s of camera; count it while waiting, with or without a goal.
  const w=s.camera?.window_s??0;
  if(s.task==="ready")detail=w<9.5?`Needs 10 s of camera: ${w.toFixed(0)} of 10 s. The first step starts by itself.`:"Choosing the next step.";
  if(s.task==="idle"&&w<9.5)detail=`Camera warming up: ${w.toFixed(0)} of 10 s. You can type the goal now.`;
  const over=STATE_BANDS[s.state]||[];
  const overDetail=typeof over[2]==="function"?over[2](s):over[2];
  return [over[0]??label,over[1]??tone,overDetail??detail??s.detail??"",over[3]!==undefined&&over[0]!=null?over[3]:primary];
}
function renderCamera(s){
  const c=s&&s.node_online?s.camera:null,source=c&&c.source;
  const late=source&&c.age_s>1,dead=source&&c.age_s>3;
  const strip=$("#live-camera");strip.dataset.source=source?(CAMERAS[source]?source:"other"):"none";strip.dataset.health=!source?"none":dead?"dead":late?"late":"ok";
  $("#live-camera-source").textContent=source?(CAMERAS[source]||`Camera: ${source}`):"No camera";
  $("#live-camera-rate").textContent=source?`${c.hz.toFixed(1)} Hz · ${late?`last frame ${c.age_s.toFixed(1)} s ago`:"live"}`:"";
}
function renderLive(s){
  live.state=s;
  const online=Boolean(s&&s.node_online),active=ACTIVE(s),confirming=active&&s.task==="awaiting_confirmation";
  const [label,tone,detail,primary]=band(s);
  $("#live-band").dataset.tone=tone;$("#live-label").textContent=label;$("#live-detail").textContent=detail;
  $("#live-node").dataset.online=String(online);$("#live-node-text").textContent=online?"Guidance running":s?"Guidance stopped":"Console offline";
  renderCamera(s);
  $("#live-goal-text").textContent=online&&s.goal?s.goal:"No goal set";
  $("#live-kicker").textContent=s&&s.task==="error"?"Last instruction":"Instruction";
  $("#live-instruction").textContent=online&&s.instruction?s.instruction:"No instruction yet.";
  $("#live-heard").textContent=online&&s.heard?`“${s.heard}”`:"Nothing yet";
  $("#live-trial").innerHTML=online&&s.trial?`<a class="text-link" href="#trials/${encodeURIComponent(s.trial.session)}/${encodeURIComponent(s.trial.id)}">${esc(s.trial.id)} · review in Trials</a>`:"None yet";
  // Acting: Done with step / Need help. When the model proposes completion: Yes, finish / Not yet.
  $("#live-step-actions").hidden=confirming;$("#live-confirm-actions").hidden=!confirming;
  ["#live-done","#live-help","#live-yes","#live-notyet","#live-cancel","#live-finish"].forEach(id=>$(id).disabled=!active);
  $("#live-finish").hidden=confirming;
  $("#live-repeat").disabled=!(active&&s.instruction);$("#live-talk").disabled=!online;
  // The button the band asks for is the dark one.
  document.querySelectorAll("#live .live-controls .button").forEach(b=>{if(b.type==="submit")return;const main=primary?b.matches(primary):false;b.classList.toggle("dark",main);b.classList.toggle("light",!main);});
  $("#live-talk").classList.toggle("asked",primary==="#live-talk");
  $("#live-talk-hint").textContent=active?"The wearer can also speak any time, starting with Aria: done, stop, finish, or anything as feedback. Hold this to record without saying Aria.":"The wearer can also say Aria, trial start, then the goal. Hold this to record without saying Aria; what is said becomes the goal.";
  // While a task runs, the goal form hides behind New goal, so a stray submit cannot replace it.
  const showForm=!active||live.editingGoal;
  $("#live-goal-form").hidden=!showForm;$("#live-newgoal").hidden=!active||live.editingGoal;$("#live-newgoal").setAttribute("aria-expanded",String(showForm));
  $("#live-goal-close").hidden=!(active&&live.editingGoal);
  if(!$("#live-goal-submit").dataset.armed)$("#live-goal-submit").textContent=active?"Replace goal":"Set goal";
  document.querySelectorAll("#live-goal-form button,#live-note-form button,#live-newgoal").forEach(b=>b.disabled=!online);
  $("#live-talk").classList.toggle("on",online&&Boolean(s.recording));
  $("#live-talk-text").textContent=online&&s.recording?"Listening, release to send":"Hold to talk";
  tour.check();
}
async function pollLive(){
  let after=-1;
  for(;;){
    try{const s=await api(`api/live/state?after=${after}&wait=5`);after=s.version;renderLive(s);}
    catch(err){renderLive(null);await sleep(2000);}
  }
}
function pollPreview(){
  const img=$("#live-preview"),next=(ms)=>setTimeout(pollPreview,ms);
  if(!live.visible||document.hidden||!live.state?.node_online){next(1000);return;}
  const probe=new Image();
  probe.onload=()=>{img.src=probe.src;img.hidden=false;$("#live-nopreview").hidden=true;next(150);};
  probe.onerror=()=>{img.hidden=true;$("#live-nopreview").hidden=false;next(1500);};
  probe.src=localURL(`api/live/preview.jpg?t=${Date.now()}`);
}
async function sendLive(kind,text=""){
  try{await api("api/live/command",{method:"POST",body:JSON.stringify({kind,text})});tour.note(kind,text);return true;}
  catch(err){toast(err.message);return false;}
}
// Destructive actions take a second press within 4 s.
function confirmFirst(button,prompt,action){
  if(button.dataset.armed){clearTimeout(Number(button.dataset.armed));delete button.dataset.armed;button.textContent=button.dataset.label;button.classList.remove("armed");action();return;}
  button.dataset.label=button.innerHTML;button.textContent=prompt;button.classList.add("armed");
  button.dataset.armed=String(setTimeout(()=>{delete button.dataset.armed;button.innerHTML=button.dataset.label;button.classList.remove("armed");},4000));
}
$("#live-done").onclick=()=>sendLive("reply","done");
$("#live-help").onclick=()=>sendLive("reply",HELP_TEXT);
$("#live-yes").onclick=()=>sendLive("reply","finish");
$("#live-notyet").onclick=()=>sendLive("reply","Keep going.");
$("#live-repeat").onclick=()=>sendLive("reply","repeat");
$("#live-finish").onclick=()=>confirmFirst($("#live-finish"),"Press again to finish",()=>sendLive("reply","finish"));
$("#live-cancel").onclick=()=>confirmFirst($("#live-cancel"),"Press again to cancel the task",()=>sendLive("reply","cancel"));
$("#live-newgoal").onclick=()=>{live.editingGoal=true;renderLive(live.state);$("#live-goal").focus();};
$("#live-goal-close").onclick=()=>{live.editingGoal=false;renderLive(live.state);};
$("#live-goal").addEventListener("keydown",(e)=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#live-goal-form").requestSubmit();}});
$("#live-goal-form").onsubmit=(e)=>{
  e.preventDefault();const goal=$("#live-goal").value.trim();
  if(goal.length<3){toast("A goal needs at least 3 characters.");return;}
  const submit=async()=>{if(await sendLive("goal",goal)){$("#live-goal").value="";live.editingGoal=false;renderLive(live.state);}};
  if(ACTIVE(live.state))confirmFirst($("#live-goal-submit"),"Press again to replace the goal",submit);else submit();
};
$("#live-note-form").onsubmit=async(e)=>{e.preventDefault();const note=$("#live-note").value.trim();if(note&&await sendLive("note",note))$("#live-note").value="";};
function talk(on){if(live.talking===on||(on&&$("#live-talk").disabled))return;live.talking=on;$("#live-talk").classList.toggle("held",on);sendLive("talk",on?"on":"off");}
const talkButton=$("#live-talk");
talkButton.addEventListener("pointerdown",(e)=>{talkButton.setPointerCapture(e.pointerId);talk(true);});
["pointerup","pointercancel","lostpointercapture"].forEach(type=>talkButton.addEventListener(type,()=>talk(false)));
talkButton.addEventListener("keydown",(e)=>{if((e.key===" "||e.key==="Enter")&&!e.repeat){e.preventDefault();talk(true);}});
talkButton.addEventListener("keyup",(e)=>{if(e.key===" "||e.key==="Enter"){e.preventDefault();talk(false);}});
// D/H/N/R/F, and Space to talk: only in the Live view and only when focus is not on a control,
// so Space still presses a focused button and typing never sends a command.
const SHORTCUTS={d:"#live-done",h:"#live-help",n:"#live-notyet",r:"#live-repeat",f:"#live-yes"};
const ownsKeys=(e)=>live.visible&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.target.closest("button,a,input,textarea,select,video,summary,[role=radio],[contenteditable]");
document.addEventListener("keydown",(e)=>{
  if(!ownsKeys(e))return;
  if(e.key===" "){e.preventDefault();if(!e.repeat)talk(true);return;}
  const key=e.key.toLowerCase();
  if(e.repeat||!SHORTCUTS[key])return;
  // F finishes: at once when the model proposed it, else through Finish task's second press.
  const target=key==="f"&&$("#live-confirm-actions").hidden?$("#live-finish"):$(SHORTCUTS[key]);
  if(target&&!target.disabled&&!target.closest("[hidden]"))target.click();
});
document.addEventListener("keyup",(e)=>{if(e.key===" "&&live.talking&&!e.target.closest("#live-talk"))talk(false);});
window.addEventListener("blur",()=>talk(false));

// System: the backend starts and stops guidance_node and the camera (fixed commands only).
const system={rows:{},dismissed:{}};
const SYS_TEXT={running:"Running",stopping:"Stopping…",stopped:"Stopped",failed:"Failed"};
const CAMERA_HINTS={glasses:"Aria glasses over USB or Wi-Fi, paired once (see Setup). The Rerun viewer stays off.",test_camera:"A still photo standing in for the glasses, for practice. Trials are tagged Test camera.",off:"No camera: nothing can be planned until you choose one."};
// The one line of a log that says what went wrong, without the traceback around it.
const cause=(log)=>(log.slice().reverse().find(l=>/error|not found|failed|no devices|refused/i.test(l))||log.at(-1)||"").replace(/^\w*(Error|Exception):\s*(\(-?\d+\)\s*)?/,"");
function renderSystem(){
  const r=system.rows,g=r.guidance||{state:"stopped"};
  $("#sys-guidance-dot").dataset.state=g.state;$("#sys-guidance-state").textContent=SYS_TEXT[g.state];
  const button=$("#sys-guidance");if(!button.dataset.armed)button.textContent=g.state==="running"?"Stop":"Start";button.disabled=g.state==="stopping";
  const camera=["glasses","test_camera"].find(n=>["running","stopping"].includes(r[n]?.state))||"off",cam=r[camera]||{state:"stopped"};
  document.querySelectorAll("[data-camera]").forEach(b=>{const on=b.dataset.camera===camera;b.setAttribute("aria-checked",String(on));b.tabIndex=on?0:-1;b.dataset.failed=String(r[b.dataset.camera]?.state==="failed");});
  $("#sys-camera-dot").dataset.state=camera==="off"?"stopped":cam.state;
  $("#sys-camera-state").textContent=camera==="off"?"":SYS_TEXT[cam.state];
  $("#sys-camera-hint").textContent=CAMERA_HINTS[camera];
  // A failure shows until dismissed or until another camera is started after it.
  const newest=Math.max(0,...["glasses","test_camera"].map(n=>r[n]?.started_at||0));
  const failed=Object.values(r).filter(x=>x.state==="failed"&&system.dismissed[x.name]!==x.started_at&&!(x.name!=="guidance"&&x.started_at<newest));
  $("#system-errors").hidden=!failed.length;
  $("#system-errors").innerHTML=failed.map(x=>`<div class="system-error"><p><strong>${esc(x.label)} didn't start:</strong> ${esc(cause(x.log))}</p><button type="button" class="text-button" data-dismiss="${esc(x.name)}" data-at="${x.started_at}">Dismiss</button><details><summary>Show log</summary><pre>${esc(x.log.join("\n"))}</pre></details></div>`).join("");
  $("#system-errors").querySelectorAll("[data-dismiss]").forEach(b=>b.onclick=()=>{system.dismissed[b.dataset.dismiss]=Number(b.dataset.at);renderSystem();});
  tour.check();
}
async function systemAction(name,action){
  try{system.rows=Object.fromEntries((await api(`api/system/${name}/${action}`,{method:"POST"})).map(x=>[x.name,x]));renderSystem();}
  catch(err){toast(err.message);}
}
async function loadSystem(){try{system.rows=Object.fromEntries((await api("api/system")).map(x=>[x.name,x]));renderSystem();}catch{}}
$("#sys-guidance").onclick=()=>{
  const running=system.rows.guidance?.state==="running";
  if(!running){systemAction("guidance","start");return;}
  // Stopping the guidance program mid-task ends the trial as aborted.
  if(ACTIVE(live.state))confirmFirst($("#sys-guidance"),"Press again: ends the trial",()=>systemAction("guidance","stop"));
  else systemAction("guidance","stop");
};
const radios=[...document.querySelectorAll("[data-camera]")];
radios.forEach((b,i)=>{
  b.onclick=()=>{const pick=b.dataset.camera,on=["glasses","test_camera"].find(n=>system.rows[n]?.state==="running");if(pick==="off"){if(on)systemAction(on,"stop");}else if(pick!==on)systemAction(pick,"start");};
  // Arrows move focus only: choosing a camera starts a process, so it waits for Enter or Space.
  b.onkeydown=(e)=>{const step={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1}[e.key];if(step){e.preventDefault();radios[(i+step+radios.length)%radios.length].focus();}};
});
setInterval(()=>{if(live.visible)loadSystem();},2000);loadSystem();

// Trials: review of what guidance_node recorded, one folder per goal.
const trials={list:[],current:null,detail:null,events:[]};
const OUTCOMES={completed:["Completed","ready"],cancelled:["Cancelled","subtle"],replaced:["Replaced","subtle"],aborted:["Aborted","error"]};
const OUTCOME_HELP={Completed:"The goal was reached: finished by the operator or confirmed after the model proposed it.",Cancelled:"The operator cancelled the task.",Replaced:"A new goal was set before this one finished.",Aborted:"The guidance program was stopped during the trial.",Interrupted:"The guidance program died without closing the trial.",Running:"Still recording."};
const EVENT_LABELS={goal:"Human",tts:"System",reply:"Human",transcript:"Human",note:"Note",error:"Error",end:"End"};
const trialURL=(t,suffix="")=>`api/trials/${encodeURIComponent(t.session)}/${encodeURIComponent(t.id)}${suffix}`;
const clock=(ms)=>{const s=Math.max(0,Math.round(ms/1000));return `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;};
const when=(ms)=>ms?new Date(ms).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"Unknown time";
// An open trial whose files stopped changing lost its node without closing.
function outcomeBadge(t){const [label,cls]=OUTCOMES[t.outcome]||(t.active?["Running","loading"]:["Interrupted","error"]);return {label,cls};}
function hideTest(){try{return localStorage.getItem("aria-hide-test")==="1";}catch{return false;}}
function renderTrialList(){
  const list=$("#trial-list"),shown=trials.list.filter(t=>!(hideTest()&&t.camera==="fake"));
  const count=(f)=>trials.list.filter(f).length,running=count(t=>!t.outcome&&t.active),test=count(t=>t.camera==="fake");
  const toReview=count(t=>(t.outcome||!t.active)&&!t.verdict&&t.camera!=="fake");
  $("#trials-summary").textContent=trials.list.length?[`${trials.list.length} trial${trials.list.length===1?"":"s"}`,running&&`${running} running`,test&&`${test} on the test camera`,`${toReview} real trial${toReview===1?"":"s"} to review`].filter(Boolean).join(" · "):"Each goal is recorded here: watch it, read the exchange, record a verdict.";
  if(!shown.length&&trials.list.length){list.innerHTML=`<li class="trial-empty">Only test-camera trials so far. Untick “Hide test-camera trials” to see them.</li>`;return;}
  if(!trials.list.length){list.innerHTML=`<li class="trial-empty"><strong>No trials yet.</strong> Start guidance_node and give it a goal; every goal is recorded here with its video and timeline.</li>`;return;}
  list.innerHTML=shown.map((t)=>{const i=trials.list.indexOf(t);const b=outcomeBadge(t),current=trials.current&&trials.current.session===t.session&&trials.current.id===t.id;
    const verdict=t.verdict==="success"?`<span class="mark pass" title="Marked success">✓<span class="sr">Success</span></span>`:t.verdict==="fail"?`<span class="mark fail" title="Marked fail">✗<span class="sr">Fail</span></span>`:"";
    return `<li><button type="button" class="trial-row" data-trial="${i}"${current?' aria-current="true"':""}><span class="trial-row-goal">${esc(t.goal)}</span><span class="trial-row-meta">${esc(when(t.started_ms))} · ${t.steps||0} step${t.steps===1?"":"s"}<span class="badge ${b.cls}" title="${OUTCOME_HELP[b.label]}">${b.label}</span>${t.camera==="fake"?'<span class="badge test">Test camera</span>':""}${verdict}</span></button></li>`;}).join("");
  list.querySelectorAll("[data-trial]").forEach(b=>b.onclick=()=>openTrial(trials.list[Number(b.dataset.trial)]));
}
async function loadTrials(){
  try{trials.list=await api("api/trials");renderTrialList();const link=location.hash.match(/^#trials\/([^/]+)\/([^/]+)$/);if(link&&(!trials.current||trials.current.session!==decodeURIComponent(link[1])||trials.current.id!==decodeURIComponent(link[2]))){const t=trials.list.find(x=>x.session===decodeURIComponent(link[1])&&x.id===decodeURIComponent(link[2]));if(t)openTrial(t);}if(trials.current){const t=trials.list.find(x=>x.session===trials.current.session&&x.id===trials.current.id);if(t&&t.outcome!==trials.current.outcome)openTrial(t);}}
  catch(err){$("#trial-list").innerHTML=`<li class="trial-empty error-text">Trials could not be loaded: ${esc(err.message)}</li>`;}
}
function eventText(e){return e.kind==="end"?`Trial ended: ${e.outcome}`:e.text||"";}
async function openTrial(t){
  trials.current=t;renderTrialList();
  history.replaceState(null,"",`#trials/${encodeURIComponent(t.session)}/${encodeURIComponent(t.id)}`);
  let detail;
  try{detail=await api(trialURL(t));}catch(err){toast(err.message);return;}
  if(trials.current!==t)return;
  trials.detail=detail;
  const info=detail.trial,b=outcomeBadge(info);
  $("#trial-placeholder").hidden=true;$("#trial-view").hidden=false;
  $("#trial-goal").textContent=info.goal;
  const running=!info.outcome&&info.active;
  $("#trial-outcome").textContent=running?"Running · open Live":b.label;$("#trial-outcome").className=`badge ${b.cls}`;
  if(running)$("#trial-outcome").href="#live";else $("#trial-outcome").removeAttribute("href");
  $("#trial-test").hidden=info.camera!=="fake";$("#trial-outcome").title=OUTCOME_HELP[b.label]||"";
  $("#trial-folder").textContent=info.folder?`Saved in ${info.folder}`:"";
  const latency=info.latency_ms?` · plan round trip ${seconds(info.latency_ms.mean)} mean, ${seconds(info.latency_ms.max)} max`:"";
  const camera=info.camera?{glasses:"Aria glasses",fake:"test camera"}[info.camera]||info.camera:"camera unknown";
  $("#trial-meta").textContent=`${when(info.started_ms)} · ${camera} · ${info.steps||0} step${info.steps===1?"":"s"}${latency}`;
  const video=$("#trial-video");
  $("#trial-novideo").hidden=detail.frames>0;video.hidden=!detail.frames;
  video.querySelectorAll("track").forEach(track=>track.remove());
  if(detail.frames){
    const fresh=info.outcome?"":`?at=${Date.now()}`;
    video.src=localURL(trialURL(t,`/video.mp4${fresh}`));
    // Captions of everything said and noted, on by default; video.vtt sits next to video.mp4 for VLC.
    const track=document.createElement("track");Object.assign(track,{kind:"captions",srclang:"en",label:"Guidance",default:true,src:localURL(trialURL(t,`/captions.vtt${fresh}`))});
    video.append(track);
  }else video.removeAttribute("src");
  trials.events=detail.events.filter(e=>EVENT_LABELS[e.kind]&&!e.voice).map(e=>({...e,offset:detail.video_start_ms==null?null:e.t_ms-detail.video_start_ms}));
  $("#trial-events").innerHTML=trials.events.length?trials.events.map((e,i)=>`<li><button type="button" class="event kind-${e.kind}" data-event="${i}"${e.offset==null?" disabled":""}><span class="event-time">${e.offset==null?"–":clock(e.offset)}</span><span class="event-kind">${EVENT_LABELS[e.kind]}</span><span class="event-text">${esc(eventText(e))}</span></button></li>`).join(""):`<li class="trial-empty">No events were recorded.</li>`;
  $("#trial-events").querySelectorAll("[data-event]").forEach(el=>el.onclick=()=>{video.currentTime=Math.max(0,trials.events[Number(el.dataset.event)].offset/1000);video.play().catch(()=>{});});
  renderReview(detail.review);$("#review-status").textContent="";tour.check();
}
function renderReview(review){
  document.querySelectorAll(".verdict-button").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.verdict===review.verdict)));
  $("#trial-notes").value=review.notes||"";
}
async function saveReview(){
  const t=trials.current,d=trials.detail;if(!t||!d)return;
  const verdict=document.querySelector('.verdict-button[aria-pressed="true"]')?.dataset.verdict||null,notes=$("#trial-notes").value;
  $("#review-status").textContent="Saving…";$("#review-status").classList.remove("error-text");
  try{d.review=await api(trialURL(t,"/review"),{method:"PUT",body:JSON.stringify({verdict,notes})});t.verdict=verdict;renderTrialList();tour.check();$("#review-status").textContent=`Saved ${new Date(d.review.updated_at*1000).toLocaleTimeString()}`;}
  catch(err){$("#review-status").textContent=`Not saved: ${err.message}`;$("#review-status").classList.add("error-text");}
}
document.querySelectorAll(".verdict-button").forEach(b=>b.onclick=()=>{const on=b.getAttribute("aria-pressed")!=="true";document.querySelectorAll(".verdict-button").forEach(x=>x.setAttribute("aria-pressed",String(x===b&&on)));saveReview();});
$("#trial-notes").addEventListener("blur",()=>{if(trials.detail&&$("#trial-notes").value!==(trials.detail.review.notes||""))saveReview();});
$("#trial-video").addEventListener("timeupdate",()=>{const now=$("#trial-video").currentTime*1000;let last=-1;trials.events.forEach((e,i)=>{if(e.offset!=null&&e.offset<=now)last=i;});document.querySelectorAll("#trial-events .event").forEach((el,i)=>el.classList.toggle("playing",i===last));});
$("#trials-refresh").onclick=()=>{loadTrials();if(trials.current)openTrial(trials.current);};
setInterval(()=>{if(!$("#trials").hidden)loadTrials();},15000);
// Walkthrough: one practice trial on the test camera. It points at the real control for each step
// and moves on when the real thing has happened; it never presses anything itself.
const DEMO_GOAL="Pick up the black marker on the grey table";
const TOUR=[
  {target:"#sys-guidance",title:"Start the guidance program",text:"It links the camera, the model and the glasses. Press Start.",
   done:()=>system.rows.guidance?.state==="running"&&Boolean(live.state?.node_online)},
  {target:".segmented",title:"Choose a camera",text:"Glasses streams the real glasses. Test camera plays a still photo so you can practise, and its trials are tagged Test camera. Choose Test camera.",
   done:()=>live.state?.camera?.source==="fake"&&cameraOk(live.state)},
  {target:"#live-band",title:"Let it watch for 10 seconds",text:"The model always looks at the last 10 s of camera. This band says what to do next; right now it counts the seconds.",
   done:()=>(live.state?.camera?.window_s||0)>=9.5||ACTIVE(live.state)},
  {target:"#live-goal-form",title:"Give the wearer a goal",text:"Type what the wearer should achieve and press Set goal. One that fits the test photo is filled in.",
   enter:()=>{if(!ACTIVE(live.state)&&!$("#live-goal").value)$("#live-goal").value=DEMO_GOAL;},
   done:()=>ACTIVE(live.state)&&["awaiting_feedback","awaiting_confirmation"].includes(live.state.task)},
  {target:"#live-step-actions",title:"Act, then report back",text:"The wearer follows the instruction. Press Done with step (D) when it's done, or Need help (H) if they are stuck. The model then plans the next step.",
   done:()=>tour.replied&&tour.sawPlanning&&["awaiting_feedback","awaiting_confirmation","completed"].includes(live.state?.task)},
  {target:()=>$("#live-confirm-actions").hidden?"#live-finish":"#live-yes",title:"Finish the task",text:"When the goal is reached, press Finish task; it asks for a second press so a trial never closes by accident. If the model thinks it's done first, the band asks Finished? and Yes, finish closes it.",
   done:()=>live.state?.task==="completed"},
  {target:()=>location.hash.startsWith("#trials")?(trials.current?"#trial-view .verdict":"#trial-list"):'.sidebar a[href="#trials"]',title:"Review the trial",text:"Open Trials and the newest trial. Play the video: captions show what was said. Click a timeline row to jump there, then mark Success or Fail.",
   done:()=>Boolean(trials.detail?.review?.verdict)&&trials.current?.id===tour.trialId},
];
const tour={step:-1,replied:false,sawPlanning:false,trialId:null,
  seen(){try{return localStorage.getItem("aria-tour")==="done";}catch{return false;}},
  remember(){try{localStorage.setItem("aria-tour","done");}catch{}$("#tour-offer").hidden=true;},
  start(){this.remember();$("#tour").hidden=false;if(!location.hash.startsWith("#live"))location.hash="#live";this.go(0);},
  go(i){
    this.step=i;this.replied=false;this.sawPlanning=false;
    if(i>=TOUR.length){this.highlight(null);$("#tour-title").textContent="That's the whole loop";
      $("#tour-text").textContent="Every goal is a trial, saved with its video, captions and timeline. For real trials choose Glasses. Replay this from Walkthrough in the top bar.";
      $("#tour-count").textContent="";$("#tour-skip").hidden=true;$("#tour-exit").textContent="Close";return;}
    const s=TOUR[i];s.enter?.();
    $("#tour-title").textContent=s.title;$("#tour-text").textContent=s.text;$("#tour-count").textContent=`${i+1} of ${TOUR.length}`;
    $("#tour-skip").hidden=false;$("#tour-exit").textContent="Exit";
    this.check();
  },
  highlight(selector){
    document.querySelectorAll(".tour-target").forEach(e=>e.classList.remove("tour-target"));
    const el=selector&&$(selector);if(!el||el.closest("[hidden]"))return;
    el.classList.add("tour-target");
    if(!this.scrolled){el.scrollIntoView({block:"nearest",behavior:"smooth"});this.scrolled=true;}
  },
  check(){
    if(this.step<0||this.step>=TOUR.length)return;
    if(["ready","planning"].includes(live.state?.task)&&this.replied)this.sawPlanning=true;
    const s=TOUR[this.step];
    if(s.done()){if(this.step===5)this.trialId=live.state?.trial?.id;this.scrolled=false;this.go(this.step+1);return;}
    this.highlight(typeof s.target==="function"?s.target():s.target);
  },
  note(kind){if(this.step===4&&kind==="reply")this.replied=true;},
  exit(){this.step=-1;this.highlight(null);$("#tour").hidden=true;}};
$("#tour-start").onclick=()=>tour.start();
$("#tour-later").onclick=()=>tour.remember();
$("#tour-button").onclick=()=>tour.start();
$("#tour-skip").onclick=()=>{if(tour.step===5)tour.trialId=live.state?.trial?.id;tour.scrolled=false;tour.go(tour.step+1);};
$("#tour-exit").onclick=()=>tour.exit();
$("#tour-offer").hidden=tour.seen();

// One view at a time: the sidebar switches sections; #trials/<session>/<trial> opens a trial.
const VIEWS=["live","trials","workbench","connect"];
function route(){
  const view=VIEWS.find(v=>location.hash===`#${v}`||location.hash.startsWith(`#${v}/`))||"live";
  document.querySelectorAll("main > section").forEach(section=>section.hidden=section.id!==view);
  document.querySelectorAll(".sidebar nav a").forEach(a=>a.classList.toggle("active",a.hash===`#${view}`));
  live.visible=view==="live";if(!live.visible)talk(false);
  if(view==="trials")loadTrials();else $("#trial-video").pause();
  scrollTo(0,0);tour.scrolled=false;tour.check();
}
window.addEventListener("hashchange",route);route();
$("#trials-hide-test").checked=hideTest();
$("#trials-hide-test").onchange=(e)=>{try{localStorage.setItem("aria-hide-test",e.target.checked?"1":"0");}catch{}renderTrialList();};
pollLive();pollPreview();
refreshStatus();setInterval(refreshStatus,5000);
