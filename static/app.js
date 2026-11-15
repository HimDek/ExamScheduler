const $ = id => document.getElementById(id);
let allRows = [];
let latestResult = null;
let activeJobId = null;
let pollHandle = null;

const sample = {
  students:[{roll_no:"S001"},{roll_no:"S002"},{roll_no:"S003"},{roll_no:"S004"}],
  faculty:[{faculty_id:"F01",name:"Dr. A Sharma"},{faculty_id:"F02",name:"Prof. B Das"}],
  rooms:[{room_no:"R101",capacity:2},{room_no:"R102",capacity:2}],
  subjects:[{subject_code:"CS101",name:"Data Structures"},{subject_code:"CS102",name:"Database Systems"},{subject_code:"CS103",name:"Operating Systems"}],
  enrollments:[
    {roll_no:"S001",subject_code:"CS101"},{roll_no:"S002",subject_code:"CS101"},
    {roll_no:"S001",subject_code:"CS102"},{roll_no:"S003",subject_code:"CS102"},
    {roll_no:"S004",subject_code:"CS103"},{roll_no:"S002",subject_code:"CS103"}
  ]
};

$("sampleBtn").onclick = () => {
  const blob = new Blob([JSON.stringify(sample,null,2)],{type:"application/json"});
  const file = new File([blob],"sample.json",{type:"application/json"});
  loadFile(file);
};

async function loadFile(file) {
  if (!file) return;
  try {
    const text = await file.text();
    if (!text.trim()) throw new Error("The selected file is empty.");
    JSON.parse(text);
    $("jsonFile").dataset.content = text;
    $("fileName").textContent = file.name;
    $("importStatus").textContent = "JSON loaded. Click Import data.";
    $("importStatus").className = "status ok";
  } catch(e) {
    $("jsonFile").dataset.content = "";
    $("importStatus").textContent = "Invalid JSON: " + e.message;
    $("importStatus").className = "status error";
  }
}
$("jsonFile").addEventListener("change", e => loadFile(e.target.files[0]));
const dropZone = $("dropZone");
["dragenter","dragover"].forEach(n=>dropZone.addEventListener(n,e=>{e.preventDefault();dropZone.classList.add("dragging");}));
["dragleave","drop"].forEach(n=>dropZone.addEventListener(n,e=>{e.preventDefault();dropZone.classList.remove("dragging");}));
dropZone.addEventListener("drop",e=>loadFile(e.dataTransfer.files[0]));

$("importBtn").onclick = async () => {
  try {
    const raw = $("jsonFile").dataset.content;
    if (!raw) throw new Error("Choose a valid JSON file first.");
    const data = JSON.parse(raw);
    const res = await fetch("/api/data",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Import failed.");
    $("importStatus").textContent = out.message;
    $("importStatus").className = "status ok";
    latestResult = null; allRows = []; render();
    $("csvBtn").disabled = true;
    await loadStats();
    $("generationStatus").textContent = "Data imported. Start optimization when ready.";
    $("generationStatus").className = "notice";
  } catch(e) {
    $("importStatus").textContent = e.message;
    $("importStatus").className = "status error";
  }
};

function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function renderHistory(history) {
  const el = $("historyView");
  const chart = $("historyChart");
  const rows = history || [];
  if (!rows.length) {
    chart.innerHTML = "";
    chart.hidden = true;
    el.className = "history-empty";
    el.textContent = "No feasible schedule found yet.";
    return;
  }

  chart.hidden = false;
  const width = 800, height = 300, left = 48, right = 18, top = 16, bottom = 32;
  const minX = Math.min(...rows.map(r => Number(r.elapsed)));
  const maxX = Math.max(...rows.map(r => Number(r.elapsed)));
  const minY = Math.min(...rows.map(r => Number(r.slots)));
  const maxY = Math.max(...rows.map(r => Number(r.slots)));
  const x = v => left + (maxX === minX ? 0.5 : (Number(v) - minX) / (maxX - minX)) * (width-left-right);
  const y = v => top + (maxY === minY ? 0.5 : (maxY - Number(v)) / (maxY-minY)) * (height-top-bottom);
  const points = rows.map(r => `${x(r.elapsed)},${y(r.slots)}`).join(" ");
  const circles = rows.map(r => `<circle cx="${x(r.elapsed)}" cy="${y(r.slots)}" r="4"><title>${formatDuration(r.elapsed)} · ${r.slots} slots</title></circle>`).join("");
  chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Best schedule slot count over elapsed optimization time">
    <line x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}" class="history-axis"/>
    <line x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}" class="history-axis"/>
    <polyline points="${points}" class="history-line"/>${circles}
    <text x="${left}" y="${height-9}" class="history-label">${formatDuration(minX)}</text>
    <text x="${width-right}" y="${height-9}" text-anchor="end" class="history-label">${formatDuration(maxX)}</text>
    <text x="${left-8}" y="${top+4}" text-anchor="end" class="history-label">${maxY}</text>
    <text x="${left-8}" y="${height-bottom+4}" text-anchor="end" class="history-label">${minY}</text>
    <text x="${width/2}" y="${height-1}" text-anchor="middle" class="history-label">Elapsed time</text>
  </svg>`;
  el.className = "history-table-wrap";
  el.innerHTML = `<table class="history-table">
    <thead><tr><th>#</th><th>Elapsed time</th><th>Best schedule (slots)</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr>
      <td>${i + 1}</td><td>${formatDuration(r.elapsed)}</td>
      <td><strong>${Number(r.slots)}</strong></td>
    </tr>`).join("")}</tbody>
  </table>`;
}

