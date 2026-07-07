// シフト表画面のロジック。テンプレート側から渡される window.SCHEDULE_DATA を参照する。
const _D = window.SCHEDULE_DATA;
const IS_CONFIRMED      = _D.isConfirmed;
const TOTAL_SHORTAGE    = _D.totalShortage;
const UNSUBMITTED_COUNT = _D.unsubmittedCount;
const PERIOD_ID         = _D.periodId;
const EXPORT_EMP_ORDER  = _D.exportEmpOrder;
const CELL_NOTES        = _D.cellNotes;
let editTool = 'select';

// ── ポジション切替 ────────────────────────────────────────────
let CURRENT_POS = _D.pos;
const STAFFING_DATA = _D.staffing;

// 前回この期間で表示していたポジションを復元（URLに?posの指定がない場合のみ）
(function() {
  if (new URLSearchParams(location.search).has('pos')) return;
  const saved = sessionStorage.getItem('schedPos_' + PERIOD_ID);
  if (saved && saved !== CURRENT_POS) switchPos(saved);
})();

function switchPos(newPos) {
  if (CURRENT_POS === newPos) return;
  CURRENT_POS = newPos;

  document.getElementById('emp-tbody-hall').classList.toggle('d-none', newPos !== 'hall');
  document.getElementById('emp-tbody-kitchen').classList.toggle('d-none', newPos !== 'kitchen');

  document.querySelectorAll('.pos-tab-btn').forEach(btn => {
    const active = btn.dataset.pos === newPos;
    btn.className = `btn btn-sm pos-tab-btn ${active ? 'btn-success' : 'btn-outline-secondary'}`;
  });

  const footer = document.getElementById('pos-label-footer');
  if (footer) footer.textContent = '表示中：' + (newPos === 'hall' ? 'ホール' : 'キッチン');

  _updateHeaderBadges(newPos);
  sessionStorage.setItem('schedPos_' + PERIOD_ID, newPos);
}

function _updateHeaderBadges(pos) {
  document.querySelectorAll('thead th[data-date]').forEach(th => {
    const ds  = th.dataset.date;
    const stB = STAFFING_DATA[`${ds}_breakfast_${pos}`] || {};
    const stD = STAFFING_DATA[`${ds}_dinner_${pos}`]    || {};

    th.classList.remove('short-staff', 'short-leader');
    if (stB.short_staff || stD.short_staff)       th.classList.add('short-staff');
    else if (stB.short_leader || stD.short_leader) th.classList.add('short-leader');

    const container = th.querySelector('.pos-count-badges');
    if (!container) return;

    const mkBadge = (st, icon, label) => {
      if (st.count === undefined) return '';
      const cls = st.short_staff ? 'count-short' : st.over_staff ? 'count-over' : st.short_leader ? 'count-leader' : 'count-ok';
      const note = st.over_staff ? '（過剰）' : '';
      return `<span class="count-badge ${cls}" title="${label}${note}"><i class="bi ${icon}"></i> ${st.count}/${st.min}</span>`;
    };
    container.innerHTML = mkBadge(stB, 'bi-sunrise', '朝食') + mkBadge(stD, 'bi-moon-stars', 'ディナー');
  });
}

// ── タイムライン ──────────────────────────────────────────────
const TL_ASGN = _D.tlAsgn;

// スマホ：ページ本体のスクロールとグリッド内スクロールが二重にならないよう、
// グリッドの高さをヘッダー・バナー類の実際の高さを差し引いたビューポート残り分にJSで再計算する
function fitSchedGrid() {
  const grid = document.querySelector('.sched-grid');
  if (!grid) return;
  if (window.innerWidth >= 768) { grid.style.maxHeight = ''; return; }
  const top = grid.getBoundingClientRect().top;
  const bottomNav = document.querySelector('.bottom-nav');
  const navH = (bottomNav && getComputedStyle(bottomNav).display !== 'none') ? bottomNav.offsetHeight : 0;
  const available = window.innerHeight - top - navH - 8;
  grid.style.maxHeight = Math.max(240, available) + 'px';
}
fitSchedGrid();
window.addEventListener('resize', fitSchedGrid);
window.addEventListener('orientationchange', fitSchedGrid);

