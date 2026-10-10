const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const STYLE_LABELS = {neutral:"Neutral",warm:"Warm",happy:"Happy",sad:"Sad",angry:"Angry",excited:"Excited",serious:"Serious",inquisitive:"Inquisitive"};

const e = {};
[
  "modelStatus","runtimePanel","runtimeTitle","runtimeMessage","runtimeProgress","installRuntime",
  "quickTab","ttsModeButton","subtitleTab","voiceModeSwitch","projectsTab","batchTab","videoTab","quickStudio","subtitleStudio","projectStudio","batchStudio","videoStudio","ttsText","textCount","textHint","clearText","quickAnalysis",
  "subtitleFile","subtitleText","subtitleCount","subtitleVoice","subtitleSpeed","subtitleEmotion","subtitleStyleWrap","subtitleStyle","subtitleError","generateSubtitle","subtitleEmpty","subtitleProgress","subtitleProgressMessage","subtitleProgressFraction","subtitleProgressBar","subtitleProgressPercent","subtitleResult","subtitleAudio","subtitleWarnings","downloadSubtitleAudio","downloadSubtitleSrt",
  "voiceSelect","deleteVoice","manageStyles","speed","speedValue","quickEmotionMode","quickStyleBlock",
  "quickStyle","generateButton","generationProgress","progressMessage","progressPercent","progressBar","formError",
  "emptyOutput","currentOutput","currentVoice","currentText","outputDuration","audioPlayer","downloadCurrent",
  "libraryList","refreshLibrary","toast","voiceDialog","voiceForm","voiceName","voiceTranscript","voiceFile",
  "fileLabel","voiceConsent","voiceError","saveVoice","uploadTab","recordTab","uploadPanel","recordPanel",
  "recordButton","recordTimer","recordState","recordPreview","styleDialog","styleForm","styleName","styleTranscript",
  "styleFile","styleFileLabel","styleRecord","styleRecordTimer","stylePreview","styleError","savedStyles",
  "projectSelect","newProject","emptyNewProject","deleteProject","projectEmpty","projectWorkspace","projectTitle",
  "projectVoice","deliveryMode","projectQuality","projectSpeed","emotionIntensity","intensityValue","chaptersEditor",
  "addChapter","projectFile","saveProject","analyzeProject","projectError","projectCount","analyzerBadge",
  "missingStyles","segmentsList","estimatedDuration","renderProject","renderProgress","renderMessage","renderPercent",
  "renderBar","pauseRender","resumeRender","cancelRender","renderResult","projectAudio","downloadProject",
  "chapterDownloads","projectDialog","projectForm","newProjectTitle","newProjectText","newProjectError",
  "newBatch","batchTitle","batchFiles","batchCsv","addBatchItem","batchItems","batchCount","batchError","startBatch",
  "batchSelect","batchEmpty","batchProgress","batchState","batchFraction","batchBar","batchMessage","pauseBatch","resumeBatch",
  "cancelBatch","retryBatch","batchResults","downloadBatch",
  "openGeminiKeys","geminiBadge","uploadSourceMode","rednoteSourceMode","uploadSourcePanel","rednoteSourcePanel","rednoteUrl","rednotePermission","videoFiles","videoDropZone","videoFileList","videoVoice","videoQuality","videoSpeed","videoDelivery","videoRatio","videoFill","mirrorOnImport","completionSound","videoError","startVideoDub",
  "videoJobSelect","videoQueueSummary","videoEmpty","videoJobView","videoJobState","videoJobTitle","videoJobPercent","videoJobBar","videoJobMessage","videoActivity","videoActivityTitle","videoElapsed","videoLiveSegments","videoActiveActions","pauseVideo","cancelVideo","videoRecovery","retryVideo","deleteVideo","videoReview","videoSegmentCount","videoSegments","renderVideo","videoResult","dubbedVideo","downloadDubbedVideo","deleteCompletedVideo",
  "videoFailure","videoFailureTitle","videoFailureMessage",
  "manualTranslation","manualExpected","copySourceTranscript","copySourceSrt","downloadSourceSrt","manualSrtFile","manualSrtText","manualSrtCount","manualSrtStatus","manualSrtError","importManualSrt","switchManual","replaceWithSrt",
  "editorUndo","editorRedo","previewTimeline","addCaptionCue","editSaveState","sourcePreview","captionPreview","reviewVideoVoice","originalAudioVolume","originalAudioValue","audioDucking","captionBurn","captionFont","captionSize","captionSizeValue","captionColor","captionSplit","captionPosition","captionX","captionY","exportCheckpoint",
  "sceneTimeline","mirrorHorizontal","blurPreview","blurEnabled","blurX","blurY","blurWidth","blurHeight","blurStrength","blurConfidence","redetectBlur","editAgain",
  "geminiDialog","geminiForm","savedKeyStatus","geminiKeys","geminiError","clearGeminiKeys","testGeminiKeys","saveGeminiKeys","closeGeminiDialog",
  "captionCueDialog","captionCueForm","closeCaptionCue","cancelCaptionCue","newCueStart","newCueEnd","newCueText","newCueCaptionOnly","newCueError"
].forEach((id) => { e[id] = document.getElementById(id); });

let voices = [], projects = [], project = null, quickJob = null, subtitleJob = null, render = null, batches = [], batch = null, videoJobs = [], videoJob = null, videoPoll = null;
let voiceBlob = null, styleBlob = null, toastTimer = null, autosaveTimer = null;
let alertAudio = null;
let editorScenes=[], editorUndoStack=[], editorRedoStack=[], editSaveTimer=null;
const watchedVideoJobs = new Set(), announcedVideoMilestones = new Set();

function syllables(text) { return (text.match(/[\u1000-\u1021]/g) || []).length; }
function timeLabel(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const s = Math.round(Number(value)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  const tail = String(m).padStart(2,"0") + ":" + String(s % 60).padStart(2,"0");
  return h ? h + ":" + tail : tail;
}
function errorAt(node, message) { node.textContent = message; node.hidden = false; }
function clearError(node) { node.textContent = ""; node.hidden = true; }
function notify(message) {
  e.toast.textContent = message; e.toast.hidden = false; clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { e.toast.hidden = true; }, 5000);
}
function completionSoundEnabled(){return !e.completionSound||e.completionSound.checked;}
function unlockAlertAudio(){
  if(!completionSoundEnabled()||alertAudio)return;
  const AudioContext=window.AudioContext||window.webkitAudioContext;
  if(AudioContext)alertAudio=new AudioContext();
}
function playAlertSound(kind="success"){
  if(!completionSoundEnabled())return;
  unlockAlertAudio();
  if(!alertAudio)return;
  if(alertAudio.state==="suspended")alertAudio.resume().catch(()=>{});
  const start=alertAudio.currentTime+.03;
  const notes=kind==="attention"?[[330,0],[262,.18]]:[[523,0],[659,.16],[784,.32]];
  notes.forEach(([frequency,offset],index)=>{
    const oscillator=alertAudio.createOscillator(),gain=alertAudio.createGain();
    oscillator.type="sine";oscillator.frequency.value=frequency;
    gain.gain.setValueAtTime(.0001,start+offset);gain.gain.exponentialRampToValueAtTime(.075,start+offset+.025);
    gain.gain.exponentialRampToValueAtTime(.0001,start+offset+.16);
    oscillator.connect(gain).connect(alertAudio.destination);oscillator.start(start+offset);oscillator.stop(start+offset+.18);
  });
}
function announceVideoMilestone(item){
  const marker=item.id+":"+item.state;
  if(announcedVideoMilestones.has(marker))return;
  const watched=watchedVideoJobs.has(item.id), changed=videoJob&&videoJob.id===item.id&&videoJob.state!==item.state;
  if(!watched&&!changed)return;
  if(item.state==="review_ready"){
    announcedVideoMilestones.add(marker);playAlertSound();
    notify("Translation ready — review the Burmese text, then choose Create Burmese video.");
  }else if(item.state==="awaiting_translation"){
    announcedVideoMilestones.add(marker);playAlertSound();
    notify("Source transcript ready — add your Burmese SRT to continue.");
  }else if(item.state==="succeeded"){
    announcedVideoMilestones.add(marker);playAlertSound();
    notify("Burmese video ready — preview it or download the MP4.");
  }else if(item.state==="failed"){
    announcedVideoMilestones.add(marker);playAlertSound("attention");
    notify("This video needs attention — choose Retry / Resume when you are ready.");
  }
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.status + " " + response.statusText;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function quality() { return document.querySelector('input[name="quality"]:checked').value; }

function updateQuickCount() {
  const count = e.ttsText.value.length;
  e.textCount.textContent = count.toLocaleString() + " / 50,000 characters";
  e.textCount.classList.toggle("over", count > 50000);
  e.textHint.textContent = count > 50000 ? "Shorten this script to 50,000 characters." : "Only Burmese text is sent to Microsoft online speech. Completed chunks are cached locally.";
  previewEmotion();
}
function updateRuntime(runtime) {
  if (!runtime || runtime.state === "ready") { e.runtimePanel.hidden = true; e.installRuntime.disabled = false; return; }
  e.runtimePanel.hidden = false; e.installRuntime.disabled = runtime.state === "running";
  e.runtimeTitle.textContent = runtime.state === "running" ? "Installing local AI runtime… / တပ်ဆင်နေသည်…" : runtime.state === "failed" ? "Setup needs another try" : "Finish first-time setup";
  e.runtimeMessage.textContent = runtime.message || "Install the local GPU runtime and model.";
  e.runtimeProgress.textContent = runtime.progress || "";
}
async function loadStatus() {
  try {
    const status = await api("/api/status"), strong = e.modelStatus.querySelector("strong"), small = e.modelStatus.querySelector("small");
    e.modelStatus.className = "model-status " + status.state; updateRuntime(status.runtime);
    if (!quickJob) e.generateButton.disabled = status.state !== "ready";
    if (status.runtime && status.runtime.state !== "ready") { strong.textContent = "Setup required"; small.textContent = "ပထမအကြိမ် တပ်ဆင်ပါ"; }
    else if (status.state === "ready") { strong.textContent = "Online voices ready"; small.textContent = "7 voices · RTX not used"; }
    else if (status.state === "offline") { strong.textContent = "Voice service offline"; small.textContent = "Check internet and retry"; }
    else if (status.state === "error") { strong.textContent = "Speech setup needs attention"; small.textContent = status.error||"Run start.cmd"; }
    else { strong.textContent = "Checking online voices"; small.textContent = "အွန်လိုင်းအသံ စစ်ဆေးနေသည်"; }
  } catch (_) {
    e.modelStatus.className = "model-status error";
    e.modelStatus.querySelector("strong").textContent = "Server unavailable";
    e.modelStatus.querySelector("small").textContent = "ဆာဗာ ချိတ်ဆက်မရပါ";
  }
}
async function installRuntime() {
  e.installRuntime.disabled = true;
  try { updateRuntime(await api("/api/setup",{method:"POST"})); notify("Setup started. Keep this page open."); }
  catch (err) { e.installRuntime.disabled = false; errorAt(e.formError,err.message); }
}

function fillVoices(select, selected) {
  select.replaceChildren();
  voices.forEach((voice) => {
    const option = document.createElement("option"); option.value = voice.id || ""; option.textContent = voice.name; select.append(option);
  });
  select.value = selected || "";
  if (select.selectedIndex < 0 && select.options.length) select.selectedIndex = 0;
}
function quickVoice() { return voices.find((voice) => (voice.id || "") === e.voiceSelect.value); }
function updateVoiceControls() {
  e.deleteVoice.disabled = true; e.manageStyles.disabled = true; e.quickStyle.replaceChildren();
  e.subtitleStyle.replaceChildren();
  Object.entries(STYLE_LABELS).forEach(([style,label]) => {
    const option = document.createElement("option"); option.value = style; option.textContent = label+" · Expressive delivery"; e.quickStyle.append(option);
    const subtitleOption=option.cloneNode(true);e.subtitleStyle.append(subtitleOption);
  });
  if(e.quickEmotionMode.value==="auto")previewEmotion();
}
async function loadVoices(selected) {
  voices = await api("/api/voices");
  fillVoices(e.voiceSelect, selected === undefined ? e.voiceSelect.value : selected);
  fillVoices(e.subtitleVoice, e.subtitleVoice.value);
  fillVoices(e.projectVoice, project ? project.voice_id : "");
  fillVoices(e.videoVoice, e.videoVoice.value);
  updateVoiceControls();
}

function showGeneration(item) {
  e.emptyOutput.hidden = true; e.currentOutput.hidden = false; e.currentVoice.textContent = item.voice_name;
  e.currentText.textContent = item.text; e.outputDuration.textContent = timeLabel(item.duration);
  e.audioPlayer.src = item.audio_url; e.downloadCurrent.href = item.audio_url;
}
function generationCard(item) {
  const card = document.createElement("article"); card.className = "library-item";
  const play = document.createElement("button"); play.className = "play-item"; play.type = "button"; play.textContent = "▶"; play.setAttribute("aria-label","Play"); play.onclick = () => showGeneration(item);
  const copy = document.createElement("div"); copy.className = "library-copy";
  const title = document.createElement("strong"); title.textContent = item.voice_name;
  const meta = document.createElement("span"); meta.textContent = timeLabel(item.duration) + " · " + item.text; copy.append(title,meta);
  const actions = document.createElement("div"); actions.className = "library-actions";
  const download = document.createElement("a"); download.href = item.audio_url; download.download = ""; download.textContent = "↓"; download.setAttribute("aria-label","Download");
  const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.setAttribute("aria-label","Delete");
  remove.onclick = async () => { if (confirm("Delete only this generated audio?")) { await api("/api/generations/"+item.id,{method:"DELETE"}); loadLibrary(); } };
  actions.append(download,remove); card.append(play,copy,actions); return card;
}
async function loadLibrary() {
  try {
    const items = await api("/api/generations"); e.libraryList.replaceChildren();
    if (!items.length) { const empty = document.createElement("div"); empty.className = "library-empty"; empty.textContent = "No saved audio yet. / သိမ်းထားသော အသံ မရှိသေးပါ။"; e.libraryList.append(empty); }
    else items.forEach((item) => e.libraryList.append(generationCard(item)));
  } catch (_) {}
}
async function generateQuick() {
  clearError(e.formError); unlockAlertAudio(); const count = e.ttsText.value.length;
  if (!syllables(e.ttsText.value) || count > 50000) { errorAt(e.formError,count > 50000 ? "Shorten text to 50,000 characters." : "Enter Burmese text first."); return; }
  e.generateButton.disabled = true; e.generationProgress.hidden = false; quickJob = true;
  try {
    const job = await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      text:e.ttsText.value,voice_id:e.voiceSelect.value||null,speed:Number(e.speed.value),preset:quality(),
      emotion_mode:e.quickEmotionMode.value,style_override:e.quickEmotionMode.value==="manual"?e.quickStyle.value:null,emotion_intensity:.35
    })});
    while (true) {
      const state = await api("/api/jobs/"+job.id), percent = state.total ? Math.round(state.progress/state.total*100) : 4;
      e.progressMessage.textContent = state.message; e.progressPercent.textContent = percent+"%"; e.progressBar.style.width = percent+"%";
      if (state.state === "succeeded") { showGeneration(state.generation); e.generationProgress.hidden = true; loadLibrary(); playAlertSound(); notify("Your Burmese voice is ready — listen or download the WAV."); break; }
      if (state.state === "failed") throw new Error(state.error || state.message);
      await new Promise((resolve) => setTimeout(resolve,900));
    }
  } catch (err) { errorAt(e.formError,err.message); e.generationProgress.hidden = true; playAlertSound("attention"); notify("Voice generation needs attention — check the message and try again."); }
  finally { quickJob = null; e.generateButton.disabled = false; }
}

