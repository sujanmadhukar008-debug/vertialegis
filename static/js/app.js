// ── Key management ────────────────────────────────────────────────────────────
function saveKey(val) {
  localStorage.setItem('ccms_gemini_key', val);
  syncKeyToBackend(val);
}
function loadKey() {
  const k = localStorage.getItem('ccms_gemini_key') || '';
  const inp = document.getElementById('api-key-input');
  if (inp) inp.value = k;
  if (k) syncKeyToBackend(k);
  return k;
}
async function syncKeyToBackend(key) {
  try {
    await fetch('/api/set-key', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({key})
    });
  } catch {}
}

// ── API client ────────────────────────────────────────────────────────────────
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async put(path, body) {
    const r = await fetch(path, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async upload(file) {
    const fd = new FormData(); fd.append('file', file);
    const r = await fetch('/api/upload', {method:'POST', body:fd});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
};

function humanError(e) {
  const m = e.message || '';
  if (m.includes('413')) return 'File too large — maximum 50MB';
  if (m.includes('400')) return 'Invalid file — must be a searchable PDF';
  if (m.includes('500')) return 'Server error — try again in a moment';
  if (m.includes('Failed to fetch') || m.includes('NetworkError')) return 'Cannot reach server — is it running?';
  if (m.includes('No API key')) return 'Enter your Gemini API key in the sidebar first';
  return 'Something went wrong — please try again';
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type='success') {
  const c = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.style.opacity='0', 2700);
  setTimeout(() => t.remove(), 3000);
}

// ── Router ────────────────────────────────────────────────────────────────────
const PAGE_TITLES = {upload:'Upload — CCMS AI',review:'Pending Review — CCMS AI',dashboard:'Dashboard — CCMS AI',judgments:'All Judgments — CCMS AI'};

function navigate(view, el) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item, .mnav-item').forEach(n => n.classList.remove('active'));
  const panel = document.getElementById(`view-${view}`);
  if (panel) panel.classList.add('active');
  if (el) el.classList.add('active');
  document.title = PAGE_TITLES[view] || 'CCMS AI';
  window.location.hash = view;
  if (view === 'review')     loadReview();
  if (view === 'dashboard')  loadDashboard();
  if (view === 'judgments')  loadJudgments();
}

window.addEventListener('hashchange', () => {
  const v = window.location.hash.replace('#','') || 'upload';
  const navEl = document.querySelector(`.nav-item[onclick*="'${v}'"]`);
  navigate(v, navEl);
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function statusBadge(s) {
  const m = {pending:['b-gray','Pending'],extracting:['b-amber','Extracting…'],extracted:['b-blue','Extracted'],verified:['b-green','Verified'],rejected:['b-red','Rejected'],draft:['b-amber','Draft'],approved:['b-green','Approved']};
  const [cls,label] = m[s]||['b-gray',s];
  return `<span class="badge ${cls}">${label}</span>`;
}
function priorityBadge(p) {
  const m = {high:'b-red',medium:'b-amber',low:'b-green',High:'b-red',Medium:'b-amber',Low:'b-green'};
  return `<span class="badge ${m[p]||'b-gray'}">${p}</span>`;
}
function boolBadge(v) {
  return v ? `<span class="pill-true">TRUE</span>` : `<span class="pill-false">false</span>`;
}
function confBar(v) {
  const pct = Math.round(v||0);
  const c = pct>=80?'var(--green)':pct>=50?'var(--amber)':'var(--red)';
  const lbl = pct>=80?'High — safe to approve':pct>=50?'Medium — review carefully':'Low — verify manually';
  return `<span class="conf-wrap" title="${lbl}"><span class="conf-fill" style="width:${pct}%;background:${c}"></span></span> <span style="font-size:11px;color:${c}">${pct} — ${lbl}</span>`;
}
function safe(arr) {
  if (Array.isArray(arr)) return arr;
  try { return JSON.parse(arr)||[]; } catch { return []; }
}
function fmt(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
}
function trunc(s,n=55) { return s&&s.length>n ? s.slice(0,n)+'…' : (s||'—'); }

const FLAG_DESCRIPTIONS = {
  missing_critical_info: 'Some fields could not be extracted — verify case number and date manually',
  ambiguous_directions: 'One or more directions are unclear — legal team should interpret before acting',
  requires_legal_review: 'Forward to Legal Cell before approving action plan',
  contempt_risk: '⚠ Court has signalled dissatisfaction — escalate to senior officer immediately',
  cross_jurisdiction: 'Multiple state/UT authorities involved — coordinate with each legal cell',
  document_incomplete: 'Document may be incomplete — verify full order is present',
  operative_order_missing: 'Operative order not found in extracted text — obtain full judgment'
};

// ── Upload ────────────────────────────────────────────────────────────────────
const zone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');

zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over'); handleFile(e.dataTransfer.files[0]); });
zone.addEventListener('click', e => { if (e.target.tagName !== 'BUTTON') fileInput.click(); });
fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));

