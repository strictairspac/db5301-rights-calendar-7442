import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

const publishKeys = new Map();

function newKey() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function keyFor(identity, text) {
  const slot = `${identity}:${text}`;
  if (!publishKeys.has(slot)) publishKeys.set(slot, newKey());
  return publishKeys.get(slot);
}

function stamp(value) {
  if (!value) return '—';
  return esc(value.replace('T', ' ').replace(/([+-]\d{2}):\d{2}$/, ' $1'));
}

function slotRows(slots) {
  return slots.map(row=>[
    esc(row.screening_id), esc(row.title),
    `${stamp(row.original_start)}<br><small>${stamp(row.original_end)}</small>`,
    `${stamp(row.starts_at)}<br><small>${stamp(row.ends_at)}</small>`,
    row.delay_minutes ? `+${esc(row.delay_minutes)} 分` : '保留原档',
  ]);
}

function unplacedRows(rows) {
  return rows.map(row=>[
    esc(row.screening_id), esc(row.title),
    `${stamp(row.original_start)}<br><small>${stamp(row.original_end)}</small>`,
    esc(row.earliest_start ? row.earliest_start : '—'), esc(row.reason),
  ]);
}

function programmeTable(programme) {
  if (!programme) return '<p class="empty">该影厅还没有已发布的节目单。</p>';
  return records(['场次','片名','原定时段','现排时段','顺延'], slotRows(programme.slots),
                 '现行节目单没有排入任何场次。');
}

function draftText(spec, programme) {
  if (!programme) return JSON.stringify(spec.sample, null, 2);
  return JSON.stringify({
    note: '',
    max_delay_minutes: 45,
    screenings: programme.slots.map(row=>({
      screening_id: row.screening_id, film_id: row.film_id, title: row.title,
      territory: row.territory, medium: row.medium,
      starts_at: row.original_start, ends_at: row.original_end,
    })),
    rights: [],
  }, null, 2);
}

export function programmePage(state, identity) {
  const spec = state.spec.programme;
  const auditoriums = state.auditoriums || [];
  if (!auditoriums.length) return heading(topic.modules[4], '尚未登记影厅。');
  const active = auditoriums.find(row=>row.id===identity) || auditoriums[0];
  const detail = state.programmeDetail?.auditorium?.id===active.id ? state.programmeDetail : null;
  const operator = state.session.role==='operator';
  const link = row=>`<a href="#programme/${row.id}" ${row.id===active.id?'aria-current="true"':''}>${esc(row.name)}</a>`;
  const programme = detail?.programme;
  const preview = state.preview?.auditorium_id===active.id ? state.preview : null;
  const draftState = state.programmeDraft?.auditorium_id===active.id ? state.programmeDraft.text : null;
  const previewBlock = preview ? panel('试排结果', `
    <dl class="metrics">${Object.entries(preview.summary).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>
    <h3>排入场次</h3>${records(['场次','片名','原定时段','现排时段','顺延'], slotRows(preview.slots), '本次没有场次排入。')}
    <h3>未排上的场次</h3>${records(['场次','片名','原定时段','最早可排','原因'], unplacedRows(preview.unplaced), '草案场次全部排入。')}`) : '';

  const bookingForm = operator ? `<form id="booking-form" class="field-grid">
    <label class="field"><span>包场名称</span><input name="reference" required maxlength="120" placeholder="例如：品牌点映包场"></label>
    <label class="field"><span>开始时间</span><input name="starts_at" required placeholder="2026-12-05T19:00+08:00" value="${esc(spec.sample_booking.starts_at)}"></label>
    <label class="field"><span>结束时间</span><input name="ends_at" required placeholder="2026-12-05T20:00+08:00" value="${esc(spec.sample_booking.ends_at)}"></label>
    <div class="actions form-actions"><button>确认包场</button></div></form>` : '';

  const bookingRows = (detail?.bookings || []).map(row=>[
    esc(row.reference),
    `${stamp(row.starts_at)}<br><small>${stamp(row.ends_at)}</small>`,
    row.status==='confirmed'?'已确认':'已取消', esc(row.created_by),
    row.status==='confirmed'&&operator?`<button class="secondary" type="button" data-cancel="${row.id}">取消</button>`:'',
  ]);

  const draft = `<form id="programme-form">
    <label class="field data"><span>改版草案（JSON）</span>
      <small>整座影厅一次性切换。场次须填写影厅对应的地域与媒介，授权可在地域或媒介填 *。</small>
      <textarea id="draft" rows="16" spellcheck="false">${esc(draftState ?? draftText(spec, programme))}</textarea></label>
    <div class="actions form-actions">${operator?'<button type="button" id="preview">试排</button><button>发布新版</button>':''}<a class="secondary" href="#programme/${active.id}">重新载入</a></div></form>`;

  const versions = (detail?.versions || []).map(row=>[
    `v${esc(row.version)}`, row.status==='published'?'现行':'已被替换',
    esc(row.note || '—'), esc(row.created_by), esc(date(row.created_at)),
  ]);

  return `${heading(topic.modules[4], spec.summary)}
    <div class="spread"><section aria-label="影厅目录">${records(['影厅','地域','媒介','现行版本','发布时间'],
      auditoriums.map(row=>[link(row), esc(row.territory), esc(row.medium),
        row.version?`v${esc(row.version)}`:'—', esc(date(row.published_at))]))}</section>
    <aside class="brief"><h2>${esc(active.name)}</h2><p>${esc(active.code)} · ${esc(active.territory)} · ${esc(active.medium)}</p>
    <p class="note">${esc(spec.guidance)}</p></aside></div>
    ${panel('现行节目单', programmeTable(programme)
      + (programme?.unplaced?.length ? `<h3>未排上的场次</h3>${records(['场次','片名','原定时段','最早可排','原因'], unplacedRows(programme.unplaced))}` : ''))}
    ${previewBlock}
    ${panel('整批改版草案', draft)}
    ${panel('包场登记', records(['包场','时段','状态','登记人','操作'], bookingRows, '该影厅没有包场登记。') + bookingForm)}
    ${panel('节目单版本', records(['版本','状态','说明','操作人','发布时间'], versions, '尚无版本记录。'))}`;
}