// スクロール復元 or 今日の列へ自動スクロール
{
  const grid = document.querySelector('.sched-grid');
  if (grid) {
    const saved = sessionStorage.getItem('schedScroll');
    if (saved) {
      const { top, left } = JSON.parse(saved);
      grid.scrollTop = top;
      grid.scrollLeft = left;
      sessionStorage.removeItem('schedScroll');
    } else {
      const todayTh = grid.querySelector('thead th.today-col');
      if (todayTh) {
        const empColW = grid.querySelector('thead th.emp-col')?.offsetWidth || 90;
        grid.scrollLeft = Math.max(0, todayTh.offsetLeft - empColW - 8);
      }
    }
  }
}

function toggleSg(id) {
  const body    = document.getElementById(id);
  if (!body) return;
  const header  = body.previousElementSibling;
  const chevron = header?.querySelector('.sg-chevron');
  const open    = body.style.display === 'none';
  body.style.display = open ? 'flex' : 'none';
  if (chevron) chevron.classList.toggle('open', open);
}

// PDFプレビューは常に最新内容を表示させたいため、毎回キャッシュバスター付きURLで新規タブを開く
function openPdfPreview(el) {
  const url = el.href + (el.href.includes('?') ? '&' : '?') + '_=' + Date.now();
  window.open(url, '_blank', 'noopener');
  return false;
}

function openTimeline(ds) {
  const day = TL_ASGN[ds] || {};
  const dow = ['月','火','水','木','金','土','日'];
  const d = new Date(ds);
  document.getElementById('timelineTitle').textContent =
    ds.slice(5).replace('-','/') + '（' + dow[d.getDay()] + '）　タイムライン';

  const hours = Array.from({length:18}, (_,i)=>i+5);
  let html = '<table class="table table-bordered table-sm" style="min-width:500px;font-size:.78rem">';
  html += '<thead class="table-dark"><tr><th style="width:90px">スタッフ</th>';
  hours.forEach(h => { html += `<th class="text-center px-0" style="width:28px">${h}</th>`; });
  html += '</tr></thead><tbody>';

  const allItems = [
    ...(day['breakfast'] || []).map(a=>({...a, slot:'breakfast'})),
    ...(day['dinner']    || []).map(a=>({...a, slot:'dinner'})),
  ];
  allItems.sort((a,b) => {
    let ia = EXPORT_EMP_ORDER.indexOf(a.empId);
    let ib = EXPORT_EMP_ORDER.indexOf(b.empId);
    if (ia === -1) ia = EXPORT_EMP_ORDER.length;
    if (ib === -1) ib = EXPORT_EMP_ORDER.length;
    if (ia !== ib) return ia - ib;
    return a.emp.localeCompare(b.emp);
  });

  const SLOT_RANGE = { breakfast:[6,11], dinner:[17,23] };
  allItems.forEach(a => {
    let s, e;
    if (a.rs && a.re) {
      s = parseT(a.rs); e = parseT(a.re);
    } else if (a.t) {
      const m = a.t.match(/(\d+:\d+)[〜~](\d+:\d+)/);
      if (m) { s = parseT(m[1]); e = parseT(m[2]); }
    }
    if (!s) [s,e] = SLOT_RANGE[a.slot];
    const clr = a.pos==='hall' ? '#dbeafe' : '#dcfce7';
    const lbl = a.pos==='hall' ? 'H' : 'K';
    const mark = a.reinf ? '<sup>応</sup>' : (a.rs && a.re ? '<sup>変</sup>' : '');
    html += `<tr><td>${a.emp}${mark}</td>`;
    hours.forEach(h => {
      const inRange = h >= s && h < e;
      html += `<td style="background:${inRange?clr:''};text-align:center">${inRange?lbl:''}</td>`;
    });
    html += '</tr>';
  });

  if (allItems.length === 0) {
    html += `<tr><td colspan="${hours.length+1}" class="text-center text-muted">配置なし</td></tr>`;
  }
  html += '</tbody></table>';
  document.getElementById('timelineBody').innerHTML = html;
  new bootstrap.Modal(document.getElementById('timelineModal')).show();
}