const STATUS_PROGRESS = {
  pending:    {pct:20, label:'Queued — waiting for AI…'},
  extracting: {pct:65, label:'AI reading judgment — 20–60 seconds…'},
  extracted:  {pct:95, label:'Extraction complete — loading…'},
  verified:   {pct:100,label:'Verified ✓'},
  rejected:   {pct:100,label:'Processing failed'}
};

async function handleFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    zone.classList.add('error');
    document.getElementById('upload-sub-text').textContent = 'Only PDF files accepted — try again';
    setTimeout(() => { zone.classList.remove('error'); document.getElementById('upload-sub-text').textContent = 'Supports digital and scanned PDFs · Max 50MB'; }, 3000);
    return;
  }
  zone.classList.add('hidden');
  document.getElementById('upload-progress').classList.remove('hidden');
  document.getElementById('upload-result').innerHTML = '';

  const progFill = document.getElementById('progress-fill');
  const progLabel = document.getElementById('progress-label');

  try {
    const judgment = await API.upload(file);
    progFill.style.width = '20%';
    progLabel.textContent = 'Uploaded — AI processing…';
    await pollStatus(judgment.id, progFill, progLabel);
  } catch(e) {
    progFill.style.background = 'var(--red)';
    progLabel.textContent = humanError(e);
    setTimeout(() => {
      document.getElementById('upload-progress').classList.add('hidden');
      zone.classList.remove('hidden');
    }, 4000);
  }
}

async function pollStatus(id, progFill, progLabel) {
  const start = Date.now();
  const TIMEOUT = 300000; // 5 minutes
  for (let i = 0; i < 100; i++) {
    await new Promise(r => setTimeout(r, 3000));
    if (Date.now() - start > TIMEOUT) {
      progLabel.innerHTML = 'AI analysis is taking longer than usual. It will appear in All Judgments once complete. <button class="btn-ghost" onclick="navigate(\'judgments\',null)" style="font-size:11px;padding:3px 8px">Check All Judgments →</button>';
      return;
    }
    try {
      const j = await API.get(`/api/judgments/${id}`);
      const sp = STATUS_PROGRESS[j.status] || {pct:50,label:'Processing…'};
      progFill.style.width = sp.pct + '%';
      progLabel.textContent = sp.label;
      if (j.status === 'extracted' || j.status === 'verified' || j.status === 'rejected') {
        document.getElementById('upload-progress').classList.add('hidden');
        zone.classList.remove('hidden');
        showUploadResult(j);
        updateReviewBadge();
        return;
      }
    } catch {}
  }
}

function showUploadResult(j) {
  const ed = j.extracted_data;
  const dirs = safe(ed?.directions);
  const conf = Math.round(ed?.confidence_score || 0);
  const confColor = conf>=80?'var(--green)':conf>=50?'var(--amber)':'var(--red)';
  document.getElementById('upload-result').innerHTML = `
    <div class="result-card">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:1rem;gap:1rem">
        <div>
          <div style="font-size:15px;font-weight:700;color:var(--green)">✓ Extraction complete</div>
          <div style="font-size:11.5px;color:var(--text2);margin-top:2px">${j.filename} · ${j.page_count} pages · ${j.status}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:24px;font-weight:700;color:${confColor}">${conf}<span style="font-size:12px">/100</span></div>
          <div style="font-size:10px;color:var(--text3)">Confidence</div>
        </div>
      </div>
      ${ed ? `
      <div class="grid-2" style="margin-bottom:1rem">
        <div>
          <div class="sec-label">Extracted</div>
          <div class="kv-row"><span>Case no.</span><strong>${ed.case_number||'—'}</strong></div>
          <div class="kv-row"><span>Court</span><strong>${ed.court_name||'—'}</strong></div>
          <div class="kv-row"><span>Date</span><strong>${ed.date_of_order||'—'}</strong></div>
        </div>
        <div>
          <div class="sec-label">Summary</div>
          <div class="kv-row"><span>Directions</span><strong>${dirs.length} extracted</strong></div>
          <div class="kv-row"><span>Timelines</span><strong>${safe(ed.timelines).length} found</strong></div>
          <div class="kv-row"><span>Status</span>${statusBadge(j.status)}</div>
        </div>
      </div>
      ` : '<div class="text-muted">No data extracted</div>'}
      <div class="btn-row">
        <button class="btn-primary" onclick="navigate('review',document.querySelector('.nav-item[onclick*=\\'review\\']'))">Review & verify extraction →</button>
        <button class="btn-ghost" onclick="navigate('judgments',document.querySelector('.nav-item[onclick*=\\'judgments\\']'))">View all judgments</button>
      </div>
    </div>`;
}