function showSchedule(schedule) {
  allRows = schedule || [];
  if (allRows.length) {
    populateFilters();
    $("csvBtn").disabled = false;
    render();
  }
}

function setRunning(running, stopping=false) {
  $("generateBtn").disabled = running;
  $("stopBtn").hidden = !running;
  $("stopBtn").disabled = stopping || !running;
  $("stopBtn").textContent = stopping ? "Stopping…" : "Stop optimization";
}

$("generateBtn").onclick = async () => {
  $("generateBtn").disabled = true;
  $("generationStatus").textContent = "Starting continuous CP-SAT optimization…";
  $("generationStatus").className = "notice";
  $("elapsedTimer").textContent = "00:00:00";
  $("bestSlots").textContent = "Searching…";
  $("bestFoundAt").textContent = "—";
  renderHistory([]);
  latestResult = null; allRows = []; render();
  $("csvBtn").disabled = true;
  try {
    const res = await fetch("/api/schedule/start", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({workers:Number($("workers").value)})
    });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Could not start optimization.");
    activeJobId = out.job_id;
    setRunning(true);
    await pollStatus();
    if (activeJobId) pollHandle = setInterval(pollStatus, 1000);
  } catch(e) {
    $("generationStatus").textContent = e.message;
    $("generationStatus").className = "notice error";
    setRunning(false);
  }
};

$("stopBtn").onclick = async () => {
  if (!activeJobId) return;
  $("stopBtn").disabled = true;
  $("stopBtn").textContent = "Stopping…";
  try {
    const res = await fetch(`/api/schedule/stop/${activeJobId}`, {method:"POST"});
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Could not stop optimization.");
    $("generationStatus").textContent = "Stop requested. Saving the current best schedule…";
    $("generationStatus").className = "notice warning";
  } catch(e) {
    $("generationStatus").textContent = e.message;
    $("generationStatus").className = "notice error";
    $("stopBtn").disabled = false;
  }
};

async function pollStatus() {
  if (!activeJobId) return;
  try {
    const res = await fetch(`/api/schedule/status/${activeJobId}`, {cache:"no-store"});
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Could not read optimization status.");
    $("elapsedTimer").textContent = formatDuration(out.elapsed);
    $("bestSlots").textContent = out.slots == null ? "Not found yet" : `${out.slots} slots`;
    $("bestFoundAt").textContent = out.best_found_elapsed == null ? "—" : formatDuration(out.best_found_elapsed);
    renderHistory(out.history || []);
    if (out.schedule && out.slots != null) showSchedule(out.schedule);

    const running = ["RUNNING", "STOPPING"].includes(out.status);
    setRunning(running, out.status === "STOPPING");
    if (out.status === "RUNNING") {
      const bound = out.best_bound == null ? "" : ` Best bound: ${Number(out.best_bound).toFixed(2)}.`;
      $("generationStatus").innerHTML = out.slots == null
        ? "<b>SEARCHING</b> · No feasible schedule found yet."
        : `<b>FEASIBLE</b> · Current best: ${out.slots} time slots.${bound}`;
      $("generationStatus").className = "notice";
    } else if (out.status === "STOPPING") {
      $("generationStatus").textContent = "Stop requested. Saving the current best schedule…";
      $("generationStatus").className = "notice warning";
    } else {
      $("generationStatus").textContent = out.message || out.error || out.status;
      $("generationStatus").className = out.status === "FAILED" ? "notice error" : (out.optimal ? "notice success" : "notice warning");
      if (out.best_bound != null) $("generationStatus").textContent += ` Best bound: ${Number(out.best_bound).toFixed(2)}.`;
      clearInterval(pollHandle);
      pollHandle = null;
      activeJobId = null;
      setRunning(false);
      if (out.slots != null) {
        latestResult = out;
        $("csvBtn").disabled = !allRows.length;
      }
    }
  } catch(e) {
    $("generationStatus").textContent = e.message;
    $("generationStatus").className = "notice error";
  }
};

