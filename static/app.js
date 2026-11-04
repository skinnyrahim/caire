// CAIRE Cover clinic desk. Talks to the Flask API in app.py.
const T = ['Enrol', 'Premium', 'Eligibility', 'Claim', 'Ledger', 'Staff'];
const LVL = {desk: 1, reviewer: 2, admin: 3};
let ME = null, staff = [], auditRows = [];
let CFG = null, tab = 0, members = [], pays = [], claims = [], sum = {};
let pend = null;      // pending mobile money prompt
let pc = null;        // pending claim waiting for the member's code
let draft = {type: 'gap'}; // claim form values kept across re-renders
let elig = null, q = '';

const $ = s => document.querySelector(s);
const gh = n => 'GHS ' + Math.round(n || 0).toLocaleString();
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pill = (t, c) => `<span class="pill ${c || ''}">${esc(t)}</span>`;
const opts = l => l.map(m => `<option value="${esc(m.id)}">${esc(m.name)} (${esc(m.id)})</option>`).join('');
const val = id => ($('#' + id)?.value ?? '').trim();

function toast(t) {
  const e = $('#t'); e.textContent = t; e.style.display = 'block';
  clearTimeout(toast.h); toast.h = setTimeout(() => e.style.display = 'none', 4000);
}

async function api(path, body, method) {
  let r;
  try {
    r = await fetch('/api/' + path, {
      method: method || (body !== undefined ? 'POST' : 'GET'),
      headers: body !== undefined ? {'Content-Type': 'application/json'} : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) { throw new Error('No internet connection. Check the connection and try again.'); }
  let d = null; try { d = await r.json(); } catch (e) {}
  if (r.status === 401 && path !== 'login' && path !== 'me') { ME = null; showLogin(); }
  if (!r.ok) throw new Error((d && d.error) || 'Something went wrong. Try again.');
  return d;
}

// Runs an action, shows errors as toasts, and disables the clicked button meanwhile.
async function run(fn, btn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message); }
  finally { if (btn) btn.disabled = false; }
}

const can = r => ME && LVL[ME.role] >= LVL[r];

async function load() {
  [members, pays, claims, sum] = await Promise.all([api('members'), api('payments'), api('claims'), api('summary')]);
  if (can('admin')) [staff, auditRows] = await Promise.all([api('staff'), api('audit')]);
  render();
}

// ---------- sign in ----------
function shell(on) {
  $('#nav').hidden = !on; $('#who').hidden = !on;
  $('#demo').hidden = !(on && CFG && CFG.simulate && can('admin'));
}
function showLogin() {
  shell(false);
  $('#m').innerHTML = `<img class="logo" src="/icons/logo.png" alt="CAIRE" width="120" height="120"><h2>Sign in</h2><p class="mut">Use the staff account given to you by the CAIRE administrator.</p>
  <form class="card" onsubmit="event.preventDefault();doLogin(this.querySelector('button'))"><div class="g"><label>Username<input id="lu" autocomplete="username" autocapitalize="none"></label>
  <label>Password<input id="lp" type="password" autocomplete="current-password"></label></div><button class="b">Sign in</button></form>`;
  $('#lu').focus();
}
function showPwChange(forced) {
  shell(!forced);
  $('#m').innerHTML = `<h2>${forced ? 'Set your own password' : 'Change password'}</h2><p class="mut">${forced ? 'Your account has a temporary password. Choose a new one to continue.' : ''} Use at least 8 characters.</p>
  <form class="card" onsubmit="event.preventDefault();doPw(this.querySelector('button.b'))"><div class="g"><label>Current password<input id="pc" type="password" autocomplete="current-password"></label>
  <label>New password<input id="pn1" type="password" autocomplete="new-password"></label><label>New password again<input id="pn2" type="password" autocomplete="new-password"></label></div>
  <button class="b">Save password</button> ${forced ? '' : '<button type="button" class="s" onclick="render()">Cancel</button>'}</form>`;
}
function doLogin(btn) {
  run(async () => { ME = await api('login', {username: val('lu'), password: $('#lp').value}); await start(); }, btn);
}
function doPw(btn) {
  run(async () => {
    if ($('#pn1').value !== $('#pn2').value) throw new Error('The new passwords do not match.');
    await api('me/password', {current: $('#pc').value, new: $('#pn1').value});
    toast('Password saved.'); ME.must_change = false; await start();
  }, btn);
}
function logout() { run(async () => { await api('logout', {}); ME = null; pend = pc = elig = null; tab = 0; showLogin(); }); }
async function start() {
  if (ME.must_change) return showPwChange(true);
  CFG = await api('config');
  $('#sub').textContent = 'Clinic desk, pilot' + (CFG.simulate ? '. Mobile money and SMS are simulated.' : '');
  $('#foot').textContent = `Benefits per member per year: gap cover up to ${gh(CFG.gap_limit)}, hospital cash ${gh(CFG.cash_per_day)} a day for up to ${CFG.cash_max_days} days.`;
  $('#who').innerHTML = `${esc(ME.name)} <span class="mut">(${esc(ME.role)})</span> <button class="s" onclick="showPwChange(false)">Password</button><button class="s" onclick="logout()">Sign out</button>`;
  shell(true);
  await load();
}