// ── Review ────────────────────────────────────────────────────────────────────
async function loadReview() {
  const list = document.getElementById('review-list');
  list.innerHTML = '<div class="skeleton" style="height:80px"></div><div class="skeleton" style="height:80px;margin-top:8px"></div>';
  try {
    const judgments = await API.get('/api/judgments?status=extracted');
    const badge = document.getElementById('badge-review');
    badge.textContent = judgments.length;
    badge.style.display = judgments.length > 0 ? 'inline-block' : 'none';
    document.getElementById('mbadge') && (document.getElementById('mbadge').textContent = judgments.length);
    if (!judgments.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-icon">✓</div><h3>Nothing pending review</h3><p>Upload a judgment to get started.</p><button class="btn-primary" onclick="navigate('upload',document.querySelector('.nav-item'))">Upload judgment →</button></div>`;
      return;
    }
    list.innerHTML = judgments.map(j => `
      <div class="card">
        <div class="card-header">
          <div><div class="card-title">${j.filename}</div><div class="card-sub">${fmt(j.upload_date)} · ${j.page_count} pages</div></div>
          ${statusBadge(j.status)}
        </div>
        <div class="btn-row"><button class="btn-primary" onclick="openReviewModal(${j.id})">Review &amp; Verify →</button></div>
      </div>`).join('');
  } catch(e) {
    list.innerHTML = `<div class="card" style="color:var(--red)">${humanError(e)}</div>`;
  }
}

async function updateReviewBadge() {
  try {
    const j = await API.get('/api/judgments?status=extracted');
    const badge = document.getElementById('badge-review');
    badge.textContent = j.length;
    badge.style.display = j.length > 0 ? 'inline-block' : 'none';
  } catch {}
}