let analysisTimer=null;
function previewEmotion(){clearTimeout(analysisTimer);if(e.quickEmotionMode.value!=="auto"||!syllables(e.ttsText.value)){e.quickAnalysis.hidden=true;return;}analysisTimer=setTimeout(async()=>{try{const result=await api("/api/emotions/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:e.ttsText.value,voice_id:e.voiceSelect.value||null,delivery_mode:"conversational"})});e.quickAnalysis.replaceChildren();result.segments.slice(0,12).forEach((item)=>{const chip=document.createElement("span");chip.className="emotion-chip "+item.effective_style;chip.textContent=(STYLE_LABELS[item.effective_style]||item.effective_style)+" · "+item.performance;e.quickAnalysis.append(chip);});e.quickAnalysis.hidden=false;}catch(_){e.quickAnalysis.hidden=true;}},450);}

function updateSubtitleCount(){const cues=(e.subtitleText.value.match(/-->/g)||[]).length,characters=e.subtitleText.value.replace(/^\d+\s*$|^\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->.*$/gm,"").trim().length;e.subtitleCount.textContent=cues+" cue"+(cues===1?"":"s")+" · "+characters.toLocaleString()+" chars";}
async function importSubtitleFile(){const file=e.subtitleFile.files[0];if(!file)return;try{e.subtitleText.value=await file.text();updateSubtitleCount();notify("SRT imported — review it, then generate timed narration.");}catch(err){errorAt(e.subtitleError,err.message);}finally{e.subtitleFile.value="";}}
async function generateSubtitleVoice(){clearError(e.subtitleError);if(!e.subtitleText.value.includes("-->")){errorAt(e.subtitleError,"Paste or import a valid SRT file first.");return;}unlockAlertAudio();e.generateSubtitle.disabled=true;e.subtitleProgress.hidden=false;e.subtitleResult.hidden=true;e.subtitleEmpty.hidden=true;try{subtitleJob=await api("/api/subtitle-jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({srt:e.subtitleText.value,voice_id:e.subtitleVoice.value,speed:Number(e.subtitleSpeed.value),emotion_mode:e.subtitleEmotion.value,style_override:e.subtitleEmotion.value==="manual"?e.subtitleStyle.value:null,emotion_intensity:.35})});while(true){const state=await api("/api/subtitle-jobs/"+subtitleJob.id),percent=state.total?Math.round(state.progress/state.total*100):2;e.subtitleProgressMessage.textContent=state.message;e.subtitleProgressFraction.textContent=state.progress+" / "+state.total;e.subtitleProgressBar.style.width=percent+"%";e.subtitleProgressPercent.textContent=percent+"%";if(state.state==="failed")throw new Error(state.error||state.message);if(state.state==="succeeded"){e.subtitleProgress.hidden=true;e.subtitleResult.hidden=false;e.subtitleAudio.src=state.generation.audio_url;e.downloadSubtitleAudio.href=state.generation.audio_url;e.downloadSubtitleSrt.href=state.srt_url;e.subtitleWarnings.hidden=!state.warnings.length;e.subtitleWarnings.textContent=state.warnings.join(" ");playAlertSound();notify("Timed subtitle narration is ready — listen or download the WAV.");loadLibrary();break;}await new Promise(resolve=>setTimeout(resolve,800));}}catch(err){errorAt(e.subtitleError,err.message);e.subtitleProgress.hidden=true;playAlertSound("attention");}finally{e.generateSubtitle.disabled=false;}}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44+samples.length*2), view = new DataView(buffer);
  function write(offset,text) { [...text].forEach((char,index) => view.setUint8(offset+index,char.charCodeAt(0))); }
  write(0,"RIFF"); view.setUint32(4,36+samples.length*2,true); write(8,"WAVE"); write(12,"fmt ");
  view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true); view.setUint32(24,rate,true);
  view.setUint32(28,rate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true); write(36,"data"); view.setUint32(40,samples.length*2,true);
  samples.forEach((sample,index) => view.setInt16(44+index*2,Math.max(-1,Math.min(1,sample))*0x7fff,true));
  return new Blob([buffer],{type:"audio/wav"});
}
class Recorder {
  constructor(button,timer,preview,ready) { this.button=button;this.timer=timer;this.preview=preview;this.ready=ready;this.stream=null;this.parts=[]; }
  async toggle() { if (this.stream) return this.stop(); this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false}}); this.context=new AudioContext(); const source=this.context.createMediaStreamSource(this.stream); this.node=this.context.createScriptProcessor(4096,1,1); this.node.onaudioprocess=(event)=>this.parts.push(new Float32Array(event.inputBuffer.getChannelData(0))); source.connect(this.node);this.node.connect(this.context.destination);this.started=Date.now();this.button.classList.add("recording");this.button.querySelector("b").textContent="Stop recording";this.tick=setInterval(()=>{const s=Math.floor((Date.now()-this.started)/1000);this.timer.textContent=String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0");if(s>=15)this.stop();},250); }
  async stop() { clearInterval(this.tick);this.node.disconnect();this.stream.getTracks().forEach((track)=>track.stop());const rate=this.context.sampleRate;await this.context.close();const length=this.parts.reduce((sum,part)=>sum+part.length,0),audio=new Float32Array(length);let offset=0;this.parts.forEach((part)=>{audio.set(part,offset);offset+=part.length;});const blob=encodeWav(audio,rate);this.ready(blob);this.preview.src=URL.createObjectURL(blob);this.preview.hidden=false;this.stream=null;this.parts=[];this.button.classList.remove("recording");this.button.querySelector("b").textContent="Record instead"; }
}
const voiceRecorder = new Recorder(e.recordButton,e.recordTimer,e.recordPreview,(blob)=>{voiceBlob=blob;});
const styleRecorder = new Recorder(e.styleRecord,e.styleRecordTimer,e.stylePreview,(blob)=>{styleBlob=blob;});

async function saveVoice(event) {
  event.preventDefault();clearError(e.voiceError);const audio=voiceBlob||e.voiceFile.files[0];
  if(!audio){errorAt(e.voiceError,"Choose or record a WAV file.");return;}
  const form=new FormData();form.append("name",e.voiceName.value);form.append("transcript",e.voiceTranscript.value);form.append("audio",audio,"voice.wav");e.saveVoice.disabled=true;
  try{const voice=await api("/api/voices",{method:"POST",body:form});e.voiceDialog.close();e.voiceForm.reset();voiceBlob=null;e.recordPreview.hidden=true;await loadVoices(voice.id);notify("Voice saved locally.");}
  catch(err){errorAt(e.voiceError,err.message);}finally{e.saveVoice.disabled=false;}
}
async function deleteVoice() {
  const voice=quickVoice();if(!voice||voice.built_in||!confirm("Delete “"+voice.name+"” and all emotion clips?"))return;
  await api("/api/voices/"+voice.id,{method:"DELETE"});await loadVoices("");notify("Voice deleted.");
}
function showSavedStyles() {
  const voice=quickVoice();e.savedStyles.replaceChildren();
  ((voice&&voice.styles)||[]).forEach((style)=>{const chip=document.createElement("span");chip.className="saved-style";chip.append(document.createTextNode(STYLE_LABELS[style.style]||style.style));
    if(style.style!=="neutral"){const remove=document.createElement("button");remove.type="button";remove.textContent="×";remove.onclick=async()=>{if(confirm("Delete the "+style.style+" style clip?")){await api("/api/voices/"+voice.id+"/styles/"+style.style,{method:"DELETE"});await loadVoices(voice.id);showSavedStyles();}};chip.append(remove);}e.savedStyles.append(chip);});
}
async function saveStyle(event) {
  event.preventDefault();clearError(e.styleError);const voice=quickVoice(),audio=styleBlob||e.styleFile.files[0];
  if(!voice||!audio){errorAt(e.styleError,"Choose or record a WAV file.");return;}
  const form=new FormData();form.append("style",e.styleName.value);form.append("transcript",e.styleTranscript.value);form.append("audio",audio,e.styleName.value+".wav");
  try{await api("/api/voices/"+voice.id+"/styles",{method:"POST",body:form});styleBlob=null;e.styleFile.value="";e.styleTranscript.value="";e.stylePreview.hidden=true;await loadVoices(voice.id);showSavedStyles();notify("Emotion style saved.");}
  catch(err){errorAt(e.styleError,err.message);}
}

function studio(mode) {
  const quick=mode==="quick",subtitleMode=mode==="subtitle",videoMode=mode==="video";e.quickStudio.hidden=!quick;e.subtitleStudio.hidden=!subtitleMode;e.projectStudio.hidden=true;e.batchStudio.hidden=true;e.videoStudio.hidden=!videoMode;e.quickTab.classList.toggle("active",quick);e.subtitleTab.classList.toggle("active",subtitleMode);e.videoTab.classList.toggle("active",videoMode);
  e.quickTab.setAttribute("aria-selected",String(quick));e.subtitleTab.setAttribute("aria-selected",String(subtitleMode));e.videoTab.setAttribute("aria-selected",String(videoMode));if(videoMode){loadVideoJobs();loadGeminiStatus();}
}
async function loadProjects(selected) {
  projects=await api("/api/projects");e.projectSelect.replaceChildren();const first=document.createElement("option");first.value="";first.textContent="Choose a project";e.projectSelect.append(first);
  projects.forEach((item)=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.title+" · "+item.syllables.toLocaleString()+" syl";e.projectSelect.append(option);});
  const id=selected===undefined?(project?project.id:""):selected;
  if(projects.some((item)=>item.id===id)){e.projectSelect.value=id;await openProject(id);}
  else if(projects.length){e.projectSelect.value=projects[0].id;await openProject(projects[0].id);}
  else{project=null;e.projectEmpty.hidden=false;e.projectWorkspace.hidden=true;e.deleteProject.disabled=true;}
}
async function openProject(id) {
  project=await api("/api/projects/"+id);e.projectEmpty.hidden=true;e.projectWorkspace.hidden=false;e.deleteProject.disabled=false;e.projectTitle.value=project.title;
  fillVoices(e.projectVoice,project.voice_id||"");e.deliveryMode.value=project.delivery_mode;e.projectQuality.value=project.preset;e.projectSpeed.value=project.speed;e.emotionIntensity.value=project.emotion_intensity;e.intensityValue.textContent=Math.round(project.emotion_intensity*100)+"%";
  renderChapters(project.chapters);renderSegments(project.segments);updateProjectStats();if(project.renders[0])showRender(project.renders[0]);else{e.renderProgress.hidden=true;e.renderResult.hidden=true;}
}
function makeChapter(chapter={title:"Chapter",text:""}) {
  const card=document.createElement("article");card.className="chapter-card";const head=document.createElement("div");head.className="chapter-card-head";
  const title=document.createElement("input");title.className="chapter-title";title.maxLength=80;title.value=chapter.title;title.setAttribute("aria-label","Chapter title");
  const cast=document.createElement("select");cast.className="chapter-voice";fillVoices(cast,chapter.voice_id||"");cast.setAttribute("aria-label","Chapter voice");cast.title="Chapter speaker (uses project voice when set to Default)";const defaultOption=cast.options[0];if(defaultOption)defaultOption.textContent="Default cast";cast.onchange=scheduleSave;
  const remove=document.createElement("button");remove.type="button";remove.textContent="×";remove.setAttribute("aria-label","Remove chapter");remove.onclick=()=>{if($$(".chapter-card").length<=1)return notify("A project needs one chapter.");card.remove();updateProjectStats();scheduleSave();};
  const text=document.createElement("textarea");text.className="chapter-text";text.value=chapter.text;text.placeholder="မြန်မာစာကို ဤနေရာတွင် ထည့်ပါ။";text.setAttribute("aria-label","Chapter text");
  const meta=document.createElement("div");meta.className="chapter-meta";const count=document.createElement("span"),estimate=document.createElement("span");meta.append(count,estimate);
  function update(){const total=syllables(text.value);count.textContent=total.toLocaleString()+" syllables";estimate.textContent="≈ "+timeLabel(total*.28/Number(e.projectSpeed.value||1));updateProjectStats();scheduleSave();}
  title.addEventListener("input",scheduleSave);text.addEventListener("input",update);head.append(title,cast,remove);card.append(head,text,meta);setTimeout(update);return card;
}
function renderChapters(chapters){e.chaptersEditor.replaceChildren(...chapters.map(makeChapter));}
function chapters(){return $$(".chapter-card").map((card,index)=>({title:card.querySelector(".chapter-title").value.trim()||"Chapter "+(index+1),text:card.querySelector(".chapter-text").value.trim(),voice_id:card.querySelector(".chapter-voice").value||null}));}
function updateProjectStats(){const total=chapters().reduce((sum,item)=>sum+syllables(item.text),0);e.projectCount.textContent=total.toLocaleString()+" / 50,000 syllables";e.projectCount.classList.toggle("over",total>50000);e.estimatedDuration.textContent=timeLabel(total*.28/Number(e.projectSpeed.value||1));}
function payload(){return{title:e.projectTitle.value.trim()||"Untitled narration",voice_id:e.projectVoice.value||null,speed:Number(e.projectSpeed.value),preset:e.projectQuality.value,delivery_mode:e.deliveryMode.value,emotion_intensity:Number(e.emotionIntensity.value),chapters:chapters()};}
function scheduleSave(){if(!project)return;clearTimeout(autosaveTimer);autosaveTimer=setTimeout(()=>saveProject(true),1400);}
async function saveProject(silent=false){
  if(!project)return null;clearError(e.projectError);const body=payload();if(body.chapters.some((item)=>!item.text)){if(!silent)errorAt(e.projectError,"Every chapter needs Burmese text.");return null;}
  try{project=await api("/api/projects/"+project.id,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const option=[...e.projectSelect.options].find((item)=>item.value===project.id);if(option)option.textContent=project.title+" · "+project.syllables.toLocaleString()+" syl";if(!silent)notify("Project saved locally.");return project;}
  catch(err){if(!silent)errorAt(e.projectError,err.message);return null;}
}
async function analyze(){
  if(!await saveProject(true))return;e.analyzeProject.disabled=true;e.analyzeProject.textContent="Analyzing locally…";
  try{const result=await api("/api/projects/"+project.id+"/analyze",{method:"POST"});project=await api("/api/projects/"+project.id);renderSegments(result.segments);e.analyzerBadge.textContent=result.analyzer.state==="ready"?"Local analyzer ready":"Basic analysis";e.analyzerBadge.classList.toggle("ready",result.analyzer.state==="ready");notify("Performance map ready.");}
  catch(err){errorAt(e.projectError,err.message);}finally{e.analyzeProject.disabled=false;e.analyzeProject.textContent="Analyze emotion / ခံစားချက်စစ်မည်";}
}
function renderSegments(segments=[]){
  e.segmentsList.replaceChildren();if(!segments.length){const empty=document.createElement("div");empty.className="performance-empty";empty.textContent="Analyze the script to preview how every sentence will be spoken.";e.segmentsList.append(empty);e.analyzerBadge.textContent="Not analyzed";e.missingStyles.hidden=true;return;}
  const missing=new Set();
  segments.forEach((segment)=>{const chosenVoice=voices.find((item)=>item.id===(segment.voice_id||project.voice_id)),available=new Set(((chosenVoice&&chosenVoice.styles)||[]).map((item)=>item.style));if(!available.has(segment.effective_style))missing.add(segment.effective_style);const card=document.createElement("article");card.className="segment-card";const copy=document.createElement("div"),text=document.createElement("p"),meta=document.createElement("small");text.textContent=segment.text;meta.textContent=(STYLE_LABELS[segment.detected_style]||segment.detected_style)+" · "+Math.round(segment.confidence*100)+"% · "+segment.explanation;copy.append(text,meta);const controls=document.createElement("div");controls.className="segment-controls";const select=document.createElement("select");select.setAttribute("aria-label","Override sentence emotion");Object.entries(STYLE_LABELS).forEach(([value,label])=>{const option=document.createElement("option");option.value=value;option.textContent=label+(available.has(value)?" · Recorded":" · Approx.");select.append(option);});select.value=segment.effective_style;select.onchange=async()=>{await api("/api/projects/"+project.id+"/segments/"+segment.id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({style:select.value})});segment.effective_style=select.value;renderSegments(segments);};const speaker=document.createElement("select");fillVoices(speaker,segment.voice_id||"");speaker.setAttribute("aria-label","Sentence speaker override");speaker.options[0].textContent="Chapter/default voice";speaker.onchange=async()=>{await api("/api/projects/"+project.id+"/segments/"+segment.id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({voice_id:speaker.value||null})});segment.voice_id=speaker.value||null;segment.manual_voice_override=Boolean(speaker.value);renderSegments(segments);};controls.append(speaker,select);card.append(copy,controls);e.segmentsList.append(card);});
  e.analyzerBadge.textContent=segments.length+" sentences";e.missingStyles.hidden=missing.size===0;e.missingStyles.textContent=missing.size?"Approximate delivery: "+[...missing].map((item)=>STYLE_LABELS[item]).join(", ")+". Record these clips for stronger emotion.":"All selected styles have recordings.";
}
async function startRender(){if(!await saveProject(true))return;if(!project.segments.length){await analyze();project=await api("/api/projects/"+project.id);}e.renderProject.disabled=true;try{render=await api("/api/projects/"+project.id+"/renders",{method:"POST"});showRender(render);pollRender(render.id);}catch(err){errorAt(e.projectError,err.message);e.renderProject.disabled=false;}}
function showRender(item){
  render=item;const percent=item.total_chunks?Math.round(item.completed_chunks/item.total_chunks*100):0,active=["queued","rendering","finalizing","paused","interrupted","failed","pausing"].includes(item.state);
  e.renderProgress.hidden=!active;e.renderMessage.textContent=item.error||item.message;e.renderPercent.textContent=percent+"%";e.renderBar.style.width=percent+"%";
  e.pauseRender.hidden=!["queued","rendering","pausing"].includes(item.state);e.resumeRender.hidden=!["paused","interrupted","failed"].includes(item.state);e.cancelRender.hidden=!["queued","rendering","pausing","paused","interrupted"].includes(item.state);e.renderProject.disabled=["queued","rendering","pausing","finalizing"].includes(item.state);
  if(item.state==="succeeded"){e.renderProgress.hidden=true;e.renderResult.hidden=false;e.renderProject.disabled=false;e.projectAudio.src=item.audio_url;e.downloadProject.href=item.audio_url;e.chapterDownloads.replaceChildren();((item.config&&item.config.chapters)||[]).forEach((chapter)=>{const link=document.createElement("a");link.href="/api/renders/"+item.id+"/chapters/"+chapter.position+"/audio";link.download="";link.textContent=chapter.title;e.chapterDownloads.append(link);});}
}
async function pollRender(id){while(true){const item=await api("/api/renders/"+id);showRender(item);if(["succeeded","failed","paused","interrupted","cancelled"].includes(item.state)){project=await api("/api/projects/"+project.id);return;}await new Promise((resolve)=>setTimeout(resolve,1200));}}
async function controlRender(action){if(!render)return;try{const item=await api("/api/renders/"+render.id+"/"+action,{method:"POST"});showRender(item);if(action==="resume"||action==="retry")pollRender(item.id);}catch(err){errorAt(e.projectError,err.message);}}
function openNewProject(){e.newProjectText.value=syllables(e.ttsText.value)?e.ttsText.value:"";clearError(e.newProjectError);e.projectDialog.showModal();}
async function createProject(event){event.preventDefault();clearError(e.newProjectError);try{const item=await api("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:e.newProjectTitle.value,voice_id:e.voiceSelect.value||null,speed:1,preset:"balanced",delivery_mode:"consistent",emotion_intensity:.35,chapters:[{title:"Chapter 1",text:e.newProjectText.value}]})});e.projectDialog.close();e.projectForm.reset();e.newProjectTitle.value="My narration";await loadProjects(item.id);}catch(err){errorAt(e.newProjectError,err.message);}}
async function deleteProject(){if(!project||!confirm("Delete “"+project.title+"” and its render files?"))return;try{await api("/api/projects/"+project.id,{method:"DELETE"});project=null;loadProjects();}catch(err){errorAt(e.projectError,err.message);}}

function makeBatchItem(item={video_name:"Video",script:"",voice_id:"",speed:1,preset:"turbo",delivery_mode:"consistent"}){const card=document.createElement("article");card.className="batch-item";const top=document.createElement("div");top.className="batch-item-head";const name=document.createElement("input");name.className="batch-name";name.value=item.video_name||"Video";name.maxLength=120;name.setAttribute("aria-label","Video name");const remove=document.createElement("button");remove.type="button";remove.textContent="×";remove.setAttribute("aria-label","Remove video");remove.onclick=()=>{card.remove();updateBatchCount();};top.append(name,remove);const script=document.createElement("textarea");script.className="batch-script";script.rows=4;script.value=item.script||"";script.placeholder="မြန်မာစာ…";const settings=document.createElement("div");settings.className="batch-item-settings";const voice=document.createElement("select");voice.className="batch-voice";fillVoices(voice,item.voice_id||"");const speed=document.createElement("input");speed.className="batch-speed";speed.type="number";speed.min=".8";speed.max="2";speed.step=".05";speed.value=item.speed||1;const quality=document.createElement("select");quality.className="batch-quality";["turbo","fast","balanced","high"].forEach((value)=>{const option=document.createElement("option");option.value=value;option.textContent=value[0].toUpperCase()+value.slice(1);quality.append(option);});quality.value=item.preset||"turbo";const delivery=document.createElement("select");delivery.className="batch-delivery";[["consistent","Consistent"],["conversational","Conversational"],["dramatic","Dramatic"]].forEach(([value,label])=>{const option=document.createElement("option");option.value=value;option.textContent=label;delivery.append(option);});delivery.value=item.delivery_mode||"consistent";settings.append(voice,speed,quality,delivery);[script,name].forEach((node)=>node.addEventListener("input",updateBatchCount));card.append(top,script,settings);return card;}
function updateBatchCount(){const cards=$$(".batch-item");e.batchCount.textContent=cards.length+" video"+(cards.length===1?"":"s");}
function batchPayload(){return $$(".batch-item").map((card,index)=>({video_name:card.querySelector(".batch-name").value.trim()||"Video "+(index+1),script:card.querySelector(".batch-script").value.trim(),voice_id:card.querySelector(".batch-voice").value||null,speed:Number(card.querySelector(".batch-speed").value),preset:card.querySelector(".batch-quality").value,delivery_mode:card.querySelector(".batch-delivery").value}));}
async function addTxtFiles(files){for(const file of files){e.batchItems.append(makeBatchItem({video_name:file.name.replace(/\.txt$/i,""),script:await file.text()}));}updateBatchCount();}
function parseCsv(text){const rows=[];let row=[],cell="",quoted=false;for(let i=0;i<text.length;i++){const char=text[i];if(char==='"'&&quoted&&text[i+1]==='"'){cell+='"';i++;}else if(char==='"')quoted=!quoted;else if(char===','&&!quoted){row.push(cell);cell="";}else if((char==='\n'||char==='\r')&&!quoted){if(char==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(Boolean))rows.push(row);row=[];cell="";}else cell+=char;}row.push(cell);if(row.some(Boolean))rows.push(row);if(rows.length<2)return[];const head=rows[0].map((value)=>value.trim().toLowerCase());return rows.slice(1).map((values)=>Object.fromEntries(head.map((key,index)=>[key,values[index]||""])));}
async function importBatchCsv(file){const rows=parseCsv(await file.text());rows.forEach((row,index)=>{const voice=voices.find((item)=>item.name.toLowerCase()===(row.voice||"").trim().toLowerCase());e.batchItems.append(makeBatchItem({video_name:row.video_name||"Video "+(index+1),script:row.script||"",voice_id:voice?voice.id:"",speed:Number(row.speed)||1,preset:["turbo","fast","balanced","high"].includes(row.quality)?row.quality:"turbo",delivery_mode:["consistent","conversational","dramatic"].includes(row.delivery)?row.delivery:"consistent"}));});updateBatchCount();}
async function loadBatches(selected){batches=await api("/api/batches");e.batchSelect.replaceChildren();const first=document.createElement("option");first.value="";first.textContent="Recent batches";e.batchSelect.append(first);batches.forEach((item)=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.title+" · "+item.state;e.batchSelect.append(option);});const id=selected||e.batchSelect.value;if(id){e.batchSelect.value=id;showBatch(await api("/api/batches/"+id));}}
function showBatch(item){batch=item;e.batchEmpty.hidden=true;e.batchProgress.hidden=false;e.batchState.textContent=item.state.replaceAll("_"," ").toUpperCase();e.batchFraction.textContent=item.completed_items+" / "+item.total_items;e.batchBar.style.width=(item.total_items?Math.round(item.completed_items/item.total_items*100):0)+"%";e.batchMessage.textContent=item.error||item.message;e.batchResults.replaceChildren();item.items.forEach((row)=>{const card=document.createElement("article");card.className="batch-result "+row.state;const copy=document.createElement("div");const name=document.createElement("strong");name.textContent=row.video_name;const state=document.createElement("small");state.textContent=row.error||row.state;copy.append(name,state);card.append(copy);if(row.audio_url){const link=document.createElement("a");link.href=row.audio_url;link.download="";link.textContent="WAV ↓";card.append(link);}e.batchResults.append(card);});e.downloadBatch.href="/api/batches/"+item.id+"/download";e.downloadBatch.hidden=!item.items.some((row)=>row.state==="succeeded");e.pauseBatch.hidden=!['queued','running','pausing'].includes(item.state);e.resumeBatch.hidden=!['paused','interrupted'].includes(item.state);e.cancelBatch.hidden=!['queued','running','pausing','paused','interrupted'].includes(item.state);e.retryBatch.hidden=!item.items.some((row)=>row.state==='failed');}
async function pollBatch(id){while(true){const item=await api("/api/batches/"+id);showBatch(item);if(["succeeded","completed_with_errors","failed","paused","interrupted","cancelled"].includes(item.state)){loadBatches(id);return;}await new Promise((resolve)=>setTimeout(resolve,1200));}}
async function startBatch(){clearError(e.batchError);const items=batchPayload();if(!items.length||items.some((item)=>!syllables(item.script))){errorAt(e.batchError,"Add at least one video with Burmese script.");return;}e.startBatch.disabled=true;try{const item=await api("/api/batches",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:e.batchTitle.value.trim()||"Video voiceovers",items})});showBatch(item);pollBatch(item.id);await loadBatches(item.id);}catch(err){errorAt(e.batchError,err.message);}finally{e.startBatch.disabled=false;}}
async function controlBatch(action){if(!batch)return;try{const item=await api("/api/batches/"+batch.id+"/"+action,{method:"POST"});showBatch(item);if(action==="resume"||action==="retry")pollBatch(item.id);}catch(err){errorAt(e.batchError,err.message);}}

function showSelectedVideos(){e.videoFileList.replaceChildren();const files=[...e.videoFiles.files];if(!files.length){const row=document.createElement("span");row.textContent="No video selected.";e.videoFileList.append(row);return;}files.forEach((file)=>{const row=document.createElement("span");row.textContent=file.name+" · "+(file.size/1024/1024).toFixed(1)+" MB";e.videoFileList.append(row);});}
async function loadGeminiStatus(){try{const status=await api("/api/settings/gemini");e.geminiBadge.textContent=status.configured?status.count+" encrypted key"+(status.count===1?"":"s"):"Keys not set";e.geminiBadge.classList.toggle("ready",status.configured);e.savedKeyStatus.textContent=status.configured?status.count+" encrypted key(s) saved · "+status.labels.join(" · "):"No keys saved yet.";return status;}catch(err){e.geminiBadge.textContent="Key storage needs attention";throw err;}}
async function openGeminiDialog(){clearError(e.geminiError);e.geminiKeys.value="";await loadGeminiStatus();e.geminiDialog.showModal();}
async function saveGemini(event){event.preventDefault();clearError(e.geminiError);const keys=e.geminiKeys.value.split(/\r?\n/).map((value)=>value.trim()).filter(Boolean);if(!keys.length){errorAt(e.geminiError,"Paste at least one key.");return;}e.saveGeminiKeys.disabled=true;try{await api("/api/settings/gemini",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({keys})});e.geminiKeys.value="";await loadGeminiStatus();notify("Translation keys encrypted locally.");}catch(err){errorAt(e.geminiError,err.message);}finally{e.saveGeminiKeys.disabled=false;}}
async function testGemini(){clearError(e.geminiError);e.testGeminiKeys.disabled=true;e.testGeminiKeys.textContent="Testing…";try{const result=await api("/api/settings/gemini/test",{method:"POST"});notify("Translation connection works · "+result.route);}catch(err){errorAt(e.geminiError,err.message);}finally{e.testGeminiKeys.disabled=false;e.testGeminiKeys.textContent="Test saved keys";}}
async function clearGemini(){if(!confirm("Remove every saved translation key from this computer?"))return;await api("/api/settings/gemini",{method:"DELETE"});await loadGeminiStatus();notify("Saved keys removed.");}

async function startVideoJobs(){
  clearError(e.videoError);unlockAlertAudio();
  const translation_mode=document.querySelector('input[name="videoTranslationMode"]:checked').value;
  if(translation_mode==="gemini"){
    const keyStatus=await loadGeminiStatus();
    if(!keyStatus.configured){errorAt(e.videoError,"Add a Gemini API key, or choose Manual SRT.");return;}
  }
  const common={voice_id:e.videoVoice.value,speed:Number(e.videoSpeed.value),delivery_mode:e.videoDelivery.value,emotion_intensity:.45,output_ratio:e.videoRatio.value,frame_fill:e.videoFill.value,original_audio_volume:.12,ducking:true,caption_burn:false,caption_split:'short',mirror_horizontal:e.mirrorOnImport.checked,translation_mode};
  e.startVideoDub.disabled=true;
  try{
    if(videoSourceMode==="rednote"){
      if(!e.rednoteUrl.value.trim())throw new Error("Paste a RedNote share link.");
      if(!e.rednotePermission.checked)throw new Error("Confirm that you have permission to download and repurpose this video.");
      e.startVideoDub.querySelector("strong").textContent="Connecting…";
      const item=await api("/api/video-jobs/from-url",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...common,url:e.rednoteUrl.value.trim(),permission_confirmed:true,best_quality:true})});
      watchedVideoJobs.add(item.id);e.rednoteUrl.value="";e.rednotePermission.checked=false;await loadVideoJobs(item.id);
      notify("RedNote download started. Partial progress is saved if the connection stops.");
    }else{
      const files=[...e.videoFiles.files];if(!files.length)throw new Error("Choose at least one video.");
      const form=new FormData();files.forEach(file=>form.append("files",file,file.name));form.append("settings",JSON.stringify({...common,preset:"balanced"}));
      e.startVideoDub.querySelector("strong").textContent="Uploading…";
      const result=await api("/api/video-jobs",{method:"POST",body:form});result.jobs.forEach(item=>watchedVideoJobs.add(item.id));
      e.videoFiles.value="";showSelectedVideos();await loadVideoJobs(result.jobs[0]&&result.jobs[0].id);
      notify(result.jobs.length+" video"+(result.jobs.length===1?"":"s")+" added. We’ll alert you when the transcript is ready.");
    }
  }catch(err){errorAt(e.videoError,err.message);}
  finally{e.startVideoDub.disabled=false;e.startVideoDub.querySelector("strong").textContent=videoSourceMode==="rednote"?"Download & transcribe":"Start video";}
}
function renderVideoJobOptions(selectedId=e.videoJobSelect.value){e.videoJobSelect.replaceChildren();const first=document.createElement("option");first.value="";first.textContent=videoJobs.length?"Choose a video":"No jobs yet";e.videoJobSelect.append(first);videoJobs.forEach((item)=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.title+" · "+item.state.replaceAll("_"," ");e.videoJobSelect.append(option);});if(selectedId&&videoJobs.some(item=>item.id===selectedId))e.videoJobSelect.value=selectedId;const counts={};videoJobs.filter(item=>["queued","downloading","transcribing","translating","synthesizing","syncing","rendering"].includes(item.state)).forEach(item=>{const stage=item.stage||item.state;counts[stage]=(counts[stage]||0)+1;});const active=Object.entries(counts).map(([stage,count])=>`${count} ${stage}`).join(" · ");e.videoQueueSummary.textContent=active?`Working in parallel · ${active}`:"Downloads and transcription can run in parallel.";}
async function refreshVideoJobOptions(){try{const selected=e.videoJobSelect.value;videoJobs=await api("/api/video-jobs");renderVideoJobOptions(selected);}catch(_){}}
async function loadVideoJobs(selected){videoJobs=await api("/api/video-jobs");const id=selected||e.videoJobSelect.value||(videoJobs[0]&&videoJobs[0].id);renderVideoJobOptions(id);if(id){await openVideoJob(id);}else{videoJob=null;e.videoEmpty.hidden=false;e.videoJobView.hidden=true;}}
function setVideoStep(stage){const order={source:0,download:0,prepare:0,transcription:0,manual_translation:1,translation:1,review:1,voice:2,smart_sync:2,render_failed:2,complete:3,failed:0,interrupted:0};const current=order[stage]??0;$$('.video-steps li').forEach((node,index)=>{node.classList.toggle("active",index===current);node.classList.toggle("done",index<current);});}
async function openVideoJob(id){const item=await api("/api/video-jobs/"+id);showVideoJob(item);if(!["awaiting_translation","review_ready","succeeded","failed","paused","cancelled","interrupted"].includes(item.state))pollVideoJob(id);}
function overallVideoPercent(item){const fraction=item.total?Math.min(1,item.progress/item.total):0;if(item.state==="downloading"||item.stage==="download")return Math.round(fraction*10);if(item.state==="transcribing"||item.stage==="transcription")return 16;if(item.state==="awaiting_translation"||item.stage==="manual_translation")return 25;if(item.state==="translating"||item.stage==="translation")return Math.round(25+fraction*25);if(item.state==="review_ready"||item.stage==="review")return 50;if(item.state==="synthesizing"||item.stage==="voice")return Math.round(50+fraction*30);if(item.state==="syncing"||item.stage==="smart_sync")return Math.round(80+fraction*19);if(item.state==="failed"&&item.stage==="render_failed")return 99;if(item.state==="succeeded")return 100;return Math.round(fraction*10);}
function showLiveVideoProgress(item){const active=["queued","downloading","transcribing","translating","synthesizing","syncing","rendering","pausing","cancelling"].includes(item.state);e.videoActivity.hidden=!active;e.videoActiveActions.hidden=!active||["pausing","cancelling"].includes(item.state);if(!active)return;const labels={queued:"Waiting for the local worker",download:"Downloading from RedNote",transcription:"Finding source speech and timestamps",translation:"Adapting into natural Burmese",voice:"Creating Burmese narration",smart_sync:"Keeping video at native 1× and fitting the voice",render:"Finishing your video"};e.videoActivityTitle.textContent=labels[item.stage]||labels[item.state]||"Working on your video";const elapsed=Math.max(0,(Date.now()-Date.parse(item.created_at))/1000);const speed=item.download?.speed_bps?" · "+(item.download.speed_bps/1024/1024).toFixed(1)+" MB/s":"";e.videoElapsed.textContent="Elapsed "+timeLabel(elapsed)+speed+" · You can keep using the studio";const stages=["download","transcription","translation","voice","smart_sync"],current=Math.max(0,stages.indexOf(item.stage));$$('[data-activity-stage]').forEach((node,index)=>{node.classList.toggle("active",index===current);node.classList.toggle("done",index<current);});e.videoLiveSegments.replaceChildren();const segments=item.segments||[];if(!segments.length){const placeholder=document.createElement("div");placeholder.className="live-segment";const first=document.createElement("p");first.textContent=item.stage==="download"?`Downloaded ${item.download?.percent||0}%`:item.stage==="transcription"?"Checking platform subtitles, then local speech…":"Preparing the next stage…";const second=document.createElement("p");second.className="waiting-copy";second.textContent=item.stage==="download"?"Partial download is kept for resume":"Progress will appear here automatically";placeholder.append(first,second);e.videoLiveSegments.append(placeholder);return;}segments.slice(0,8).forEach(segment=>{const row=document.createElement("div");row.className="live-segment";const source=document.createElement("p");source.textContent=segment.source_text;const burmese=document.createElement("p");burmese.textContent=segment.translation||"Waiting for Burmese translation…";if(!segment.translation)burmese.className="waiting-copy";row.append(source,burmese);e.videoLiveSegments.append(row);});if(segments.length>8){const more=document.createElement("small");more.className="field-hint";more.textContent="Showing 8 of "+segments.length+" saved segments";e.videoLiveSegments.append(more);}}
function showVideoJob(item){announceVideoMilestone(item);videoJob=item;e.videoEmpty.hidden=true;e.videoJobView.hidden=false;e.videoJobState.textContent=item.state.replaceAll("_"," ").toUpperCase();e.videoJobTitle.textContent=item.title;const percent=overallVideoPercent(item);e.videoJobPercent.textContent=percent+"%";e.videoJobBar.style.width=percent+"%";e.videoJobMessage.textContent=item.state==="failed"?item.message:(item.error||item.message);e.videoFailure.hidden=item.state!=="failed";if(item.state==="failed"){e.videoFailureTitle.textContent=item.error||"This step could not finish.";e.videoFailureMessage.textContent=item.message||"Choose Retry / Resume to continue from saved work.";}setVideoStep(item.stage);showLiveVideoProgress(item);const recover=["failed","paused","interrupted","cancelled"].includes(item.state);e.videoRecovery.hidden=!recover;e.videoReview.hidden=item.state!=="review_ready";e.videoResult.hidden=item.state!=="succeeded";if(item.state==="review_ready"){editorScenes=structuredClone(item.segments||[]);loadEditorSettings();renderVideoSegments(editorScenes);}if(item.state==="succeeded"){e.dubbedVideo.src="/api/video-jobs/"+item.id+"/artifacts/video";e.downloadDubbedVideo.href=e.dubbedVideo.src;$$('[data-artifact]').forEach((link)=>{link.href="/api/video-jobs/"+item.id+"/artifacts/"+link.dataset.artifact;link.download="";});}e.renderVideo.disabled=false;}
function renderVideoSegments(segments=editorScenes){e.videoSegments.replaceChildren();const active=segments.filter(s=>!s.deleted);e.videoSegmentCount.textContent=active.length+" scenes";active.forEach((segment,index)=>{const card=document.createElement("article");card.className="video-segment";card.dataset.id=segment.id;const source=document.createElement("div");source.className="source-copy";const clip=(segment.source_clips||[{start:segment.source_start,end:segment.source_end}])[0];const time=document.createElement("small");time.textContent="SCENE "+(index+1)+" · "+timeLabel(clip.start)+"–"+timeLabel(clip.end);const english=document.createElement("div");english.textContent=segment.source_text;const actions=document.createElement("div");actions.className="scene-actions";[["←","Move earlier"],["→","Move later"],["Delete","Delete scene"]].forEach(([label,name],action)=>{const b=document.createElement("button");b.type="button";b.textContent=label;b.setAttribute("aria-label",name);b.onclick=()=>sceneAction(index,action);actions.append(b);});source.append(time,english,actions);const edit=document.createElement("div");const translation=document.createElement("textarea");translation.value=segment.translation;translation.setAttribute("aria-label","Burmese translation for scene "+(index+1));translation.onchange=()=>changeScene(segment,"translation",translation.value);const controls=document.createElement("div");controls.className="video-segment-controls";const voice=document.createElement("select");fillVoices(voice,segment.voice_id||videoJob.settings.voice_id);voice.setAttribute("aria-label","Online voice");voice.onchange=()=>changeScene(segment,"voice_id",voice.value);const style=document.createElement("select");style.setAttribute("aria-label","Expressive delivery");Object.entries(STYLE_LABELS).forEach(([value,label])=>{const option=document.createElement("option");option.value=value;option.textContent=label;style.append(option);});style.value=segment.style||"neutral";style.onchange=()=>changeScene(segment,"style",style.value);const speed=document.createElement("input");speed.type="number";speed.min=.8;speed.max=2;speed.step=.05;speed.value=segment.scene_speed||1;speed.setAttribute("aria-label","Scene speed");speed.onchange=()=>changeScene(segment,"scene_speed",Number(speed.value));const pause=document.createElement("input");pause.type="number";pause.min=0;pause.max=5000;pause.step=50;pause.value=segment.pause_after_ms??180;pause.setAttribute("aria-label","Pause after scene in milliseconds");pause.onchange=()=>changeScene(segment,"pause_after_ms",Number(pause.value));const trim=document.createElement("div");trim.className="trim-controls";const start=document.createElement("input"),end=document.createElement("input");[start,end].forEach(x=>{x.type="number";x.step=.05;x.min=0;});start.value=clip.start;end.value=clip.end;start.setAttribute("aria-label","Picture in time");end.setAttribute("aria-label","Picture out time");start.onchange=()=>changeClip(segment,"start",Number(start.value));end.onchange=()=>changeClip(segment,"end",Number(end.value));const split=document.createElement("button");split.type="button";split.textContent="Split picture";split.onclick=()=>splitClip(segment);trim.append(start,end,split);const preview=document.createElement("button");preview.type="button";preview.className="preview-scene";preview.textContent=segment.audio_state==="ready"?"Play scene":"Generate preview";preview.onclick=()=>previewScene(segment,preview);controls.append(voice,style,speed,pause);edit.append(translation,controls,trim,preview);card.append(source,edit);e.videoSegments.append(card);});}
async function saveVideoSegment(id,values){try{await api("/api/video-jobs/"+videoJob.id+"/segments/"+id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(values)});}catch(err){errorAt(e.videoError,err.message);}}
function selectedExportMode(){return document.querySelector('input[name="videoExportMode"]:checked')?.value||"voiceover_captions";}
function updateExportModeUi(){const mode=selectedExportMode(),subtitlesOnly=mode==='subtitles_only';e.reviewVideoVoice.disabled=subtitlesOnly;e.audioDucking.disabled=subtitlesOnly;e.originalAudioVolume.disabled=subtitlesOnly;e.renderVideo.querySelector('strong').textContent=subtitlesOnly?'Export subtitle video':'Export approved video';}
function globalCaptionStyle(){return{font:e.captionFont.value,size:Number(e.captionSize.value),color:e.captionColor.value.toUpperCase(),position:e.captionPosition.value,x:Number(e.captionX.value),y:Number(e.captionY.value)};}
function resolvedCaptionStyle(scene){return{...globalCaptionStyle(),...(scene?.caption_style_override||{})};}
function splitCaptionPreview(text,mode=e.captionSplit?.value||'short',duration=4){const clean=String(text||'').replace(/\s+/g,' ').trim();if(!clean||mode==='full')return clean?[clean]:[];const limit=mode==='balanced'?20:12,parts=[];let current='';for(const word of clean.split(' ')){const candidate=(current+' '+word).trim();if(current&&syllables(candidate)>limit){parts.push(current);current=word;}else current=candidate;while(syllables(current)>limit){let count=0,cut=0;for(const [index,char] of [...current].entries()){if(/[\u1000-\u1021]/.test(char))count++;if(count>=limit){cut=index+1;break;}}if(!cut)break;parts.push([...current].slice(0,cut).join('').trim());current=[...current].slice(cut).join('').trim();}}if(current)parts.push(current);const maximum=Math.max(1,Math.floor(Math.max(.1,Number(duration))/.85));while(parts.length>maximum){let pair=0;for(let i=1;i<parts.length-1;i++)if((parts[i].length+parts[i+1].length)<(parts[pair].length+parts[pair+1].length))pair=i;parts.splice(pair,2,(parts[pair]+' '+parts[pair+1]).trim());}return parts.length?parts:[clean];}
function captionSlicesForScene(scene){return splitCaptionPreview(scene?.translation,e.captionSplit?.value,Number(scene?.source_end)-Number(scene?.source_start));}
function applyCaptionPreview(scene=activeVideoScene){if(!e.captionPreview)return;const style=resolvedCaptionStyle(scene);e.captionPreview.style.fontFamily=`"${style.font}",sans-serif`;e.captionPreview.style.color=style.color;e.captionPreview.style.fontSize=Math.max(.78,Number(style.size)/32)+"rem";let x=Number(style.x),y=Number(style.y);if(style.position!=="custom"){x=.5;y={upper:.12,center:.5,lower:.9}[style.position]??.9;}e.captionPreview.style.left=(x*100)+"%";e.captionPreview.style.top=(y*100)+"%";e.captionPreview.style.right="auto";}
function loadEditorSettings(){const s=videoJob.settings||{},b=videoJob.subtitle_blur||{},c=s.caption_style||{};e.sourcePreview.src=videoJob.source_url;e.mirrorHorizontal.checked=!!s.mirror_horizontal;e.sourcePreview.style.transform=s.mirror_horizontal?"scaleX(-1)":"";fillVoices(e.reviewVideoVoice,s.voice_id||e.videoVoice.value);e.originalAudioVolume.value=s.original_audio_volume??.12;e.originalAudioValue.textContent=Math.round(e.originalAudioVolume.value*100)+"%";e.audioDucking.checked=s.ducking!==false;e.captionBurn.checked=s.export_mode!=="voiceover_only";e.captionFont.value=c.font||"Noto Sans Myanmar";e.captionSize.value=Number(c.size||44);e.captionSizeValue.textContent=e.captionSize.value;e.captionColor.value=(c.color||"#FFFFFF").toLowerCase();e.captionSplit.value=s.caption_split||"short";e.captionPosition.value=c.position||"lower";e.captionX.value=Number(c.x??.5);e.captionY.value=Number(c.y??.9);const mode=s.export_mode||"voiceover_captions",radio=document.querySelector(`input[name="videoExportMode"][value="${mode}"]`);if(radio)radio.checked=true;updateExportModeUi();e.blurEnabled.checked=!!b.enabled;e.blurX.value=b.x??.08;e.blurY.value=b.y??.72;e.blurWidth.value=b.width??.84;e.blurHeight.value=b.height??.18;e.blurStrength.value=b.strength??18;e.blurConfidence.textContent=b.status==="detected"?`Detected · ${Math.round((b.confidence||0)*100)}% confidence`:b.status==="possible"?`Possible subtitles · ${Math.round((b.confidence||0)*100)}% confidence`:"No stable subtitle band detected";updateBlurPreview();applyCaptionPreview();e.editSaveState.textContent="Saved";loadExportCheckpoint();}
function editorSnapshot(){editorUndoStack.push(structuredClone(editorScenes));if(editorUndoStack.length>40)editorUndoStack.shift();editorRedoStack=[];}
function scheduleEditSave(){clearTimeout(editSaveTimer);e.editSaveState.textContent="Unsaved changes";editSaveTimer=setTimeout(saveEditPlan,650);}
async function saveEditPlan(){if(!videoJob)return;const subtitle_blur={enabled:e.blurEnabled.checked,x:Number(e.blurX.value),y:Number(e.blurY.value),width:Number(e.blurWidth.value),height:Number(e.blurHeight.value),strength:Number(e.blurStrength.value),confidence:videoJob.subtitle_blur?.confidence||0,status:videoJob.subtitle_blur?.status||"manual"},mode=selectedExportMode();const settings={voice_id:e.reviewVideoVoice.value||videoJob.settings.voice_id,original_audio_volume:Number(e.originalAudioVolume.value),ducking:e.audioDucking.checked,caption_burn:mode!=="voiceover_only",caption_size:String(e.captionSize.value),caption_position:e.captionPosition.value,caption_style:globalCaptionStyle(),caption_split:e.captionSplit.value,export_mode:mode,mirror_horizontal:e.mirrorHorizontal.checked,subtitle_blur};try{videoJob=await api("/api/video-jobs/"+videoJob.id+"/edit-plan",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({scenes:editorScenes,settings})});editorScenes=structuredClone(videoJob.segments||[]);e.editSaveState.textContent="Saved";}catch(err){e.editSaveState.textContent="Save failed";errorAt(e.videoError,err.message);}}
function changeScene(scene,key,value){editorSnapshot();scene[key]=value;scene.audio_state="dirty";scheduleEditSave();}
function changeClip(scene,key,value){editorSnapshot();scene.source_clips=scene.source_clips||[{start:scene.source_start,end:scene.source_end}];scene.source_clips[0][key]=value;scheduleEditSave();}
function splitClip(scene){const clip=(scene.source_clips||[])[0];if(!clip)return;editorSnapshot();const mid=(Number(clip.start)+Number(clip.end))/2;scene.source_clips=[{start:clip.start,end:mid},{start:mid,end:clip.end}];scheduleEditSave();renderVideoSegments();}
function sceneAction(index,action){editorSnapshot();const active=editorScenes.filter(s=>!s.deleted),scene=active[index],real=editorScenes.indexOf(scene);if(action===0&&real>0)[editorScenes[real-1],editorScenes[real]]=[editorScenes[real],editorScenes[real-1]];if(action===1&&real<editorScenes.length-1)[editorScenes[real+1],editorScenes[real]]=[editorScenes[real],editorScenes[real+1]];if(action===2)scene.deleted=true;scheduleEditSave();renderVideoSegments();}
const renderVideoSegmentsBase=renderVideoSegments;
renderVideoSegments=function(segments=editorScenes){renderVideoSegmentsBase(segments);const active=segments.filter(scene=>!scene.deleted);[...e.videoSegments.children].forEach((card,index)=>{const scene=active[index],duplicate=document.createElement("button");duplicate.type="button";duplicate.textContent="Duplicate";duplicate.setAttribute("aria-label","Duplicate scene");duplicate.onclick=async()=>{try{await saveEditPlan();const copy=await api("/api/video-jobs/"+videoJob.id+"/segments/"+scene.id+"/duplicate",{method:"POST"});editorScenes.splice(editorScenes.indexOf(scene)+1,0,copy);scheduleEditSave();renderVideoSegments();}catch(err){errorAt(e.videoError,err.message);}};card.querySelector(".scene-actions").insertBefore(duplicate,card.querySelector(".scene-actions").lastChild);const clips=document.createElement("div");clips.className="clip-list";(scene.source_clips||[]).forEach((clip,clipIndex)=>{const row=document.createElement("span");row.textContent=`Picture ${clipIndex+1}: ${clip.start.toFixed(2)}–${clip.end.toFixed(2)}s`;if((scene.source_clips||[]).length>1){const remove=document.createElement("button");remove.type="button";remove.textContent="Remove";remove.setAttribute("aria-label",`Remove picture range ${clipIndex+1}`);remove.onclick=()=>{editorSnapshot();scene.source_clips.splice(clipIndex,1);scheduleEditSave();renderVideoSegments();};row.append(remove);}clips.append(row);});card.querySelector(".trim-controls").after(clips);});};
async function previewScene(scene,button){button.disabled=true;button.textContent="Generating…";try{await saveEditPlan();await api("/api/video-jobs/"+videoJob.id+"/segments/"+scene.id+"/preview",{method:"POST"});const audio=new Audio("/api/video-jobs/"+videoJob.id+"/segments/"+scene.id+"/audio?t="+Date.now()),clip=(scene.source_clips||[])[0];e.sourcePreview.currentTime=clip.start;e.sourcePreview.volume=Number(e.originalAudioVolume.value);e.captionPreview.textContent=scene.translation;e.captionPreview.className="caption-preview "+e.captionSize.value+" "+e.captionPosition.value;await e.sourcePreview.play();await audio.play();await new Promise(resolve=>{audio.onended=()=>{e.sourcePreview.pause();resolve();};audio.onerror=resolve;});button.textContent="Play scene";}catch(err){errorAt(e.videoError,err.message);button.textContent="Retry preview";}finally{button.disabled=false;}}
async function previewAll(){e.previewTimeline.disabled=true;try{for(const scene of editorScenes.filter(s=>!s.deleted)){const button=e.videoSegments.querySelector(`[data-id="${scene.id}"] .preview-scene`);await previewScene(scene,button);}}finally{e.previewTimeline.disabled=false;}}
function undoEditor(){if(!editorUndoStack.length)return;editorRedoStack.push(structuredClone(editorScenes));editorScenes=editorUndoStack.pop();scheduleEditSave();renderVideoSegments();}
function redoEditor(){if(!editorRedoStack.length)return;editorUndoStack.push(structuredClone(editorScenes));editorScenes=editorRedoStack.pop();scheduleEditSave();renderVideoSegments();}
async function pollVideoJob(id){clearTimeout(videoPoll);try{const item=await api("/api/video-jobs/"+id);if(!videoJob||videoJob.id!==id)return;showVideoJob(item);if(!["awaiting_translation","review_ready","succeeded","failed","paused","cancelled","interrupted"].includes(item.state)){videoPoll=setTimeout(()=>pollVideoJob(id),1300);}else{loadVideoJobs(id);}}catch(_){videoPoll=setTimeout(()=>pollVideoJob(id),2500);}}
async function renderCurrentVideo(){if(!videoJob)return;e.renderVideo.disabled=true;try{await saveEditPlan();const item=await api("/api/video-jobs/"+videoJob.id+"/export",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:selectedExportMode()})});showVideoJob(item);pollVideoJob(item.id);}catch(err){errorAt(e.videoError,err.message);e.renderVideo.disabled=false;}}
async function retryCurrentVideo(){if(!videoJob)return;try{const item=await api("/api/video-jobs/"+videoJob.id+"/retry",{method:"POST"});showVideoJob(item);pollVideoJob(item.id);}catch(err){errorAt(e.videoError,err.message);}}
async function controlCurrentVideo(action){if(!videoJob)return;try{const item=await api("/api/video-jobs/"+videoJob.id+"/"+action,{method:"POST"});showVideoJob(item);pollVideoJob(item.id);}catch(err){errorAt(e.videoError,err.message);}}
async function deleteCurrentVideo(){
  if(!videoJob)return;
  const completed=videoJob.state==="succeeded";
  const prompt=completed
    ? "Delete this completed video? This permanently removes the MP4, source video, previews, subtitles, and local edit data. Download anything you want to keep first."
    : "Delete only this video job and its local files?";
  if(!confirm(prompt))return;
  try{await api("/api/video-jobs/"+videoJob.id,{method:"DELETE"});videoJob=null;await loadVideoJobs();notify("Video deleted from this computer.");}catch(err){errorAt(e.videoError,err.message);}
}