// ---------- staff (admin) ----------
function addStaff(btn) {
  run(async () => {
    const u = await api('staff', {name: val('sn'), username: val('su'), role: val('sr'), password: $('#sp').value});
    toast(`${u.name} added. Give them the temporary password; they will set their own when they sign in.`); await load();
  }, btn);
}
function editStaff(id, patch, btn) { run(async () => { await api('staff/' + id, patch); toast('Saved.'); await load(); }, btn); }
function resetPw(id, name, btn) {
  const p = prompt(`Temporary password for ${name} (at least 8 characters):`);
  if (p) editStaff(id, {password: p}, btn);
}

const go = i => { tab = i; render(); };

// ---------- actions ----------
function enrol(btn) {
  run(async () => {
    const m = await api('members', {name: val('n'), phone: val('p'), ghana_card: val('c'), nhis: val('h'), consent: $('#k').checked});
    toast(`${m.name} enrolled as ${m.id}. Next: collect the first premium.`);
    await load();
  }, btn);
}
function askPay(btn) {
  run(async () => {
    if (!val('pm')) throw new Error('Enrol a member first.');
    pend = await api('payments', {member_id: val('pm'), network: val('pn')});
    render();
  }, btn);
}
function payAnswer(ok, btn) {
  run(async () => {
    const r = await api(`payments/${pend.id}/${ok ? 'approve' : 'decline'}`, {});
    toast(ok ? `Payment received. ${pend.member_name} is covered until ${r.paid_through}.` : 'Payment declined. No cover started.');
    pend = null; await load();
  }, btn);
}
function look(btn) {
  q = val('q');
  run(async () => { elig = q ? await api('eligibility?q=' + encodeURIComponent(q)) : null; render(); }, btn);
}
function keepDraft() {
  for (const k of ['cm', 'cd', 'ct', 'cx', 'cs', 'ca', 'cn']) if ($('#' + k)) draft[k] = $('#' + k).value;
  draft.type = draft.ct || draft.type;
}
function claimType() { keepDraft(); render(); }
function startClaim(btn) {
  keepDraft();
  run(async () => {
    if (!draft.cm) throw new Error('Pick a member.');
    pc = await api('claims/start', {member_id: draft.cm, visit_date: draft.cd, type: draft.type, diagnosis: draft.cx,
      services: draft.cs, amount: draft.ca, days: draft.cn});
    render();
  }, btn);
}
function confirmClaim(btn) {
  run(async () => {
    const c = await api('claims/confirm', {pending_id: pc.pending_id, code: val('co')});
    toast(c.id + (c.status === 'Needs review' ? ' sent for review.' : ' approved. Clinic paid within 72 hours.'));
    pc = null; draft = {type: 'gap'}; tab = 4; await load();
  }, btn);
}
function setSt(id, s, btn) { run(async () => { await api(`claims/${id}/status`, {status: s}); await load(); }, btn); }
function spotDraw(btn) {
  run(async () => {
    const r = await api('spotchecks/draw', {count: 2});
    toast(r.picked.length ? `${r.picked.length} claim(s) picked for a verification call.` : 'No claims left to sample.');
    await load();
  }, btn);
}
function spot(id, v, btn) { run(async () => { await api(`claims/${id}/spot`, {result: v}); await load(); }, btn); }
function seed(btn) { run(async () => { const r = await api('demo/seed', {}); toast(r.added.length ? 'Sample members added.' : 'Sample members are already loaded.'); await load(); }, btn); }
function wipe(btn) {
  if (!confirm('Delete all members, payments and claims?')) return;
  run(async () => { await api('demo/reset', {}); pend = pc = elig = null; q = ''; draft = {type: 'gap'}; await load(); }, btn);
}