async function openReviewModal(id) {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-title').textContent = 'Loading judgment…';
  document.getElementById('modal-body').innerHTML = '<div class="skeleton" style="height:300px"></div>';

  const j = await API.get(`/api/judgments/${id}`);
  const ed = j.extracted_data;
  const dirs = safe(ed?.directions);
  const times = safe(ed?.timelines);
  const pets = safe(ed?.petitioners);
  const resps = safe(ed?.respondents);
  let flags = {};
  try { flags = typeof ed?.flags === 'object' ? ed.flags : JSON.parse(ed?.flags||'{}'); } catch {}

  document.getElementById('modal-title').textContent = j.filename;
  document.getElementById('modal-body').innerHTML = `
    <div class="grid-2">
      <div>
        <div class="sec-label">Case details — click to edit</div>
        <div class="card" style="margin-bottom:1rem">
          <div style="margin-bottom:8px"><div style="font-size:10px;color:var(--text3);margin-bottom:3px">Case number</div><input class="inline-edit" id="e-case-number" value="${ed?.case_number||''}"/></div>
          <div style="margin-bottom:8px"><div style="font-size:10px;color:var(--text3);margin-bottom:3px">Court name</div><input class="inline-edit" id="e-court-name" value="${ed?.court_name||''}"/></div>
          <div style="margin-bottom:8px"><div style="font-size:10px;color:var(--text3);margin-bottom:3px">Date of order</div><input class="inline-edit" id="e-date" value="${ed?.date_of_order||''}"/></div>
          <div><div style="font-size:10px;color:var(--text3);margin-bottom:3px">Case title</div><input class="inline-edit" id="e-title" value="${ed?.case_title||''}"/></div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">${confBar(ed?.confidence_score||0)}</div>
        </div>
        <div class="sec-label">Parties</div>
        <div class="card" style="margin-bottom:1rem">
          <div style="font-size:10.5px;color:var(--text3);margin-bottom:4px">Petitioners</div>
          ${pets.map(p=>`<div style="font-size:12.5px;margin-bottom:2px">${p?.value||p}</div>`).join('')||'<div class="text-muted">—</div>'}
          <div style="font-size:10.5px;color:var(--text3);margin:8px 0 4px">Respondents</div>
          ${resps.map(r=>`<div style="font-size:12.5px;margin-bottom:2px">${r?.value||r}</div>`).join('')||'<div class="text-muted">—</div>'}
        </div>
        <div class="sec-label">Validation flags</div>
        <div class="card">
          ${Object.entries(flags).map(([k,v])=>`
            <div class="flag-row">
              <div>
                <div style="font-size:12px;font-weight:500">${k.replace(/_/g,' ')}</div>
                ${v && FLAG_DESCRIPTIONS[k] ? `<div class="flag-desc">${FLAG_DESCRIPTIONS[k]}</div>` : ''}
              </div>
              ${boolBadge(v)}
            </div>`).join('')||'<div class="text-muted">No flags</div>'}
        </div>
      </div>
      <div>
        <div class="sec-label">Directions (${dirs.length})</div>
        ${dirs.map((d,i)=>`
          <div class="dir-card">
            <div class="dir-num">Direction ${i+1}</div>
            <div class="dir-text">${d?.value||d}</div>
            ${d?.source_text?`<div class="snip">"${d.source_text}"</div>`:''}
          </div>`).join('')||'<div class="text-muted">None extracted</div>'}
        <div class="sec-label" style="margin-top:1rem">Timelines</div>
        ${times.map(t=>`
          <div class="timeline-item">
            <div class="timeline-dot"></div>
            <div><div style="font-size:12.5px">⏰ ${t?.value||t}</div>${t?.source_text?`<div class="snip">"${t.source_text}"</div>`:''}</div>
          </div>`).join('')||'<div class="text-muted">None found</div>'}
      </div>
    </div>
    <div class="divider"></div>
    <div class="sec-label">Reviewer notes</div>
    <textarea id="review-notes" placeholder="Add notes (optional)…"></textarea>
    <div class="btn-row">
      <button class="btn-success" onclick="verifyJudgment(${id},'approve')">✓ Approve &amp; Generate Action Plan</button>
      <button class="btn-danger"  onclick="verifyJudgment(${id},'reject')">✕ Reject</button>
      <button class="btn-ghost"   onclick="closeModal()">Cancel</button>
    </div>`;
}

async function verifyJudgment(id, action) {
  const notes = document.getElementById('review-notes')?.value || '';
  const changes = action === 'edit' || action === 'approve' ? {
    case_number: document.getElementById('e-case-number')?.value,
    court_name:  document.getElementById('e-court-name')?.value,
    date_of_order: document.getElementById('e-date')?.value,
    case_title:  document.getElementById('e-title')?.value,
  } : {};

  const actualAction = action === 'approve' ? 'edit' : action;
  try {
    await API.put(`/api/judgments/${id}/verify`, {action: actualAction, notes, changes});
    if (action === 'approve') {
      document.getElementById('modal-body').innerHTML += `<div id="plan-gen" style="margin-top:1rem;color:var(--amber);font-size:13px">⏳ Generating action plan…</div>`;
      try {
        const plan = await API.post(`/api/judgments/${id}/action-plan`, {});
        document.getElementById('plan-gen').innerHTML = `
          <div style="color:var(--green);margin-bottom:8px">✓ Action plan generated — ${safe(plan.specific_actions).length} actions</div>
          <div class="btn-row"><button class="btn-amber" onclick="openPlanModal(${plan.id})">Review action plan →</button></div>`;
      } catch(e) {
        document.getElementById('plan-gen').innerHTML = `<div style="color:var(--red)">Action plan failed: ${humanError(e)}</div>`;
      }
    } else {
      toast('Judgment rejected', 'error');
      closeModal();
    }
    loadReview();
    updateReviewBadge();
  } catch(e) {
    toast(humanError(e), 'error');
  }
}