function parseT(s) {
  const [h,m] = s.split(':').map(Number);
  return h + (m>=30 ? 0.5 : 0);
}

let _modal = null;
let _cell  = null;
let _selectedPos = 'hall';

const SLOT_PRESETS = {
  breakfast: [
    { label: '全時間', start: '06:00', end: '11:00' },
    { label: '前半',   start: '06:00', end: '09:00' },
    { label: '後半',   start: '09:00', end: '11:00' },
  ],
  dinner: [
    { label: '全時間', start: '17:00', end: '22:00' },
    { label: '前半',   start: '17:00', end: '20:00' },
    { label: '後半',   start: '19:00', end: '22:00' },
  ],
};

function selectPos(pos) {
  _selectedPos = pos;
  document.getElementById('btn-hall').className    =
    'asgn-pos-btn flex-grow-1 text-center' + (pos === 'hall'    ? ' hall'    : '');
  document.getElementById('btn-kitchen').className =
    'asgn-pos-btn flex-grow-1 text-center' + (pos === 'kitchen' ? ' kitchen' : '');
}

function setTimeMode(mode) {
  document.querySelector(`input[name="time-mode"][value="${mode}"]`).checked = true;
  document.getElementById('opt-default').classList.toggle('selected', mode === 'default');
  document.getElementById('opt-custom').classList.toggle('selected',  mode === 'custom');
  document.getElementById('custom-time-section').style.display = mode === 'custom' ? 'block' : 'none';
  document.getElementById('reinf-start').classList.remove('is-invalid');
  document.getElementById('reinf-end').classList.remove('is-invalid');
  document.getElementById('reinf-time-error').classList.add('d-none');
}

function applyPreset(start, end, el) {
  document.getElementById('reinf-start').value = start;
  document.getElementById('reinf-end').value   = end;
  document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
}

function openConfirmDialog() {
  let html = '';
  const hasIssue = TOTAL_SHORTAGE > 0 || UNSUBMITTED_COUNT > 0;
  if (hasIssue) {
    html += '<div class="mb-3" style="font-size:.85rem;color:#fbbf24"><i class="bi bi-exclamation-triangle-fill me-1"></i>以下の問題があります：</div>';
    if (TOTAL_SHORTAGE > 0) {
      html += `<div class="mb-2 small"><i class="bi bi-x-circle-fill text-danger me-1"></i>人員不足の日が <strong>${TOTAL_SHORTAGE}日</strong> あります</div>`;
    }
    if (UNSUBMITTED_COUNT > 0) {
      html += `<div class="mb-2 small"><i class="bi bi-person-x-fill text-warning me-1"></i>希望シフト未提出が <strong>${UNSUBMITTED_COUNT}名</strong> います</div>`;
    }
    html += '<hr style="border-color:#334155;margin:.75rem 0"><div class="small" style="color:#94a3b8">このまま確定しますか？</div>';
  } else {
    html = '<div class="text-center py-2"><i class="bi bi-check-circle-fill text-success" style="font-size:2rem"></i><div class="mt-2" style="font-size:.9rem;color:#e2e8f0">問題は検出されませんでした。<br>シフトを確定します。</div></div>';
  }
  document.getElementById('confirm-dialog-body').innerHTML = html;
  document.getElementById('btn-do-confirm').onclick = () => document.getElementById('confirm-form').submit();
  new bootstrap.Modal(document.getElementById('confirmDialog')).show();
}