// ---------- views ----------
function vEnrol() {
  const rows = members.slice().reverse().map(m => `<tr><td>${esc(m.id)}</td><td>${esc(m.name)}</td><td>${esc(m.phone)}</td><td>${m.covered ? pill('Covered to ' + m.paid_through, 'ok') : m.paid_through ? pill('Lapsed ' + m.paid_through, 'bad') : pill('Awaiting premium', 'warn')}</td></tr>`).join('');
  return `<h2>Enrol a member</h2><p class="mut">The clinic enrols the member at the desk. Confirm consent with them first.</p>
  <div class="card"><div class="g"><label>Full name<input id="n" autocomplete="off"></label><label>Mobile money phone<input id="p" inputmode="tel" placeholder="024 000 0000"></label>
  <label>Ghana Card number<input id="c" placeholder="GHA-000000000-0"></label><label>NHIS number (if any)<input id="h"></label></div>
  <label class="chk"><input type="checkbox" id="k">The member agrees to join CAIRE Cover and to share visit details for claims.</label>
  <button class="b" onclick="enrol(this)">Enrol member</button></div>
  <div class="card tw"><table><tr><th>ID</th><th>Name</th><th>Phone</th><th>Status</th></tr>${rows || '<tr><td colspan="4" class="mut">No members yet. Enrol the first one above.</td></tr>'}</table></div>`;
}

function vPay() {
  const box = pend ? `<div class="phone"><small>${CFG.simulate ? 'Simulated ' : ''}${esc(pend.network)} prompt on ${esc(pend.phone)}</small>Pay ${gh(pend.amount)} to CAIRE Cover for ${esc(pend.member_name)}?<br><br>
    <button class="b" onclick="payAnswer(true,this)">Approve</button> <button class="s r" onclick="payAnswer(false,this)">Decline</button></div>` : '';
  const st = {approved: 'ok', declined: 'bad', expired: 'bad', pending: 'warn'};
  return `<h2>Collect premium</h2><p class="mut">Each ${CFG.cover_days}-day cover costs ${gh(CFG.premium)}: the member pays ${gh(CFG.member_share)} and RUG subsidises ${gh(CFG.subsidy)}. Paying early adds ${CFG.cover_days} days after the current cover ends.</p>
  <div class="card"><div class="g"><label>Member<select id="pm">${opts(members)}</select></label><label>Network<select id="pn">${CFG.networks.map(n => `<option>${esc(n)}</option>`).join('')}</select></label></div>
  <button class="b" onclick="askPay(this)">Send payment prompt</button>${box}</div>
  <div class="card tw"><table><tr><th>Date</th><th>Member</th><th>Network</th><th>Member paid</th><th>RUG subsidy</th><th>Status</th></tr>${pays.map(p => `<tr><td>${esc(p.requested_at.slice(0, 10))}</td><td>${esc(p.member_name)}</td><td>${esc(p.network)}</td><td>${gh(p.member_amount)}</td><td>${gh(p.subsidy_amount)}</td><td>${pill(p.status === 'approved' ? `Cover ${p.cover_from} to ${p.cover_to}` : p.status, st[p.status])}</td></tr>`).join('') || '<tr><td colspan="6" class="mut">No payments yet.</td></tr>'}</table></div>`;
}