// ── Action plan modal ─────────────────────────────────────────────────────────
async function openPlanModal(planId) {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-title').textContent = 'Loading action plan…';
  document.getElementById('modal-body').innerHTML = '<div class="skeleton" style="height:300px"></div>';
  try {
    const plan = await API.get(`/api/action-plans/${planId}`);
    const actions = safe(plan.specific_actions);
    const depts = safe(plan.responsible_departments);
    const times = safe(plan.key_timelines);
    document.getElementById('modal-title').textContent = `Action Plan — ${plan.filename||'Judgment'}`;
    document.getElementById('modal-body').innerHTML = `
      <div class="card" style="margin-bottom:1rem">
        <div class="card-meta">${statusBadge(plan.status)}<span class="badge b-purple">${plan.nature_of_action||'compliance'}</span>${priorityBadge(plan.priority_level||'medium')}</div>
        <div style="font-size:12px;color:var(--text2);margin-top:8px">Departments: ${depts.join(', ')||'—'}</div>
      </div>
      <div class="sec-label">Key timelines</div>
      ${times.map(t=>`
        <div class="timeline-item">
          <div class="timeline-dot" style="background:${t.urgency==='critical'?'var(--red)':'var(--amber)'}"></div>
          <div>
            <div style="font-size:12.5px;font-weight:500">${t.deadline||'—'}</div>
            <div style="font-size:12px;color:var(--text2)">${t.description||'—'}</div>
            <span class="badge b-${t.urgency==='critical'?'red':t.urgency==='high'?'amber':'gray'}" style="margin-top:4px">${t.urgency||'—'}</span>
          </div>
        </div>`).join('')||'<div class="text-muted">None</div>'}
      <div class="sec-label" style="margin-top:1.25rem">Specific actions (${actions.length})</div>
      ${actions.map((a,i)=>`
        <div class="action-item" style="border-left:3px solid ${a.priority==='High'?'var(--red)':a.priority==='Medium'?'var(--amber)':'var(--green)'}">
          <div class="action-title">${i+1}. ${a.action||'—'}</div>
          <div style="font-size:11px;color:var(--text3);margin-bottom:6px">${a.source_text||''} ${a.page ? `(Page ${a.page})` : ''}</div>
          <div style="font-size:12px;color:var(--text2);margin-bottom:8px"><strong>Deliverable:</strong> ${a.deliverable||'—'}</div>
          <div style="font-size:11px;color:var(--text3);margin-bottom:8px;font-style:italic">Reason: ${a.reasoning||'—'}</div>
          <div class="action-meta">
            ${priorityBadge(a.priority||'Medium')}
            <span class="badge b-purple">Confidence: ${a.confidence||'Medium'}</span>
            <span class="badge b-blue">${a.department||'—'}</span>
            📅 ${a.deadline||'—'}
          </div>
        </div>`).join('')}
      <div class="sec-label" style="margin-top:1rem">Reviewer notes</div>
      <textarea id="plan-notes" placeholder="Add notes…"></textarea>
      <div class="btn-row">
        <button class="btn-success" onclick="verifyPlan(${planId},'approve')">✓ Approve plan</button>
        <button class="btn-danger"  onclick="verifyPlan(${planId},'reject')">✕ Reject plan</button>
        <button class="btn-ghost"   onclick="closeModal()">Close</button>
      </div>`;
  } catch(e) {
    document.getElementById('modal-body').innerHTML = `<div style="color:var(--red)">${humanError(e)}</div>`;
  }
}