function openAssignModal(cell) {
  if (IS_CONFIRMED) {
    new bootstrap.Modal(document.getElementById('unconfirmDialog')).show();
    return;
  }
  if (editTool !== 'select') {
    quickPaint(cell, editTool);
    return;
  }
  _cell = cell;
  const { empName, date, slot, assigned, pos, reinfStart, reinfEnd, shiftTime, hasRequest } = cell.dataset;
  const slotJp = slot === 'breakfast' ? '朝食' : 'ディナー';

  document.getElementById('asgn-name').textContent = empName;
  document.getElementById('asgn-sub').textContent  =
    date.slice(5).replace('-', '/') + '（' + slotJp + '）';

  selectPos(pos || CURRENT_POS);

  const hasReq = hasRequest === 'true';
  const optDef  = document.getElementById('opt-default');
  const radioDef = optDef.querySelector('input[type=radio]');
  radioDef.disabled = !hasReq;
  optDef.classList.toggle('disabled', !hasReq);

  document.getElementById('shift-time-hint').textContent = hasReq
    ? (shiftTime || '時間指定なし')
    : '希望シフト未提出';

  if (hasReq) {
    document.getElementById('custom-mode-title').textContent = '時間を変更する';
    document.getElementById('custom-mode-sub').textContent   = '希望時間より短縮・延長する場合';
  } else {
    document.getElementById('custom-mode-title').textContent = '応援をお願いする';
    document.getElementById('custom-mode-sub').textContent   = '希望にない日に出勤してもらう場合';
  }

  const presets = SLOT_PRESETS[slot] || [];
  document.getElementById('preset-chips').innerHTML = presets.map(p =>
    `<span class="preset-chip" onclick="applyPreset('${p.start}','${p.end}',this)">${p.label}</span>`
  ).join(' ');

  if (reinfStart && reinfEnd) {
    setTimeMode('custom');
    document.getElementById('reinf-start').value = reinfStart;
    document.getElementById('reinf-end').value   = reinfEnd;
  } else if (!hasReq) {
    setTimeMode('custom');
    if (presets.length) applyPreset(presets[0].start, presets[0].end, null);
  } else {
    setTimeMode('default');
    if (presets.length) applyPreset(presets[0].start, presets[0].end, null);
  }

  document.getElementById('remove-section').style.display =
    assigned === 'true' ? 'block' : 'none';

  document.getElementById('btn-submit').innerHTML =
    `<i class="bi bi-person-check me-1"></i>${assigned === 'true' ? '担当を変更する' : '担当として追加'}`;

  const noteKey = `${cell.dataset.empId}_${cell.dataset.date}`;
  document.getElementById('cell-note-input').value = CELL_NOTES[noteKey] || cell.dataset.cellNote || '';

  if (!_modal) {
    _modal = new bootstrap.Modal(document.getElementById('assignModal'));
    document.getElementById('assignModal').addEventListener('hide.bs.modal', _saveCellNote);
  }
  _modal.show();
}

function doSubmit() {
  const mode = document.querySelector('input[name="time-mode"]:checked').value;
  const d    = _cell.dataset;
  if (mode === 'custom') {
    const start = document.getElementById('reinf-start').value;
    const end   = document.getElementById('reinf-end').value;
    if (!start || !end) {
      document.getElementById('reinf-start').classList.add('is-invalid');
      document.getElementById('reinf-end').classList.add('is-invalid');
      document.getElementById('reinf-time-error').classList.remove('d-none');
      return;
    }
    document.getElementById('reinf-start').classList.remove('is-invalid');
    document.getElementById('reinf-end').classList.remove('is-invalid');
    document.getElementById('reinf-time-error').classList.add('d-none');
    const hasReq = d.hasRequest === 'true';
    callAssign({ employee_id: +d.empId, date: d.date, time_slot: d.slot,
                 position: _selectedPos, action: 'add',
                 is_reinforcement: !hasReq, reinf_start: start, reinf_end: end });
  } else {
    callAssign({ employee_id: +d.empId, date: d.date, time_slot: d.slot,
                 position: _selectedPos, action: 'add' });
  }
}

function doRemove() {
  const d = _cell.dataset;
  callAssign({ employee_id: +d.empId, date: d.date, time_slot: d.slot, position: d.pos, action: 'remove' });
}