e.ttsText.oninput=updateQuickCount;e.clearText.onclick=()=>{e.ttsText.value="";updateQuickCount();e.ttsText.focus();};e.speed.oninput=()=>{e.speedValue.value=Number(e.speed.value).toFixed(2)+"×";};
e.voiceSelect.onchange=updateVoiceControls;e.quickEmotionMode.onchange=()=>{e.quickStyleBlock.hidden=e.quickEmotionMode.value!=="manual";previewEmotion();};
e.generateButton.onclick=generateQuick;e.refreshLibrary.onclick=loadLibrary;e.installRuntime.onclick=installRuntime;e.quickTab.onclick=()=>studio("quick");e.subtitleTab.onclick=()=>studio("subtitle");e.videoTab.onclick=()=>studio("video");
e.subtitleFile.onchange=importSubtitleFile;e.subtitleText.oninput=updateSubtitleCount;e.subtitleEmotion.onchange=()=>e.subtitleStyleWrap.hidden=e.subtitleEmotion.value!=="manual";e.generateSubtitle.onclick=generateSubtitleVoice;
$("#openVoiceDialog").onclick=()=>{clearError(e.voiceError);e.voiceDialog.showModal();};$("#closeVoiceDialog").onclick=$("#cancelVoice").onclick=()=>e.voiceDialog.close();e.voiceForm.onsubmit=saveVoice;e.deleteVoice.onclick=deleteVoice;
e.voiceFile.onchange=()=>{e.fileLabel.textContent=e.voiceFile.files[0]?e.voiceFile.files[0].name:"Choose a WAV file";voiceBlob=null;};e.uploadTab.onclick=()=>{e.uploadPanel.hidden=false;e.recordPanel.hidden=true;e.uploadTab.classList.add("active");e.recordTab.classList.remove("active");};e.recordTab.onclick=()=>{e.uploadPanel.hidden=true;e.recordPanel.hidden=false;e.recordTab.classList.add("active");e.uploadTab.classList.remove("active");};e.recordButton.onclick=()=>voiceRecorder.toggle().catch((err)=>errorAt(e.voiceError,err.message));
e.manageStyles.onclick=()=>{showSavedStyles();clearError(e.styleError);e.styleDialog.showModal();};$("#closeStyleDialog").onclick=$("#cancelStyle").onclick=()=>e.styleDialog.close();e.styleForm.onsubmit=saveStyle;e.styleFile.onchange=()=>{e.styleFileLabel.textContent=e.styleFile.files[0]?e.styleFile.files[0].name:"Choose a WAV";styleBlob=null;};e.styleRecord.onclick=()=>styleRecorder.toggle().catch((err)=>errorAt(e.styleError,err.message));
e.newProject.onclick=e.emptyNewProject.onclick=openNewProject;$("#closeProjectDialog").onclick=$("#cancelProject").onclick=()=>e.projectDialog.close();e.projectForm.onsubmit=createProject;e.projectSelect.onchange=()=>{if(e.projectSelect.value)openProject(e.projectSelect.value);};e.deleteProject.onclick=deleteProject;
e.addChapter.onclick=()=>{e.chaptersEditor.append(makeChapter({title:"Chapter "+($$(".chapter-card").length+1),text:""}));updateProjectStats();};e.projectFile.onchange=async()=>{if(!project||!e.projectFile.files[0])return;const form=new FormData();form.append("file",e.projectFile.files[0]);try{project=await api("/api/projects/"+project.id+"/import",{method:"POST",body:form});renderChapters(project.chapters);updateProjectStats();}catch(err){errorAt(e.projectError,err.message);}e.projectFile.value="";};
e.saveProject.onclick=()=>saveProject(false);e.analyzeProject.onclick=analyze;e.renderProject.onclick=startRender;e.pauseRender.onclick=()=>controlRender("pause");e.resumeRender.onclick=()=>controlRender(render&&render.state==="failed"?"retry":"resume");e.cancelRender.onclick=()=>controlRender("cancel");
[e.projectTitle,e.projectVoice,e.deliveryMode,e.projectQuality,e.projectSpeed].forEach((node)=>node.addEventListener("change",()=>{updateProjectStats();scheduleSave();}));e.emotionIntensity.oninput=()=>{e.intensityValue.textContent=Math.round(e.emotionIntensity.value*100)+"%";scheduleSave();};
e.newBatch.onclick=()=>{e.batchItems.replaceChildren(makeBatchItem());e.batchTitle.value="My video voiceovers";updateBatchCount();e.batchItems.querySelector(".batch-name").focus();};e.addBatchItem.onclick=()=>{e.batchItems.append(makeBatchItem({video_name:"Video "+($$(".batch-item").length+1)}));updateBatchCount();};e.batchFiles.onchange=async()=>{await addTxtFiles(e.batchFiles.files);e.batchFiles.value="";};e.batchCsv.onchange=async()=>{if(e.batchCsv.files[0])await importBatchCsv(e.batchCsv.files[0]);e.batchCsv.value="";};e.startBatch.onclick=startBatch;e.batchSelect.onchange=async()=>{if(e.batchSelect.value)showBatch(await api("/api/batches/"+e.batchSelect.value));};e.pauseBatch.onclick=()=>controlBatch("pause");e.resumeBatch.onclick=()=>controlBatch("resume");e.cancelBatch.onclick=()=>controlBatch("cancel");e.retryBatch.onclick=()=>controlBatch("retry");