async function verifyPlan(planId, action) {
  const notes = document.getElementById('plan-notes')?.value || '';
  try {
    await API.put(`/api/action-plans/${planId}/verify`, {action, notes});
    toast(action === 'approve' ? '✓ Action plan approved — visible on Dashboard' : 'Plan rejected', action === 'approve' ? 'success' : 'error');
    closeModal();
    navigate('dashboard', document.querySelector('.nav-item[onclick*="dashboard"]'));
  } catch(e) { toast(humanError(e), 'error'); }
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard() {
  const statsGrid = document.getElementById('stats-grid');
  const approvedList = document.getElementById('approved-list');
  try {
    const stats = await API.get('/api/dashboard/stats');
    statsGrid.innerHTML = `
      <div class="stat-card"><div class="stat-val">${stats.total}</div><div class="stat-key">Total</div></div>
      <div class="stat-card"><div class="stat-val" style="color:var(--amber)">${stats.pending}</div><div class="stat-key">Processing</div></div>
      <div class="stat-card"><div class="stat-val" style="color:var(--blue)">${stats.extracted}</div><div class="stat-key">For review</div></div>
      <div class="stat-card"><div class="stat-val" style="color:var(--green)">${stats.verified}</div><div class="stat-key">Verified</div></div>
      <div class="stat-card"><div class="stat-val" style="color:var(--red)">${stats.rejected}</div><div class="stat-key">Rejected</div></div>
      <div class="stat-card"><div class="stat-val" style="color:var(--purple)">${stats.approved_plans}</div><div class="stat-key">Approved plans</div></div>`;

    const approved = await API.get('/api/dashboard/approved');
    if (!approved.length) {
      approvedList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📋</div>
          <h3>Dashboard is empty</h3>
          <p>Upload a judgment, verify the extraction, approve the action plan — it appears here.</p>
          <div class="empty-steps">
            <div class="empty-step active">① Upload</div>
            <div class="empty-step">② Verify</div>
            <div class="empty-step">③ Approve plan</div>
            <div class="empty-step">④ Appears here</div>
          </div>
          <button class="btn-primary" onclick="navigate('upload',document.querySelector('.nav-item'))">Upload first judgment →</button>
        </div>`;
      return;
    }
    approvedList.innerHTML = approved.map(ap => {
      const actions = safe(ap.specific_actions);
      const high = actions.filter(a=>(a.priority||'').toLowerCase()==='high').length;
      const depts = safe(ap.responsible_departments);
      return `
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">${ap.case_number||ap.filename||'—'}</div>
              <div class="card-sub">${ap.court_name||''} ${ap.date_of_order?'· '+ap.date_of_order:''}</div>
            </div>
            <div style="display:flex;gap:6px;align-items:center">${priorityBadge(ap.priority_level||'medium')}${statusBadge('approved')}</div>
          </div>
          <div style="font-size:12.5px;color:var(--text2);margin-bottom:10px">${ap.case_title||'—'}</div>
          <div class="card-meta">
            <span class="badge b-red">⚡ ${high} high-priority</span>
            <span class="badge b-blue">📋 ${actions.length} actions</span>
            <span class="badge b-purple">🏛 ${depts.length} departments</span>
          </div>
          <div class="divider"></div>
          <div class="sec-label">Departments</div>
          <div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px">
            ${depts.map(d=>`<span class="badge b-gray">${d}</span>`).join('')}
          </div>
          <div class="sec-label">Top actions</div>
          ${actions.slice(0,4).map((a,i)=>`
            <div class="action-item" style="border-left:3px solid ${(a.priority||'').toLowerCase()==='high'?'var(--red)':a.priority==='Medium'||a.priority==='medium'?'var(--amber)':'var(--green)'}">
              <div class="action-title">${i+1}. ${a.action||'—'}</div>
              <div style="font-size:11.5px;color:var(--text2);margin:4px 0"><strong>Deliverable:</strong> ${a.deliverable||'—'}</div>
              <div class="action-meta">${priorityBadge(a.priority||'Medium')}<span class="badge b-blue">${a.department||'—'}</span>📅 ${a.deadline||'—'}</div>
            </div>`).join('')}
          ${actions.length>4?`<div class="text-muted" style="font-size:11.5px;margin-top:4px">+${actions.length-4} more actions</div>`:''}
        </div>`;
    }).join('');
  } catch(e) {
    statsGrid.innerHTML = `<div style="color:var(--red);font-size:13px">${humanError(e)}</div>`;
  }
}

// ── All judgments ─────────────────────────────────────────────────────────────
async function loadJudgments() {
  const container = document.getElementById('judgments-table');
  const filter = document.getElementById('status-filter').value;
  container.innerHTML = '<div class="skeleton" style="height:200px"></div>';
  try {
    const url = filter ? `/api/judgments?status=${filter}` : '/api/judgments';
    const judgments = await API.get(url);
    if (!judgments.length) {
      container.innerHTML = `<div class="empty-state"><div class="empty-icon">📂</div><h3>No judgments found</h3><button class="btn-primary" onclick="navigate('upload',null)" style="margin-top:1rem">Upload judgment →</button></div>`;
      return;
    }
    container.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Case / File</th><th>Uploaded</th><th>Pages</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>${judgments.map(j=>`
            <tr>
              <td><div style="font-weight:500;color:var(--text)">${trunc(j.filename,45)}</div></td>
              <td>${fmt(j.upload_date)}</td>
              <td>${j.page_count||'—'}</td>
              <td>${statusBadge(j.status)}</td>
              <td class="td-link" onclick="openDetailModal(${j.id})">View →</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red);font-size:13px">${humanError(e)}</div>`;
  }
}

async function openDetailModal(id) {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-title').textContent = 'Loading…';
  document.getElementById('modal-body').innerHTML = '<div class="skeleton" style="height:300px"></div>';
  const j = await API.get(`/api/judgments/${id}`);
  const ed = j.extracted_data;
  const plans = j.action_plans||[];
  document.getElementById('modal-title').textContent = j.filename;
  document.getElementById('modal-body').innerHTML = `
    <div class="card-meta" style="margin-bottom:1rem">${statusBadge(j.status)}<span class="badge b-gray">${j.page_count} pages</span><span class="badge b-gray">${fmt(j.upload_date)}</span></div>
    ${ed?`
      <div class="grid-2">
        <div>
          <div class="sec-label">Case details</div>
          <div class="kv-row"><span>Case no.</span><strong>${ed.case_number||'—'}</strong></div>
          <div class="kv-row"><span>Court</span><strong>${ed.court_name||'—'}</strong></div>
          <div class="kv-row"><span>Date</span><strong>${ed.date_of_order||'—'}</strong></div>
          <div class="kv-row"><span>Confidence</span><div>${confBar(ed.confidence_score||0)}</div></div>
        </div>
        <div>
          <div class="sec-label">Action plans (${plans.length})</div>
          ${plans.map(p=>`
            <div class="action-item">
              <div class="action-meta">${statusBadge(p.status)}${priorityBadge(p.priority_level||'medium')}</div>
              <div style="font-size:11.5px;color:var(--text2);margin-top:6px">${safe(p.specific_actions).length} actions · ${fmt(p.created_at)}</div>
              ${p.status==='draft'?`<button class="btn-amber" style="margin-top:8px;font-size:12px" onclick="openPlanModal(${p.id})">Review plan →</button>`:''}
            </div>`).join('')||'<div class="text-muted">No plans yet</div>'}
        </div>
      </div>
    `:'<div class="text-muted">No extracted data</div>'}
    <div class="btn-row">
      ${j.status==='extracted'?`<button class="btn-primary" onclick="openReviewModal(${id})">Review extraction</button>`:''}
      ${j.status==='verified'&&!plans.length?`<button class="btn-amber" onclick="generatePlan(${id})">Generate action plan</button>`:''}
      <button class="btn-ghost" onclick="closeModal()">Close</button>
    </div>`;
}

async function generatePlan(id) {
  const body = document.getElementById('modal-body');
  body.innerHTML += `<div id="gen-status" style="margin-top:1rem;color:var(--amber);font-size:13px">⏳ Generating…</div>`;
  try {
    const plan = await API.post(`/api/judgments/${id}/action-plan`,{});
    document.getElementById('gen-status').innerHTML = `<div style="color:var(--green)">✓ ${safe(plan.specific_actions).length} actions generated</div>`;
    setTimeout(()=>{ closeModal(); openPlanModal(plan.id); }, 800);
  } catch(e) { document.getElementById('gen-status').innerHTML = `<div style="color:var(--red)">${humanError(e)}</div>`; }
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function handleOverlayClick(e) { if (e.target === document.getElementById('modal-overlay')) closeModal(); }
function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// ── Health check ──────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const stats = await API.get('/api/dashboard/stats');
    document.getElementById('status-dot').style.background = 'var(--green)';
    document.getElementById('status-text').textContent = `${stats.total} judgment${stats.total!==1?'s':''}`;
  } catch {
    document.getElementById('status-dot').style.background = 'var(--red)';
    document.getElementById('status-text').textContent = 'API unreachable';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadKey();
checkHealth();
updateReviewBadge();

let badgeInterval = setInterval(updateReviewBadge, 20000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearInterval(badgeInterval);
  else { updateReviewBadge(); badgeInterval = setInterval(updateReviewBadge, 20000); }
});

const initView = window.location.hash.replace('#','') || 'upload';
if (initView !== 'upload') {
  const el = document.querySelector(`.nav-item[onclick*="'${initView}'"]`);
  navigate(initView, el);
}