function _saveCellNote() {
  if (!_cell) return;
  const note    = document.getElementById('cell-note-input').value.trim();
  const empId   = +_cell.dataset.empId;
  const date    = _cell.dataset.date;
  const noteKey = `${empId}_${date}`;
  const prev    = CELL_NOTES[noteKey] || _cell.dataset.cellNote || '';
  if (note === prev) return;
  CELL_NOTES[noteKey] = note;
  const td = _cell.closest('td');
  fetch(`/schedule/${PERIOD_ID}/cell_note`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ employee_id: empId, date, note })
  }).then(() => {
    const badge = td.querySelector('.cell-note-badge');
    if (note) {
      const icon = '<i class="bi bi-chat-fill" style="font-size:.55rem"></i> ';
      const text = note.length > 8 ? note.slice(0, 8) + '…' : note;
      if (badge) { badge.innerHTML = icon + text; badge.title = note; }
      else {
        const s = document.createElement('span');
        s.className = 'cell-note-badge'; s.title = note;
        s.innerHTML = icon + text;
        td.appendChild(s);
      }
    } else {
      if (badge) badge.remove();
    }
  });
}

// ── リロードなし割当更新 ─────────────────────────────────────
async function callAssign(payload) {
  const btnSubmit = document.getElementById('btn-submit');
  if (btnSubmit) { btnSubmit.disabled = true; btnSubmit.classList.add('btn-loading'); }

  try {
    const r = await fetch(`/schedule/${PERIOD_ID}/assign`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const res = await r.json();
    if (!res.ok) {
      showToast('エラー: ' + (res.error || '不明'), 'danger');
      return;
    }

    _modal.hide();

    // セルチップを即時更新
    _updateCellChip(_cell, payload);

    // 人員充足データを再取得してヘッダーバッジ＋不足グリッドを更新
    try {
      const sRes = await fetch(`/schedule/${PERIOD_ID}/staffing_json`);
      if (sRes.ok) {
        const sData = await sRes.json();
        Object.assign(STAFFING_DATA, sData.staffing);
        _updateHeaderBadges(CURRENT_POS);
        _refreshShortageGrid(sData.shortage_groups);
      }
    } catch(_) {
      // 非クリティカル：次回ページ読み込み時に反映される
    }
  } finally {
    if (btnSubmit) { btnSubmit.disabled = false; btnSubmit.classList.remove('btn-loading'); }
  }
}

function _updateCellChip(cell, payload) {
  const { action, position, is_reinforcement, reinf_start, reinf_end } = payload;

  if (action === 'add' && position === CURRENT_POS) {
    cell.dataset.assigned   = 'true';
    cell.dataset.pos        = position;
    cell.dataset.reinfStart = reinf_start || '';
    cell.dataset.reinfEnd   = reinf_end   || '';

    const posLabel  = position === 'hall' ? 'H' : 'K';
    const chipCls   = position === 'hall' ? 'chip-hall' : 'chip-kitchen';
    const hasCustom = reinf_start && reinf_end;
    const shiftTime = hasCustom
      ? `${reinf_start}〜${reinf_end}`
      : (cell.dataset.shiftTime || '');

    const titleAttr = is_reinforcement ? 'title="希望にない応援"' : hasCustom ? 'title="希望時間から変更"' : '';
    const sup       = is_reinforcement ? '<sup style="font-size:.6rem">応</sup>' : hasCustom ? '<sup style="font-size:.6rem">変</sup>' : '';

    let html = `<button type="button" class="btn-quick-remove" onclick="quickRemove(event, this)" title="担当を外す"><i class="bi bi-x"></i></button>`;
    html += `<span class="${chipCls}${is_reinforcement ? ' opacity-75' : ''}" ${titleAttr}>${posLabel}${sup}</span>`;
    if (shiftTime) html += `<div style="font-size:.6rem;color:var(--text-muted);line-height:1.15">${shiftTime}</div>`;
    cell.innerHTML = html;
    cell.classList.remove('has-unassigned-request');

  } else if (action === 'remove') {
    cell.dataset.assigned   = 'false';
    cell.dataset.pos        = '';
    cell.dataset.reinfStart = '';
    cell.dataset.reinfEnd   = '';

    const hasRequest = cell.dataset.hasRequest === 'true';
    const reqTime    = cell.dataset.shiftTime || '';

    if (hasRequest) {
      let html = `<span class="chip-unassigned" title="希望提出済み（${reqTime}）ですが、アサインされていません">希</span>`;
      if (reqTime) html += `<div style="font-size:.6rem;color:#94a3b8;line-height:1.15">${reqTime}</div>`;
      cell.innerHTML = html;
      cell.classList.add('has-unassigned-request');
    } else {
      cell.innerHTML = '<span class="chip-empty">+</span>';
      cell.classList.remove('has-unassigned-request');
    }
  }
  // action==='add' && position !== CURRENT_POS → 別ポジション割当、当ビューの見た目は変わらず
}

function _refreshShortageGrid(groups) {
  groups.forEach(g => {
    const cell = document.querySelector(`.sg-cell[data-slot="${g.slot}"][data-pos="${g.pos}"]`);
    if (!cell) return;

    const state = g.has_short_staff ? 'staff' : g.has_short_leader ? 'leader' : 'ok';
    cell.className = `sg-cell sg-${state}`;

    const header = cell.querySelector('.sg-header');
    let bodyEl   = cell.querySelector('.sg-body');
    if (!header) return;

    const cellId = `sg-dyn-${g.slot.replace(/\s/g,'')}-${g.pos.replace(/\s/g,'')}`;

    if (state === 'ok') {
      header.className = 'sg-header';
      header.removeAttribute('onclick');
      header.removeAttribute('tabindex');
      header.removeAttribute('role');
      header.onkeydown = null;
      header.innerHTML = `
        <span class="sg-label">${g.slot}<span style="opacity:.5;margin:0 2px">/</span>${g.pos}</span>
        <i class="bi bi-check-circle-fill ms-auto" style="color:#22c55e;font-size:.9rem"></i>`;
      if (bodyEl) bodyEl.style.display = 'none';
    } else {
      header.className = 'sg-header sg-expandable';
      header.setAttribute('role', 'button');
      header.setAttribute('tabindex', '0');
      header.onclick  = () => toggleSg(cellId);
      header.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') toggleSg(cellId); };
      header.innerHTML = `
        <span class="sg-label">${g.slot}<span style="opacity:.5;margin:0 2px">/</span>${g.pos}</span>
        <span class="sg-count ms-auto">${g.short_count}日</span>
        <i class="bi bi-chevron-down sg-chevron" style="font-size:.7rem"></i>`;

      if (!bodyEl) {
        bodyEl = document.createElement('div');
        cell.appendChild(bodyEl);
      }
      bodyEl.id        = cellId;
      bodyEl.className = 'sg-body';
      bodyEl.style.display = 'none';
      bodyEl.innerHTML = g.chips.map(c =>
        `<span class="shortage-chip ${c.is_staff ? 'chip-staff' : 'chip-leader'}"
               title="${c.count}名/最低${c.min}名">${c.label}</span>`
      ).join('');
    }
  });
}

function showToast(msg, type) {
  const tc = document.querySelector('.toast-container');
  if (!tc) return;
  const el = document.createElement('div');
  el.className = `toast show align-items-center text-bg-${type} border-0`;
  el.setAttribute('role', 'alert');
  el.innerHTML = `<div class="d-flex"><div class="toast-body">${msg}</div>
    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
  tc.appendChild(el);
  if (type !== 'danger') { // エラーは見逃し防止のため手動で閉じるまで表示
    setTimeout(() => bootstrap.Toast.getOrCreateInstance(el).hide(), 4000);
  }
}

/* ── 変更履歴 ── */
async function openHistory(periodId) {
  const modal = new bootstrap.Modal(document.getElementById('historyModal'));
  const body  = document.getElementById('historyBody');
  body.innerHTML = '<div class="text-center py-3 text-muted small">読み込み中…</div>';
  modal.show();
  try {
    const res = await fetch(`/schedule/${periodId}/history`);
    const logs = await res.json();
    if (!logs.length) {
      body.innerHTML = '<div class="text-muted small py-3 text-center">変更履歴がありません</div>';
      return;
    }
    const _ACT = { add: {icon:'bi-plus-circle-fill', cls:'text-success'}, remove: {icon:'bi-dash-circle-fill', cls:'text-danger'} };
    body.innerHTML = logs.map(l => {
      const a = _ACT[l.action] || {icon:'bi-circle', cls:'text-muted'};
      const label = l.action === 'add' ? '追加' : '削除';
      return `<div class="d-flex align-items-center gap-2 py-2 border-bottom border-secondary">
        <i class="bi ${a.icon} ${a.cls}" style="font-size:1rem;flex-shrink:0"></i>
        <div class="flex-grow-1 small">
          <span class="fw-semibold">${l.emp_name || '(不明)'}</span>
          <span class="text-muted mx-1">—</span>
          <span>${l.date} ${l.slot_label} ${l.pos_label}</span>
          <span class="badge ${l.action==='add'?'bg-success':'bg-danger'} bg-opacity-75 ms-1">${label}</span>
        </div>
        <div class="text-muted" style="font-size:.7rem;white-space:nowrap">${(l.changed_at||'').slice(5,16)}</div>
      </div>`;
    }).join('');
  } catch(e) {
    body.innerHTML = '<div class="text-danger small py-2">履歴の取得に失敗しました</div>';
  }
}

function editReservation(el) {
  if (el.querySelector('input')) return;
  const date = el.dataset.date;
  const curB = el.dataset.b === '0' ? '' : el.dataset.b;
  const curD = el.dataset.d === '0' ? '' : el.dataset.d;
  const orig = el.innerHTML;
  el.innerHTML = `
    <div class="d-flex flex-column gap-1" style="min-width:64px">
      <input type="number" min="0" max="999" class="form-control form-control-sm res-in-b"
             style="font-size:.82rem;padding:4px 6px;min-height:34px;-moz-appearance:textfield"
             placeholder="朝" value="${curB}">
      <input type="number" min="0" max="999" class="form-control form-control-sm res-in-d"
             style="font-size:.82rem;padding:4px 6px;min-height:34px;-moz-appearance:textfield"
             placeholder="夜" value="${curD}">
    </div>`;
  const inB = el.querySelector('.res-in-b');
  const inD = el.querySelector('.res-in-d');
  inB.focus();
  let saved = false;
  function save() {
    if (saved) return; saved = true;
    const prevB = curB || '0', prevD = curD || '0';
    const b = parseInt(inB.value || '0', 10) || 0;
    const d = parseInt(inD.value || '0', 10) || 0;
    el.dataset.b = b; el.dataset.d = d;
    el.innerHTML = `
      <div><i class="bi bi-sunrise"></i> <span class="res-b">${b || '—'}</span></div>
      <div><i class="bi bi-moon-stars"></i> <span class="res-d">${d || '—'}</span></div>`;
    function revert() {
      el.dataset.b = prevB; el.dataset.d = prevD;
      el.innerHTML = `
        <div><i class="bi bi-sunrise"></i> <span class="res-b">${prevB !== '0' ? prevB : '—'}</span></div>
        <div><i class="bi bi-moon-stars"></i> <span class="res-d">${prevD !== '0' ? prevD : '—'}</span></div>`;
      showToast('予約客数の保存に失敗しました。元の値に戻しました', 'danger');
    }
    fetch(`/schedule/${PERIOD_ID}/reservation`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, breakfast: b, dinner: d})
    }).then(r => {
      if (!r.ok) { revert(); return; }
      el.style.transition = 'background .15s';
      el.style.background = '#bbf7d0';
      setTimeout(() => { el.style.background = ''; }, 800);
    }).catch(revert);
  }
  function cancelIfOutside(ev) {
    if (!el.contains(ev.relatedTarget)) save();
  }
  inB.addEventListener('blur', cancelIfOutside);
  inD.addEventListener('blur', cancelIfOutside);
  inB.addEventListener('keydown', e => { if (e.key === 'Enter') inD.focus(); if (e.key === 'Escape') { saved = true; el.innerHTML = orig; } });
  inD.addEventListener('keydown', e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { saved = true; el.innerHTML = orig; } });
}

function editNote(el) {
  const date = el.dataset.date;
  const periodId = el.dataset.period;
  const endpoint = el.dataset.endpoint || 'note';
  const current = el.textContent.trim();
  const input = document.createElement('input');
  input.type = 'text'; input.value = current;
  input.maxLength = 200;
  input.className = 'form-control form-control-sm'; input.style.minWidth = '80px';
  el.replaceWith(input); input.focus();
  function save() {
    const note = input.value.trim();
    const span = document.createElement('span');
    span.className = 'note-cell small text-muted';
    span.dataset.date = date;
    span.dataset.period = periodId;
    span.dataset.endpoint = endpoint;
    span.setAttribute('role', 'button');
    span.setAttribute('tabindex', '0');
    span.onclick = function(){ editNote(this); };
    span.onkeydown = function(e){ if(e.key==='Enter') editNote(this); };
    span.textContent = note;
    input.replaceWith(span);
    function revert() {
      span.textContent = current;
      showToast('メモの保存に失敗しました。元の内容に戻しました', 'danger');
    }
    fetch(`/schedule/${periodId}/${endpoint}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, note})
    }).then(r => {
      if (!r.ok) { revert(); return; }
      span.style.transition = 'background .15s';
      span.style.background = '#bbf7d0';
      setTimeout(() => { span.style.background = ''; }, 800);
    }).catch(revert);
  }
  input.addEventListener('blur', save);
  input.addEventListener('keydown', e => { if(e.key==='Enter') input.blur(); });
}