function vCheck() {
  let r = '';
  if (elig) {
    if (!elig.found) r = `<div class="card">${pill('Not found', 'bad')} <span class="mut">Check the number, or enrol the person.</span></div>`;
    else {
      const m = elig.member, rm = elig.remaining;
      r = `<div class="card"><b>${esc(m.name)}</b> (${esc(m.id)}) ${m.covered ? pill('Covered', 'ok') : pill('Not covered', 'bad')}
      <p class="mut">${m.covered ? 'Cover runs to ' + m.paid_through + '.' : 'Premium is not up to date. Collect it before treating under cover.'}</p>
      ${rm ? `<table><tr><th>Benefit</th><th>Remaining this year</th></tr><tr><td>Gap cover (labs, scans, medicines)</td><td>${gh(rm.gap)} of ${gh(CFG.gap_limit)}</td></tr><tr><td>Hospital cash (${gh(CFG.cash_per_day)} a day)</td><td>${rm.days} of ${CFG.cash_max_days} days</td></tr></table>` : ''}</div>`;
    }
  }
  return `<h2>Check eligibility</h2><p class="mut">Look up by phone number, member ID or Ghana Card number.</p>
  <div class="card"><label>Phone, member ID or Ghana Card<input id="q" value="${esc(q)}" onkeydown="if(event.key==='Enter')look()"></label><br><button class="b" onclick="look(this)">Check</button></div>${r}`;
}