async function loadStats() {
  const r=await fetch("/api/summary"),s=await r.json();
  ["students","faculty","rooms","subjects","enrollments"].forEach(k=>$(k+"Count").textContent=s[k]);
}
function escapeHtml(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function setOptions(id,values,label){
  const el=$(id),old=el.value;
  el.innerHTML=`<option value="">All ${label}</option>`+[...new Set(values)].sort().map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  el.value=old;
}
function populateFilters(){
  setOptions("slotFilter",allRows.map(x=>String(x.slot_id)),"slots");
  setOptions("roomFilter",allRows.map(x=>x.room_no),"rooms");
  setOptions("subjectFilter",allRows.flatMap(x=>Object.keys(x.subjects)),"subjects");
  setOptions("facultyFilter",allRows.map(x=>x.faculty_id).filter(Boolean),"faculty");
}
["slotFilter","roomFilter","subjectFilter","facultyFilter","studentFilter"].forEach(id=>$(id).addEventListener("input",render));

function render(){
  const slot=$("slotFilter").value,room=$("roomFilter").value,subject=$("subjectFilter").value;
  const faculty=$("facultyFilter").value,student=$("studentFilter").value.trim().toLowerCase();
  const rows=allRows.filter(x=>
    (!slot||String(x.slot_id)===slot)&&(!room||x.room_no===room)&&(!faculty||x.faculty_id===faculty)&&
    (!subject||x.subjects[subject])&&(!student||x.students.some(s=>s.toLowerCase().includes(student))&&
      (!subject||x.subjects[subject].students.some(s=>s.toLowerCase().includes(student))))
  );
  $("scheduleSummary").textContent=allRows.length?`${rows.length} room-slot assignments shown.`:"No schedule generated.";
  if(!rows.length){$("scheduleView").innerHTML='<div class="empty"><strong>No matching assignments</strong><span>Try clearing some filters.</span></div>';return;}
  const slots=[...new Set(rows.map(x=>x.slot_id))].sort((a,b)=>a-b);
  $("scheduleView").innerHTML=slots.map(n=>{
    const cards=rows.filter(x=>x.slot_id===n);
    return `<div class="slot-block"><div class="slot-title">TIME SLOT ${n}</div><div class="room-grid">${
      cards.map(x=>{
        const subjectRows=Object.values(x.subjects).filter(s=>!subject||s.subject_code===subject);
        return `<article class="room-card"><div class="room-top"><b>Room ${escapeHtml(x.room_no)}</b><span class="pill">${x.students.length} / ${x.capacity} seats</span></div>
          <div class="meta">Invigilator: <b>${escapeHtml(x.faculty_name||"Not assigned")}</b> (${escapeHtml(x.faculty_id||"—")})</div>
          ${subjectRows.map(s=>`<div class="subject"><b>${escapeHtml(s.subject_code)}</b> · ${escapeHtml(s.subject_name)}<div class="student-list">${s.students.map(escapeHtml).join(" · ")}</div></div>`).join("")}
        </article>`;
      }).join("")
    }</div></div>`;
  }).join("");
}

$("csvBtn").onclick=()=>{
  const rows=[["Time Slot","Room","Capacity","Faculty ID","Faculty Name","Subject Code","Subject Name","Student Roll No"]];
  allRows.forEach(room=>Object.values(room.subjects).forEach(sub=>
    sub.students.forEach(roll=>rows.push([room.slot_id,room.room_no,room.capacity,room.faculty_id,room.faculty_name,sub.subject_code,sub.subject_name,roll]))
  ));
  const csv=rows.map(r=>r.map(v=>`"${String(v??"").replace(/"/g,'""')}"`).join(",")).join("\r\n");
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="exam_schedule.csv";a.click();URL.revokeObjectURL(a.href);
};
async function restoreCurrentJob() {
  try {
    const res = await fetch("/api/schedule/current", {cache: "no-store"});
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Could not restore scheduler state.");

    if (out.slots != null) $("bestSlots").textContent = `${out.slots} slots`;
    else $("bestSlots").textContent = "Not found yet";
    $("bestFoundAt").textContent = out.best_found_elapsed == null
      ? "—" : formatDuration(out.best_found_elapsed);
    $("elapsedTimer").textContent = formatDuration(out.elapsed);
    renderHistory(out.history || []);
    if (out.schedule) showSchedule(out.schedule);

    if (["RUNNING", "STOPPING"].includes(out.status)) {
      activeJobId = out.id;
      setRunning(true, out.status === "STOPPING");
      $("generationStatus").textContent = out.status === "STOPPING"
        ? "Stop requested. Saving the current best schedule…"
        : (out.slots == null
          ? "Optimization is running. No feasible schedule found yet."
          : `Optimization is running. Current best: ${out.slots} time slots.`);
      $("generationStatus").className = out.status === "STOPPING"
        ? "notice warning" : "notice";
      await pollStatus();
      if (activeJobId) pollHandle = setInterval(pollStatus, 1000);
      return;
    }

    setRunning(false);
    if (out.status !== "IDLE") {
      $("generationStatus").textContent = out.message || out.status;
      $("generationStatus").className = out.status === "FAILED"
        ? "notice error" : (out.optimal ? "notice success" : "notice warning");
      latestResult = out;
      $("csvBtn").disabled = !allRows.length;
    }
  } catch (e) {
    $("generationStatus").textContent = "Could not restore previous optimization: " + e.message;
    $("generationStatus").className = "notice warning";
  }
}

loadStats();
restoreCurrentJob();