function quickRemove(event, el) {
  event.stopPropagation(); // セルクリック時のアサインモーダル表示を防ぐ
  if (IS_CONFIRMED) {
    new bootstrap.Modal(document.getElementById('unconfirmDialog')).show();
    return;
  }
  
  const cell = el.closest('.cell-half');
  if (!cell) return;
  
  const d = cell.dataset;
  
  // 一時的にローディング状態にする
  el.disabled = true;
  el.innerHTML = '<span class="spinner-border spinner-border-sm" style="width:8px;height:8px;border-width:1px;"></span>';
  
  _cell = cell; // callAssign 内で _cell を参照するためセット
  
  callAssign({ 
    employee_id: +d.empId, 
    date: d.date, 
    time_slot: d.slot, 
    position: d.pos, 
    action: 'remove' 
  });
}

function setEditTool(tool) {
  editTool = tool;
  document.querySelectorAll('#edit-tool-group button').forEach(btn => {
    const isSelf = btn.id === 'tool-' + tool;
    if (tool === 'select') {
      btn.className = `btn btn-sm btn-outline-light ${isSelf ? 'active' : ''}`;
    } else {
      const colorCls = tool === 'hall' ? 'btn-outline-primary' : tool === 'kitchen' ? 'btn-outline-success' : 'btn-outline-danger';
      btn.className = `btn btn-sm ${colorCls} ${isSelf ? 'active' : ''}`;
    }
  });
  
  const grid = document.querySelector('.sched-grid');
  if (grid) {
    grid.classList.remove('paint-mode-hall', 'paint-mode-kitchen', 'paint-mode-remove');
    if (tool !== 'select') {
      grid.classList.add('paint-mode-' + tool);
    }
  }
}

async function quickPaint(cell, tool) {
  const d = cell.dataset;
  if (tool === 'remove' && d.assigned !== 'true') return;
  if (tool === 'hall' && d.assigned === 'true' && d.pos === 'hall') return;
  if (tool === 'kitchen' && d.assigned === 'true' && d.pos === 'kitchen') return;
  
  cell.style.opacity = '0.5';
  _cell = cell;
  
  if (tool === 'remove') {
    await callAssign({ 
      employee_id: +d.empId, 
      date: d.date, 
      time_slot: d.slot, 
      position: d.pos, 
      action: 'remove' 
    });
  } else {
    const isReinf = d.hasRequest !== 'true';
    await callAssign({
      employee_id: +d.empId,
      date: d.date,
      time_slot: d.slot,
      position: tool,
      action: 'add',
      is_reinforcement: isReinf
    });
  }
  cell.style.opacity = '';
}