e.videoFiles.onchange=showSelectedVideos;e.startVideoDub.onclick=startVideoJobs;e.videoJobSelect.onchange=()=>{if(e.videoJobSelect.value)openVideoJob(e.videoJobSelect.value);};e.renderVideo.onclick=renderCurrentVideo;e.retryVideo.onclick=retryCurrentVideo;e.deleteVideo.onclick=deleteCurrentVideo;e.deleteCompletedVideo.onclick=deleteCurrentVideo;e.pauseVideo.onclick=()=>controlCurrentVideo("pause");e.cancelVideo.onclick=()=>controlCurrentVideo("cancel");
e.editorUndo.onclick=undoEditor;e.editorRedo.onclick=redoEditor;e.previewTimeline.onclick=previewAll;
[e.audioDucking,e.captionBurn,e.captionSize,e.captionPosition].forEach(node=>node.onchange=scheduleEditSave);
e.originalAudioVolume.oninput=()=>{e.originalAudioValue.textContent=Math.round(e.originalAudioVolume.value*100)+"%";scheduleEditSave();};
document.addEventListener("keydown",event=>{if(!e.videoReview.hidden&&(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="z"){event.preventDefault();event.shiftKey?redoEditor():undoEditor();}if(!e.videoReview.hidden&&(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="y"){event.preventDefault();redoEditor();}});
e.completionSound.checked=localStorage.getItem("completionSound")!=="off";e.completionSound.onchange=()=>{localStorage.setItem("completionSound",e.completionSound.checked?"on":"off");if(e.completionSound.checked){unlockAlertAudio();playAlertSound();notify("Completion sound is on.");}else notify("Completion sound is off.");};
document.addEventListener("pointerdown",unlockAlertAudio,{once:true});document.addEventListener("keydown",unlockAlertAudio,{once:true});
e.videoDropZone.ondragover=(event)=>{event.preventDefault();e.videoDropZone.classList.add("dragging");};e.videoDropZone.ondragleave=()=>e.videoDropZone.classList.remove("dragging");e.videoDropZone.ondrop=(event)=>{event.preventDefault();e.videoDropZone.classList.remove("dragging");const transfer=new DataTransfer();[...event.dataTransfer.files].forEach((file)=>transfer.items.add(file));e.videoFiles.files=transfer.files;showSelectedVideos();};
e.openGeminiKeys.onclick=openGeminiDialog;e.closeGeminiDialog.onclick=()=>e.geminiDialog.close();e.geminiForm.onsubmit=saveGemini;e.testGeminiKeys.onclick=testGemini;e.clearGeminiKeys.onclick=clearGemini;

// Unified Quick Voice mode and fixed-timing, single-voice video editor.
let quickVoiceMode="tts",activeVideoScene=null,videoSourceMode="upload";
function setQuickVoiceMode(mode){quickVoiceMode=mode;const tts=mode==="tts";e.quickStudio.hidden=!tts;e.subtitleStudio.hidden=tts;e.ttsModeButton.classList.toggle("active",tts);e.subtitleTab.classList.toggle("active",!tts);e.ttsModeButton.setAttribute("aria-selected",String(tts));e.subtitleTab.setAttribute("aria-selected",String(!tts));}
studio=function(mode){const quick=mode==="quick",videoMode=mode==="video";e.voiceModeSwitch.hidden=!quick;if(quick)setQuickVoiceMode(quickVoiceMode);else{e.quickStudio.hidden=true;e.subtitleStudio.hidden=true;}e.projectStudio.hidden=true;e.batchStudio.hidden=true;e.videoStudio.hidden=!videoMode;e.quickTab.classList.toggle("active",quick);e.videoTab.classList.toggle("active",videoMode);e.quickTab.setAttribute("aria-selected",String(quick));e.videoTab.setAttribute("aria-selected",String(videoMode));if(videoMode){loadVideoJobs();loadGeminiStatus();}};
e.quickTab.onclick=()=>studio("quick");e.ttsModeButton.onclick=()=>setQuickVoiceMode("tts");e.subtitleTab.onclick=()=>setQuickVoiceMode("sts");e.videoTab.onclick=()=>studio("video");

function voiceName(id){const item=voices.find(voice=>voice.id===id);return item?item.name:id;}
function focusVideoScene(scene,card){activeVideoScene=scene;e.sourcePreview.pause();e.sourcePreview.currentTime=Number(scene.source_start);$$('.video-segment.active').forEach(node=>node.classList.remove('active'));if(card)card.classList.add('active');const slices=captionSlicesForScene(scene);e.captionPreview.querySelector('.caption-burmese').textContent=slices[0]||scene.translation;e.captionPreview.querySelector('.caption-original').textContent=scene.source_text||"Added Burmese cue";applyCaptionPreview(scene);const start=Number(scene.source_start),end=Number(scene.source_end),duration=Math.max(.01,Number(videoJob.duration)||end),bar=e.sceneTimeline.querySelector('.timeline-range');bar.style.left=(start/duration*100)+"%";bar.style.width=(Math.max(.02,end-start)/duration*100)+"%";e.sceneTimeline.querySelector('strong').textContent=`${scene.caption_only?"Caption only":voiceName(e.reviewVideoVoice.value||videoJob.settings.voice_id)} · ${start.toFixed(3)}s–${end.toFixed(3)}s · ${slices.length} subtitle card${slices.length===1?'':'s'}`;}

renderVideoSegments=function(segments=editorScenes){e.videoSegments.replaceChildren();const active=segments.filter(s=>!s.deleted);e.videoSegmentCount.textContent=active.length+" scenes · one voice";active.forEach((segment,index)=>{const card=document.createElement("article");card.className="video-segment";card.dataset.id=segment.id;card.tabIndex=0;card.onclick=event=>{if(!event.target.closest('button,input,select,textarea'))focusVideoScene(segment,card);};card.onfocus=()=>focusVideoScene(segment,card);const source=document.createElement("div");source.className="source-copy";const time=document.createElement("small");time.textContent=`SCENE ${index+1} · CUE ${Number(segment.source_start).toFixed(3)}–${Number(segment.source_end).toFixed(3)}s`;const transcript=document.createElement("div");transcript.textContent=segment.source_text;const actions=document.createElement("div");actions.className="scene-actions";[["←","Move earlier"],["→","Move later"],["Duplicate","Duplicate scene"],["Delete","Delete scene"]].forEach(([label,name],action)=>{const button=document.createElement("button");button.type="button";button.textContent=label;button.setAttribute("aria-label",name);button.onclick=async()=>{if(action<2)sceneAction(index,action);else if(action===3)sceneAction(index,2);else{await saveEditPlan();const copy=await api("/api/video-jobs/"+videoJob.id+"/segments/"+segment.id+"/duplicate",{method:"POST"});editorScenes.splice(editorScenes.indexOf(segment)+1,0,copy);scheduleEditSave();renderVideoSegments();}};actions.append(button);});source.append(time,transcript,actions);const edit=document.createElement("div"),translation=document.createElement("textarea");translation.value=segment.translation;translation.setAttribute("aria-label","Burmese translation for scene "+(index+1));translation.onchange=()=>changeScene(segment,"translation",translation.value);const controls=document.createElement("div");controls.className="video-segment-controls";const style=document.createElement("select");style.setAttribute("aria-label","Expressive delivery");Object.entries(STYLE_LABELS).forEach(([v,l])=>style.add(new Option(l,v)));style.value=segment.style||"neutral";style.onchange=()=>changeScene(segment,"style",style.value);const speed=document.createElement("input");speed.type="number";speed.min=.8;speed.max=2;speed.step=.05;speed.value=segment.scene_speed||1;speed.setAttribute("aria-label","Voice speed before timing fit");speed.onchange=()=>changeScene(segment,"scene_speed",Number(speed.value));controls.append(style,speed);const clip=(segment.source_clips||[{start:segment.source_start,end:segment.source_end}])[0],trim=document.createElement("div");trim.className="trim-controls";const start=document.createElement("input"),end=document.createElement("input");[start,end].forEach(x=>{x.type="number";x.step=.05;x.min=0;});start.value=clip.start;end.value=clip.end;start.setAttribute("aria-label","Picture in time");end.setAttribute("aria-label","Picture out time");start.onchange=()=>changeClip(segment,"start",Number(start.value));end.onchange=()=>changeClip(segment,"end",Number(end.value));const split=document.createElement("button");split.type="button";split.textContent="Split picture";split.onclick=()=>splitClip(segment);trim.append(start,end,split);const clipList=document.createElement("div");clipList.className="clip-list";(segment.source_clips||[]).forEach((part,i)=>{const row=document.createElement("span");row.textContent=`Picture ${i+1}: ${Number(part.start).toFixed(2)}–${Number(part.end).toFixed(2)}s`;clipList.append(row);});const preview=document.createElement("button");preview.type="button";preview.className="preview-scene";preview.textContent=segment.audio_state==="ready"?"Play scene":"Generate & play scene";preview.onclick=()=>previewScene(segment,preview);edit.append(translation,controls,trim,clipList);if(segment.warning){const warning=document.createElement("p");warning.className="timing-warning";warning.textContent=segment.warning;edit.append(warning);}edit.append(preview);card.append(source,edit);e.videoSegments.append(card);});};

const renderSingleVoiceScenes=renderVideoSegments;
renderVideoSegments=function(segments=editorScenes){
  renderSingleVoiceScenes(segments);const active=segments.filter(scene=>!scene.deleted);
  [...e.videoSegments.children].forEach((card,index)=>{
    const scene=active[index],edit=card.lastElementChild,controls=card.querySelector('.video-segment-controls');
    const captionOnly=document.createElement('label');captionOnly.className='caption-only-control';
    const captionCheck=document.createElement('input');captionCheck.type='checkbox';captionCheck.checked=!!scene.caption_only;
    captionCheck.onchange=()=>{changeScene(scene,'caption_only',captionCheck.checked);renderVideoSegments();};
    captionOnly.append(captionCheck,document.createTextNode('Caption only · no narration'));controls.append(captionOnly);
    const styleBox=document.createElement('div');styleBox.className='cue-style-controls';
    const override=scene.caption_style_override||{};
    const fontLabel=document.createElement('label');fontLabel.textContent='Cue font';const font=document.createElement('select');
    ['','Noto Sans Myanmar','Padauk','Pyidaungsu','TharLon','Myanmar Sans Pro'].forEach(value=>font.add(new Option(value||'Use video font',value)));
    font.value=override.font||'';font.onchange=()=>changeCueStyle(scene,'font',font.value);fontLabel.append(font);
    const colorLabel=document.createElement('label');colorLabel.textContent='Cue color';const color=document.createElement('input');color.type='color';color.value=(override.color||globalCaptionStyle().color).toLowerCase();color.oninput=()=>changeCueStyle(scene,'color',color.value.toUpperCase());colorLabel.append(color);
    const sizeLabel=document.createElement('label');sizeLabel.textContent='Cue size';const size=document.createElement('input');size.type='number';size.min='20';size.max='120';size.step='1';size.placeholder='Video size';size.value=override.size||'';size.onchange=()=>changeCueStyle(scene,'size',size.value?Number(size.value):'');sizeLabel.append(size);
    const positionLabel=document.createElement('label');positionLabel.textContent='Cue position';const position=document.createElement('select');[['','Use video position'],['upper','Upper'],['center','Center'],['lower','Lower'],['custom','Custom']].forEach(([value,label])=>position.add(new Option(label,value)));position.value=override.position||'';position.onchange=()=>{changeCueStyle(scene,'position',position.value);renderVideoSegments();};positionLabel.append(position);
    const xLabel=document.createElement('label');xLabel.textContent='Cue X';const cueX=document.createElement('input');cueX.type='number';cueX.min='.05';cueX.max='.95';cueX.step='.01';cueX.placeholder='Video X';cueX.disabled=position.value!=='custom';cueX.value=override.x??'';cueX.onchange=()=>changeCueStyle(scene,'x',cueX.value?Number(cueX.value):'');xLabel.append(cueX);
    const yLabel=document.createElement('label');yLabel.textContent='Cue Y';const cueY=document.createElement('input');cueY.type='number';cueY.min='.05';cueY.max='.95';cueY.step='.01';cueY.placeholder='Video Y';cueY.disabled=position.value!=='custom';cueY.value=override.y??'';cueY.onchange=()=>changeCueStyle(scene,'y',cueY.value?Number(cueY.value):'');yLabel.append(cueY);
    const reset=document.createElement('button');reset.type='button';reset.className='use-global';reset.textContent='Use video subtitle style';reset.onclick=()=>{editorSnapshot();scene.caption_style_override={};scheduleEditSave();renderVideoSegments();if(activeVideoScene?.id===scene.id)applyCaptionPreview(scene);};
    styleBox.append(fontLabel,colorLabel,sizeLabel,positionLabel,xLabel,yLabel,reset);edit.insertBefore(styleBox,edit.querySelector('.preview-scene'));
    const slices=captionSlicesForScene(scene),splitNote=document.createElement('p');splitNote.className='caption-split-note';splitNote.textContent=`${slices.length} short subtitle card${slices.length===1?'':'s'} · narration remains continuous`;styleBox.after(splitNote);
    if(scene.is_user_added){card.classList.add('user-added-cue');card.querySelector('.source-copy div').textContent='Added Burmese sentence';card.querySelector('.trim-controls')?.remove();card.querySelector('.clip-list')?.remove();const timing=document.createElement('div');timing.className='trim-controls';const start=document.createElement('input'),end=document.createElement('input');[start,end].forEach(input=>{input.type='number';input.min='0';input.step='.01';});start.value=scene.source_start;end.value=scene.source_end;start.setAttribute('aria-label','Subtitle start time');end.setAttribute('aria-label','Subtitle end time');start.onchange=()=>changeAddedCueTime(scene,'source_start',Number(start.value));end.onchange=()=>changeAddedCueTime(scene,'source_end',Number(end.value));const label=document.createElement('span');label.textContent='Subtitle timing';timing.append(start,end,label);controls.after(timing);}
    if(scene.caption_only){const preview=card.querySelector('.preview-scene');preview.textContent='Preview caption timing';preview.onclick=()=>previewCaptionOnly(scene,preview);}
  });
};

function changeCueStyle(scene,key,value){editorSnapshot();scene.caption_style_override={...(scene.caption_style_override||{})};if(value)scene.caption_style_override[key]=value;else delete scene.caption_style_override[key];scheduleEditSave();if(activeVideoScene?.id===scene.id)applyCaptionPreview(scene);}
async function changeAddedCueTime(scene,key,value){const previous=scene[key];scene[key]=value;try{const updated=await api(`/api/video-jobs/${videoJob.id}/segments/${scene.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({[key]:value})});Object.assign(scene,updated);scheduleEditSave();focusVideoScene(scene,e.videoSegments.querySelector(`[data-id="${scene.id}"]`));}catch(err){scene[key]=previous;errorAt(e.videoError,err.message);renderVideoSegments();}}
async function previewCaptionOnly(scene,button){button.disabled=true;focusVideoScene(scene,e.videoSegments.querySelector(`[data-id="${scene.id}"]`));e.sourcePreview.currentTime=Number(scene.source_start);e.sourcePreview.playbackRate=1;try{await e.sourcePreview.play();await new Promise(resolve=>{const stop=()=>{if(e.sourcePreview.currentTime>=Number(scene.source_end)){e.sourcePreview.pause();e.sourcePreview.removeEventListener('timeupdate',stop);resolve();}};e.sourcePreview.addEventListener('timeupdate',stop);});}finally{button.disabled=false;}}

previewScene=async function(scene,button){button.disabled=true;button.textContent="Generating…";try{await saveEditPlan();const updated=await api("/api/video-jobs/"+videoJob.id+"/segments/"+scene.id+"/preview",{method:"POST"});Object.assign(scene,updated);const audio=new Audio("/api/video-jobs/"+videoJob.id+"/segments/"+scene.id+"/audio?t="+Date.now());e.sourcePreview.pause();e.sourcePreview.playbackRate=1;e.sourcePreview.currentTime=Number(scene.source_start);e.sourcePreview.volume=Number(e.originalAudioVolume.value);focusVideoScene(scene,e.videoSegments.querySelector(`[data-id="${scene.id}"]`));await Promise.allSettled([e.sourcePreview.play(),audio.play()]);await new Promise(resolve=>{const stop=()=>{if(e.sourcePreview.currentTime>=Number(scene.source_end)||e.sourcePreview.ended){e.sourcePreview.pause();audio.pause();e.sourcePreview.removeEventListener("timeupdate",stop);resolve();}};e.sourcePreview.addEventListener("timeupdate",stop);audio.onerror=()=>{e.sourcePreview.removeEventListener("timeupdate",stop);resolve();};});button.textContent="Play scene";renderVideoSegments();}catch(err){errorAt(e.videoError,err.message);button.textContent="Retry preview";}finally{button.disabled=false;}};

const showVideoJobOriginal=showVideoJob;
function manualDraftKey(id){return "manual-srt-draft:"+id;}
function manualCueCount(text){return (text.match(/^\s*\d+\s*$/gm)||[]).length;}
function updateManualSrtStatus(){
  const found=manualCueCount(e.manualSrtText.value),expected=Number(videoJob?.expected_cue_count||videoJob?.segments?.length||0);
  e.manualSrtCount.textContent=found+" cue"+(found===1?"":"s")+" found";
  const ready=found===expected&&expected>0;
  e.manualSrtStatus.textContent=ready?"Cue count matches":found?`Expected ${expected} cues`:"Waiting for SRT";
  e.manualSrtStatus.className=ready?"valid":found?"invalid":"";
  e.importManualSrt.disabled=!e.manualSrtText.value.trim();
  if(videoJob)localStorage.setItem(manualDraftKey(videoJob.id),e.manualSrtText.value);
}
async function copyText(value,label){
  try{await navigator.clipboard.writeText(value);notify(label+" copied.");}
  catch(_){const area=document.createElement("textarea");area.value=value;document.body.append(area);area.select();document.execCommand("copy");area.remove();notify(label+" copied.");}
}
async function sourceSrtText(){const response=await fetch("/api/video-jobs/"+videoJob.id+"/artifacts/source-srt");if(!response.ok)throw new Error("Source SRT is not ready.");return response.text();}
async function importManualTranslation(){
  if(!videoJob)return;clearError(e.manualSrtError);e.importManualSrt.disabled=true;e.importManualSrt.textContent="Validating every cue…";
  try{
    const item=await api("/api/video-jobs/"+videoJob.id+"/translation/srt",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({srt:e.manualSrtText.value})});
    localStorage.removeItem(manualDraftKey(videoJob.id));e.manualSrtText.value="";showVideoJob(item);notify("Burmese SRT imported. Review the scenes before export.");e.videoReview.scrollIntoView({behavior:"smooth",block:"start"});
  }catch(err){errorAt(e.manualSrtError,err.message);}
  finally{e.importManualSrt.textContent="Import SRT & open editor";updateManualSrtStatus();}
}
async function switchToManualTranslation(){
  if(!videoJob)return;
  try{const item=await api("/api/video-jobs/"+videoJob.id+"/translation/manual",{method:"POST"});showVideoJob(await api("/api/video-jobs/"+item.id));e.manualTranslation.scrollIntoView({behavior:"smooth",block:"start"});}
  catch(err){errorAt(e.videoError,err.message);}
}
function showManualTranslation(item){
  const hasTranscript=(item.segments||[]).length>0;
  const visible=item.state==="awaiting_translation"||(item.state==="failed"&&hasTranscript);
  e.manualTranslation.hidden=!visible;
  if(!visible)return;
  e.manualExpected.textContent=(item.expected_cue_count||item.segments.length)+" cues";
  e.downloadSourceSrt.href=item.source_srt_url;
  e.switchManual.hidden=!(item.state==="failed"&&item.translation_mode==="gemini");
  const draft=localStorage.getItem(manualDraftKey(item.id))||"";
  if(e.manualSrtText.dataset.job!==item.id){e.manualSrtText.dataset.job=item.id;e.manualSrtText.value=draft;clearError(e.manualSrtError);}
  updateManualSrtStatus();
}
showVideoJob=function(item){
  const reopening=item.state==="review_ready"&&videoJob&&videoJob.id===item.id;
  showVideoJobOriginal(item);showManualTranslation(item);
  if(item.state==="review_ready"){
    videoJob=item;editorScenes=structuredClone(item.segments||[]);loadEditorSettings();renderVideoSegments();
    if(!reopening&&editorScenes.length)focusVideoScene(editorScenes[0],e.videoSegments.firstElementChild);
  }
  if(item.output_filename){e.videoResult.hidden=false;e.dubbedVideo.src="/api/video-jobs/"+item.id+"/artifacts/video?rev="+(item.export_revision||0);e.downloadDubbedVideo.href="/api/video-jobs/"+item.id+"/artifacts/video";$$('[data-artifact]').forEach(link=>{link.href="/api/video-jobs/"+item.id+"/artifacts/"+link.dataset.artifact;link.download="";});const audioLink=document.querySelector('[data-artifact="audio"]');if(audioLink)audioLink.hidden=item.settings?.export_mode==="subtitles_only";}
};
e.editAgain.onclick=async()=>{if(!videoJob)return;try{const item=await api("/api/video-jobs/"+videoJob.id+"/edit",{method:"POST"});showVideoJob(await api("/api/video-jobs/"+item.id));e.videoReview.scrollIntoView({behavior:"smooth",block:"start"});}catch(err){errorAt(e.videoError,err.message);}};
e.manualSrtText.oninput=updateManualSrtStatus;
e.manualSrtFile.onchange=async()=>{if(e.manualSrtFile.files[0]){e.manualSrtText.value=await e.manualSrtFile.files[0].text();updateManualSrtStatus();}e.manualSrtFile.value="";};
e.copySourceTranscript.onclick=()=>copyText((videoJob.segments||[]).map((segment,index)=>(index+1)+". "+segment.source_text).join("\n\n"),"Numbered transcript");
e.copySourceSrt.onclick=async()=>{try{await copyText(await sourceSrtText(),"SRT template");}catch(err){errorAt(e.manualSrtError,err.message);}};
e.importManualSrt.onclick=importManualTranslation;e.switchManual.onclick=switchToManualTranslation;e.replaceWithSrt.onclick=switchToManualTranslation;

function setVideoSourceMode(mode){videoSourceMode=mode;const upload=mode==="upload";e.uploadSourcePanel.hidden=!upload;e.rednoteSourcePanel.hidden=upload;e.uploadSourceMode.classList.toggle("active",upload);e.rednoteSourceMode.classList.toggle("active",!upload);e.uploadSourceMode.setAttribute("aria-selected",String(upload));e.rednoteSourceMode.setAttribute("aria-selected",String(!upload));e.startVideoDub.querySelector("strong").textContent=upload?"Start video":"Download & transcribe";}
e.uploadSourceMode.onclick=()=>setVideoSourceMode("upload");
e.rednoteSourceMode.onclick=()=>setVideoSourceMode("rednote");

function blurValues(){return{x:Number(e.blurX.value||0),y:Number(e.blurY.value||0),width:Number(e.blurWidth.value||.02),height:Number(e.blurHeight.value||.02)};}
function updateBlurPreview(){if(!e.blurPreview)return;const b=blurValues(),x=e.mirrorHorizontal.checked?1-b.x-b.width:b.x;e.blurPreview.hidden=!e.blurEnabled.checked;e.blurPreview.style.left=(x*100)+"%";e.blurPreview.style.top=(b.y*100)+"%";e.blurPreview.style.width=(b.width*100)+"%";e.blurPreview.style.height=(b.height*100)+"%";}
function changeBlurPosition(x,y){const b=blurValues();e.blurX.value=Math.max(0,Math.min(1-b.width,x)).toFixed(3);e.blurY.value=Math.max(0,Math.min(1-b.height,y)).toFixed(3);updateBlurPreview();scheduleEditSave();}
[e.blurEnabled,e.blurX,e.blurY,e.blurWidth,e.blurHeight,e.blurStrength].forEach(node=>node.addEventListener(node.type==="checkbox"?"change":"input",()=>{updateBlurPreview();scheduleEditSave();}));
e.mirrorHorizontal.addEventListener("change",()=>{e.sourcePreview.style.transform=e.mirrorHorizontal.checked?"scaleX(-1)":"";updateBlurPreview();scheduleEditSave();});
e.reviewVideoVoice.onchange=()=>{editorScenes.forEach(scene=>scene.audio_state="dirty");scheduleEditSave();renderVideoSegments();};
e.redetectBlur.onclick=async()=>{if(!videoJob)return;e.redetectBlur.disabled=true;e.blurConfidence.textContent="Detecting source subtitles…";try{videoJob.subtitle_blur=await api("/api/video-jobs/"+videoJob.id+"/detect-subtitle-region",{method:"POST"});loadEditorSettings();}catch(err){errorAt(e.videoError,err.message);}finally{e.redetectBlur.disabled=false;}};
e.blurPreview.onkeydown=event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;event.preventDefault();const step=event.shiftKey?0.02:0.005,b=blurValues();changeBlurPosition(b.x+(event.key==="ArrowRight"?step:event.key==="ArrowLeft"?-step:0),b.y+(event.key==="ArrowDown"?step:event.key==="ArrowUp"?-step:0));};
e.blurPreview.onpointerdown=event=>{event.preventDefault();e.blurPreview.setPointerCapture(event.pointerId);const frame=e.blurPreview.parentElement.getBoundingClientRect(),b=blurValues(),offsetX=event.clientX-frame.left-b.x*frame.width,offsetY=event.clientY-frame.top-b.y*frame.height;const move=moveEvent=>changeBlurPosition((moveEvent.clientX-frame.left-offsetX)/frame.width,(moveEvent.clientY-frame.top-offsetY)/frame.height);const stop=()=>{e.blurPreview.removeEventListener("pointermove",move);e.blurPreview.removeEventListener("pointerup",stop);};e.blurPreview.addEventListener("pointermove",move);e.blurPreview.addEventListener("pointerup",stop);};

async function loadExportCheckpoint(){if(!videoJob||!e.exportCheckpoint)return;try{const status=await api(`/api/video-jobs/${videoJob.id}/exports/current`);if(status.state==='not_started'){e.exportCheckpoint.hidden=true;return;}const fraction=status.total?`${status.progress} of ${status.total} chunks`:status.stage;e.exportCheckpoint.textContent=`${status.message||'Export saved'} · ${fraction}${status.encoder?' · '+(status.encoder==='h264_nvenc'?'RTX NVENC':'CPU encoder'):''}${status.eta_seconds?` · about ${timeLabel(status.eta_seconds)} remaining`:''}`;e.exportCheckpoint.hidden=false;}catch(_){e.exportCheckpoint.hidden=true;}}
function updateGlobalCaptionControls(){e.captionSizeValue.textContent=e.captionSize.value;if(e.captionPosition.value!=="custom"){e.captionX.value=.5;e.captionY.value={upper:.12,center:.5,lower:.9}[e.captionPosition.value]??.9;}applyCaptionPreview();scheduleEditSave();}
[e.captionFont,e.captionColor,e.captionPosition].forEach(node=>node.addEventListener('change',updateGlobalCaptionControls));
[e.captionSize,e.captionX,e.captionY].forEach(node=>node.addEventListener('input',()=>{if(node===e.captionX||node===e.captionY)e.captionPosition.value='custom';updateGlobalCaptionControls();}));
e.captionSplit.addEventListener('change',()=>{if(activeVideoScene)focusVideoScene(activeVideoScene,e.videoSegments.querySelector(`[data-id="${activeVideoScene.id}"]`));renderVideoSegments();scheduleEditSave();});
e.sourcePreview.addEventListener('timeupdate',()=>{if(!activeVideoScene)return;const slices=captionSlicesForScene(activeVideoScene);if(!slices.length)return;const start=Number(activeVideoScene.source_start),duration=Math.max(.1,Number(activeVideoScene.source_end)-start),progress=Math.min(.9999,Math.max(0,(e.sourcePreview.currentTime-start)/duration)),index=Math.min(slices.length-1,Math.floor(progress*slices.length));e.captionPreview.querySelector('.caption-burmese').textContent=slices[index];});
document.querySelectorAll('input[name="videoExportMode"]').forEach(node=>node.addEventListener('change',()=>{updateExportModeUi();scheduleEditSave();}));
function setCaptionPosition(x,y){e.captionPosition.value='custom';e.captionX.value=Math.min(.95,Math.max(.05,x)).toFixed(2);e.captionY.value=Math.min(.95,Math.max(.05,y)).toFixed(2);applyCaptionPreview();scheduleEditSave();}
e.captionPreview.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const step=event.shiftKey ? .03 : .01,x=Number(e.captionX.value),y=Number(e.captionY.value);setCaptionPosition(x+(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0),y+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0));};
e.captionPreview.onpointerdown=event=>{if(event.target.closest('.caption-original'))return;event.preventDefault();e.captionPreview.setPointerCapture(event.pointerId);const frame=e.captionPreview.parentElement.getBoundingClientRect();const move=moveEvent=>setCaptionPosition((moveEvent.clientX-frame.left)/frame.width,(moveEvent.clientY-frame.top)/frame.height);const stop=()=>{e.captionPreview.removeEventListener('pointermove',move);e.captionPreview.removeEventListener('pointerup',stop);};e.captionPreview.addEventListener('pointermove',move);e.captionPreview.addEventListener('pointerup',stop);};

e.addCaptionCue.onclick=()=>{clearError(e.newCueError);const current=activeVideoScene||editorScenes.filter(scene=>!scene.deleted).at(-1);e.newCueStart.value=current?Number(current.source_end).toFixed(2):'0.00';e.newCueEnd.value=current?Math.min(Number(videoJob.duration||Infinity),Number(current.source_end)+2).toFixed(2):'2.00';e.newCueText.value='';e.newCueCaptionOnly.checked=false;e.captionCueDialog.showModal();};
e.closeCaptionCue.onclick=e.cancelCaptionCue.onclick=()=>e.captionCueDialog.close();
e.captionCueForm.onsubmit=async event=>{event.preventDefault();clearError(e.newCueError);try{const cue=await api(`/api/video-jobs/${videoJob.id}/segments`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:Number(e.newCueStart.value),end:Number(e.newCueEnd.value),translation:e.newCueText.value,caption_only:e.newCueCaptionOnly.checked})});editorScenes.push(cue);editorScenes.sort((a,b)=>Number(a.source_start)-Number(b.source_start));e.captionCueDialog.close();scheduleEditSave();renderVideoSegments();focusVideoScene(cue,e.videoSegments.querySelector(`[data-id="${cue.id}"]`));notify('Timed Burmese sentence added.');}catch(err){errorAt(e.newCueError,err.message);}};

e.batchItems.append(makeBatchItem());updateBatchCount();updateQuickCount();showSelectedVideos();setVideoSourceMode("upload");setQuickVoiceMode("tts");Promise.all([loadStatus(),loadVoices(),loadLibrary(),loadGeminiStatus()]).catch(()=>{});setInterval(loadStatus,3000);setInterval(()=>{if(!e.videoStudio.hidden)refreshVideoJobOptions();},2500);