function vClaim() {
  if (pc) {
    return `<h2>Confirm the claim</h2><div class="card"><p><b>${esc(pc.member_name)}</b>, ${esc(pc.visit_date)}: ${esc(pc.diagnosis)}. ${pc.type === 'gap' ? esc(pc.services) + ', ' + gh(pc.amount) : pc.days + ' day(s) off work'}</p>
    ${pc.simulated_code ? `<div class="phone"><small>Simulated SMS to ${esc(pc.phone)}</small>CAIRE Cover: your code to confirm today's visit is<div class="big">${esc(pc.simulated_code)}</div>Share it only if you are at the clinic.</div>` : `<p class="mut">A code was sent by SMS to ${esc(pc.phone)}. Ask the member to read it out.</p>`}
    <label>Code from the member<input id="co" inputmode="numeric" maxlength="4" autocomplete="one-time-code"></label><br>
    <button class="b" onclick="confirmClaim(this)">Submit claim</button> <button class="s" onclick="pc=null;render()">Back</button></div>`;
  }
  const ok = members.filter(m => m.covered), cash = draft.type === 'cash', d = draft;
  const sel = ok.map(m => `<option value="${esc(m.id)}"${m.id === d.cm ? ' selected' : ''}>${esc(m.name)} (${esc(m.id)})</option>`).join('');
  return `<h2>Submit a claim</h2><p class="mut">Capture the visit, then the member confirms with a code sent to their phone.</p>
  <div class="card"><div class="g"><label>Member (covered only)<select id="cm">${sel}</select></label><label>Visit date<input id="cd" type="date" max="${CFG.today}" value="${esc(d.cd || CFG.today)}"></label>
  <label>Benefit<select id="ct" onchange="claimType()"><option value="gap">Gap cover</option><option value="cash"${cash ? ' selected' : ''}>Hospital cash</option></select></label>
  <label>Diagnosis<input id="cx" value="${esc(d.cx)}"></label>${cash ? `<label>Days off work<input id="cn" type="number" min="1" max="60" value="${esc(d.cn)}"></label>` : `<label>Services and medicines<input id="cs" value="${esc(d.cs)}"></label><label>Amount (GHS)<input id="ca" type="number" min="1" value="${esc(d.ca)}"></label>`}</div>
  <button class="b" onclick="startClaim(this)">Send code to member</button>${ok.length ? '' : '<p class="mut">No covered members yet. Collect a premium first.</p>'}</div>`;
}

function vLedger() {
  const rv = can('reviewer');
  const rows = claims.map(c => {
    const a = !rv ? '' : c.created_by === ME.username && (c.status === 'Needs review' || c.status === 'Approved') ? '<span class="mut">Another reviewer must decide (you submitted it).</span>'
      : c.status === 'Needs review' ? `<button class="s" onclick="setSt('${c.id}','Approved',this)">Approve</button><button class="s r" onclick="setSt('${c.id}','Rejected',this)">Reject</button>`
      : c.status === 'Approved' ? `<button class="s" onclick="setSt('${c.id}','Paid',this)">Mark paid</button>` : '';
    const sp = c.spot === 'pending' && rv ? `<br>Call ${esc(c.member_phone)}: did the visit happen?<br><button class="s" onclick="spot('${c.id}','confirmed',this)">Yes</button><button class="s r" onclick="spot('${c.id}','disputed',this)">No</button>`
      : c.spot ? `<br>${pill('Call: ' + c.spot, c.spot === 'confirmed' ? 'ok' : 'bad')}` : '';
    return `<tr><td>${esc(c.id)}</td><td>${esc(c.member_name)}</td><td>${esc(c.visit_date)}<br><span class="mut">${esc(c.diagnosis)}</span></td><td>${c.type === 'gap' ? 'Gap' + (c.amount_billed !== c.payable ? `<br><span class="mut">billed ${gh(c.amount_billed)}</span>` : '') : 'Cash, ' + c.days + ' d'}</td><td>${gh(c.payable)}</td>
    <td>${pill(c.status, c.status === 'Needs review' ? 'warn' : c.status === 'Rejected' ? 'bad' : 'ok')}<br><span class="mut">by ${esc(c.created_by || 'n/a')}${c.reviewed_by ? ', reviewed by ' + esc(c.reviewed_by) : ''}</span>${c.flags.length ? '<br><span class="mut">' + esc(c.flags.join('; ')) + '</span>' : ''}</td><td>${a}${sp}</td></tr>`;
  }).join('');
  return `<h2>Ledger</h2><p class="mut">Claims, flags for review and weekly spot-checks.</p>
  <div class="k"><div><b>${sum.members}</b><span>Members</span></div><div><b>${sum.covered}</b><span>Covered now</span></div><div><b>${gh(sum.premiums)}</b><span>Premiums (member ${gh(sum.premiums_member)} + RUG ${gh(sum.premiums_subsidy)})</span></div><div><b>${gh(sum.claims_payable)}</b><span>Claims payable (${gh(sum.claims_paid)} paid)</span></div><div><b>${sum.claims_ratio == null ? 'n/a' : sum.claims_ratio + '%'}</b><span>Claims ratio</span></div></div>
  <div class="card tw">${rv ? '<div class="bar"><button class="s" onclick="spotDraw(this)">Draw weekly spot-check</button><a class="s" href="/api/export/claims.csv">Export CSV</a></div>' : ''}
  <table><tr><th>ID</th><th>Member</th><th>Visit</th><th>Benefit</th><th>Payable</th><th>Status</th><th>Action</th></tr>${rows || '<tr><td colspan="7" class="mut">No claims yet.</td></tr>'}</table></div>`;
}

function vStaff() {
  const roleSel = (u) => `<select onchange="editStaff(${u.id},{role:this.value},this)" aria-label="Role for ${esc(u.name)}"${u.id === ME.id ? ' disabled' : ''}>${Object.keys(LVL).map(r => `<option${r === u.role ? ' selected' : ''}>${r}</option>`).join('')}</select>`;
  const rows = staff.map(u => `<tr><td>${esc(u.name)}<br><span class="mut">${esc(u.username)}</span></td><td>${roleSel(u)}</td>
    <td>${u.active ? pill(u.must_change ? 'Temporary password' : 'Active', u.must_change ? 'warn' : 'ok') : pill('Disabled', 'bad')}</td>
    <td>${u.id === ME.id ? '<span class="mut">You</span>' : `<button class="s" onclick="resetPw(${u.id},'${esc(u.name).replace(/'/g, '')}',this)">Reset password</button><button class="s ${u.active ? 'r' : ''}" onclick="editStaff(${u.id},{active:${!u.active}},this)">${u.active ? 'Disable' : 'Enable'}</button>`}</td></tr>`).join('');
  const log = auditRows.slice(0, 100).map(a => `<tr><td>${esc(a.ts.replace('T', ' '))}</td><td>${esc(a.username || '')}</td><td>${esc(a.action.replace(/_/g, ' '))}</td><td>${esc(a.ref || '')} <span class="mut">${esc(a.detail || '')}</span></td></tr>`).join('');
  return `<h2>Staff</h2><p class="mut">Desk staff enrol members, collect premiums and submit claims. Reviewers also approve, pay and spot-check claims, but never ones they submitted. Admins also manage staff.</p>
  <div class="card"><div class="g"><label>Full name<input id="sn"></label><label>Username<input id="su" autocapitalize="none" placeholder="e.g. ama.m"></label>
  <label>Role<select id="sr"><option value="desk">Desk</option><option value="reviewer">Reviewer</option><option value="admin">Admin</option></select></label><label>Temporary password<input id="sp" type="text" autocomplete="off"></label></div>
  <button class="b" onclick="addStaff(this)">Add staff member</button></div>
  <div class="card tw"><table><tr><th>Name</th><th>Role</th><th>Status</th><th>Action</th></tr>${rows}</table></div>
  <h2>Activity log</h2><p class="mut">The latest 100 actions.</p><div class="card tw"><table><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th></tr>${log || '<tr><td colspan="4" class="mut">Nothing yet.</td></tr>'}</table></div>`;
}

function render() {
  if (!CFG || !ME) return;
  if (tab === 5 && !can('admin')) tab = 0;
  shell(true);
  $('#nav').innerHTML = T.map((t, i) => i === 5 && !can('admin') ? '' : `<button class="${i === tab ? 'on' : ''}" onclick="go(${i})">${t}${i === 4 && sum.needs_review ? `<span class="n">${sum.needs_review}</span>` : ''}</button>`).join('');
  $('#m').innerHTML = [vEnrol, vPay, vCheck, vClaim, vLedger, vStaff][tab]();
}

(async () => {
  $('#demo').addEventListener('click', e => { const a = e.target.dataset.act; if (a === 'seed') seed(e.target); if (a === 'reset') wipe(e.target); });
  try { ME = await api('me'); } catch (e) {
    if (/internet/.test(e.message)) return $('#m').innerHTML = `<div class="card">${pill('Offline', 'bad')} <span class="mut">${esc(e.message)}</span></div>`;
    return showLogin();
  }
  try { await start(); } catch (e) { $('#m').innerHTML = `<div class="card">${pill('Offline', 'bad')} <span class="mut">${esc(e.message)}</span></div>`; }
})();

// ---------- installable app ----------
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
let installEvt = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installEvt = e; $('#install').hidden = false; });
$('#install').addEventListener('click', async () => {
  if (!installEvt) return;
  installEvt.prompt(); await installEvt.userChoice; installEvt = null; $('#install').hidden = true;
});
window.addEventListener('appinstalled', () => { $('#install').hidden = true; toast('CAIRE Cover is installed. Open it from your home screen.'); });