function readDraft(state, identity) {
  const text = document.getElementById('draft').value;
  state.programmeDraft = {auditorium_id:identity, text};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('改版草案须为合法 JSON。');
  }
}

export function bindProgramme(state, api, update, fail) {
  const identity = state.programmeDetail?.auditorium?.id;
  if (!identity) return;
  document.getElementById('preview')?.addEventListener('click', async event=>{
    const button = event.currentTarget;
    button.disabled = true;
    try {
      state.preview = {...await api(`/auditoriums/${identity}/preview`, {
        method:'POST', data:{draft:readDraft(state, identity)}}), auditorium_id:identity};
      await update();
      document.getElementById('message').textContent = '试排完成，请核对排入与未排上的场次。';
    } catch (error) { fail(error); }
    finally { button.disabled = false; }
  });
  submit('programme-form', async()=>{
    const text = document.getElementById('draft').value;
    const body = readDraft(state, identity);
    const result = await api(`/auditoriums/${identity}/publish`, {
      method:'POST', headers:{'Idempotency-Key':keyFor(identity, text)}, data:{draft:body}});
    publishKeys.delete(`${identity}:${text}`);
    state.preview = null;
    state.programmeDraft = null;
    state.notice = `已发布 v${result.version}：排入 ${result.summary['排入场次']} 场，未排 ${result.summary['未排场次']} 场。`
      + (result.unplaced.length ? '未排上的场次已列入现行节目单。' : '');
    await update();
  }, fail);
  submit('booking-form', async data=>{
    await api(`/auditoriums/${identity}/bookings`, {
      method:'POST', headers:{'Idempotency-Key':newKey()},
      data:Object.fromEntries(data)});
    await update();
  }, fail);
  document.querySelectorAll('[data-cancel]').forEach(button=>{
    button.addEventListener('click', async()=>{
      button.disabled = true;
      try {
        await api(`/auditoriums/${identity}/bookings/${button.dataset.cancel}/cancel`, {method:'POST'});
        await update();
      } catch (error) { fail(error); button.disabled = false; }
    });
  });
}
