import './style.css';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const $ = id => document.getElementById(id);
const log = $('log'), form = $('form'), input = $('input'), send = $('send');

/* ---------- 小工具：动态内容一律 textContent；唯一例外是助手回复的
 * Markdown 渲染，且必须先经 DOMPurify 净化（LLM 输出不可信）。 ---------- */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function scrollBottom() { log.scrollTop = log.scrollHeight; }

marked.setOptions({ gfm: true, breaks: true });

function renderMarkdown(node, raw) {
  node.innerHTML = DOMPurify.sanitize(marked.parse(raw));
  for (const a of node.querySelectorAll('a')) {
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  }
  node._raw = raw;   // 流式增量在此基础上追加后整体重渲
}

/* ---------- 会话列表（localStorage 持久化，FR-20） ---------- */
const store = {
  load() { try { return JSON.parse(localStorage.getItem('fa.sessions')) || []; } catch { return []; } },
  save(list) { localStorage.setItem('fa.sessions', JSON.stringify(list)); },
  get active() { return localStorage.getItem('fa.active') || null; },
  set active(id) { id ? localStorage.setItem('fa.active', id) : localStorage.removeItem('fa.active'); },
};
let sessions = store.load();
let activeId = store.active;
let serverInfo = { model: '', base_url: '' };
let viewEpoch = 0;  // 每次切换视图 +1；流式回调据此判断"用户是否已离开发起时的视图"

/* ---------- 运行中轮次的内存缓冲 ----------
 * 事件先进缓冲再渲染：切走会话时 DOM 被清空，但缓冲不丢；切回来重放。
 * 轮次结束（done/error）后本轮已被服务端落库，缓冲即弃，回放走历史接口。 */
const liveTurns = {};      // sessionId -> turn
let pendingTurn = null;    // 新会话首条消息：session_id 未返回前暂存

function makeTurn(userText) {
  return { id: null, userText, events: [], done: false, ui: null };
}
function turnFor(id) {
  const turn = liveTurns[id];
  return turn && !turn.done ? turn : null;
}

function upsertSession(id, title) {
  const found = sessions.find(s => s.id === id);
  if (found) { if (title && !found.title) found.title = title; }
  else sessions.unshift({ id, title: title || id, ts: Date.now() });
  store.save(sessions);
}

function renderSessionList() {
  const box = $('sessionList');
  box.textContent = '';
  for (const s of sessions) {
    const item = el('div', 'sess' + (s.id === activeId ? ' active' : ''));
    item.appendChild(el('div', 't', s.title || s.id));
    const sub = el('div', 's', s.id);
    if (turnFor(s.id)) sub.appendChild(el('span', 'running', ' ● 运行中'));
    item.appendChild(sub);
    const del = el('button', 'del', '×');
    del.title = '从本地列表移除（不删除服务端数据）';
    del.addEventListener('click', ev => {
      ev.stopPropagation();
      sessions = sessions.filter(x => x.id !== s.id);
      store.save(sessions);
      if (activeId === s.id) newChat(); else renderSessionList();
    });
    item.appendChild(del);
    item.addEventListener('click', () => selectSession(s.id));
    box.appendChild(item);
  }
}

function updateSendState() {
  // 只在"当前视图对应的会话正在跑"时禁用；别的会话在跑不影响这里发新任务
  send.disabled = !!(activeId && turnFor(activeId));
}

/* ---------- 头部与产物面板（按会话） ---------- */
function providerLabel() {
  if (!serverInfo.base_url) return 'OpenAI';
  try { return new URL(serverInfo.base_url).host; } catch { return serverInfo.base_url; }
}
function renderHeader(workspaceDir) {
  const base = `${providerLabel()} · ${serverInfo.model}`;
  $('sessionInfo').textContent = activeId
    ? `会话 ${activeId} ｜ ${base}${workspaceDir ? ' ｜ ' + workspaceDir : ''}`
    : `新会话（发送首条消息后创建） ｜ ${base}`;
}

async function refreshArtifacts() {
  const box = $('artifacts');
  if (!activeId) { box.textContent = ''; box.appendChild(el('div', 'empty', '暂无产物')); return; }
  const forId = activeId;
  const resp = await fetch(`/api/sessions/${forId}/state`);
  if (!resp.ok || forId !== activeId) return;
  const state = await resp.json();
  renderHeader(state.workspace_dir);
  box.textContent = '';
  if (!state.artifacts.length) { box.appendChild(el('div', 'empty', '暂无产物')); return; }
  for (const a of state.artifacts) {
    const card = el('div', 'card');
    const idLine = el('div', 'id', a.artifact_id);
    idLine.appendChild(el('span', 'badge', 'v' + a.current_version));
    idLine.appendChild(el('span', 'badge', a.kind));
    card.appendChild(idLine);
    card.appendChild(el('div', 'meta', a.title));
    const history = el('div', 'meta');
    for (const h of a.history) history.appendChild(el('div', '', `v${h.v} ${h.change_summary}`));
    card.appendChild(history);
    const link = el('a', '', '打开当前版本');
    link.href = `/api/sessions/${forId}/artifacts/${a.artifact_id}/file`;
    link.target = '_blank'; link.rel = 'noopener';
    card.appendChild(link);
    box.appendChild(card);
  }
}

/* ---------- 消息渲染 ---------- */
function addMsg(cls, text) {
  const div = el('div', 'msg ' + cls, text);
  log.appendChild(div);
  scrollBottom();
  return div;
}

function addMdMsg(raw) {
  const div = el('div', 'msg bot md');
  renderMarkdown(div, raw);
  log.appendChild(div);
  scrollBottom();
  return div;
}

function newTrace() {
  const trace = el('details', 'trace');
  trace.open = true;
  const summary = el('summary');
  summary.appendChild(el('span', 'spin', '◌'));
  summary.appendChild(document.createTextNode(' 执行中…'));
  trace.appendChild(summary);
  trace._summary = summary;
  trace._steps = 0;
  log.appendChild(trace);
  scrollBottom();
  return trace;
}

function traceLine(trace, parts) {
  const line = el('div', 'line');
  for (const p of parts) line.appendChild(el('span', p.cls || '', p.text));
  trace.appendChild(line);
  trace._steps += 1;
  scrollBottom();
}

function finishTrace(trace, failed) {
  if (!trace) return;
  trace._summary.textContent = `${failed ? '✘ 执行中断' : '✔ 执行过程'}（${trace._steps} 步）`;
  trace.open = false;
}

/* ---------- 轮次事件 → UI（流式与重放共用同一份渲染逻辑） ---------- */
function applyEventToUi(turn, ev) {
  const ui = turn.ui;
  if (!ui) return;
  switch (ev.type) {
    case 'agent_start':
      if (!ui.trace) ui.trace = newTrace();
      traceLine(ui.trace, [{ text: '▸ ' }, { text: ev.agent, cls: 'agent' }, { text: ' 启动' }]);
      break;
    case 'tool_call':
      if (!ui.trace) ui.trace = newTrace();
      traceLine(ui.trace, [
        { text: '⚙ ' }, { text: `[${ev.agent}] `, cls: 'agent' },
        { text: ev.tool + (ev.detail ? '  ' + ev.detail : '') },
      ]);
      break;
    case 'tool_result':
      if (!ui.trace) ui.trace = newTrace();
      traceLine(ui.trace, [
        { text: ev.ok ? '✔ ' : '✘ ', cls: ev.ok ? 'okmark' : 'errmark' },
        { text: `[${ev.agent}] `, cls: 'agent' },
        { text: (ev.tool ? ev.tool + '  ' : '') + (ev.detail || '') },
      ]);
      break;
    case 'agent_end':
      if (!ui.trace) ui.trace = newTrace();
      traceLine(ui.trace, [{ text: '◂ ' }, { text: ev.agent, cls: 'agent' }, { text: ' 结束' }]);
      break;
    case 'delta':
      if (!ui.botDiv) ui.botDiv = addMdMsg('');
      renderMarkdown(ui.botDiv, (ui.botDiv._raw || '') + ev.text);
      scrollBottom();
      break;
    case 'done':
      finishTrace(ui.trace, false); ui.trace = null;
      if (!ui.botDiv && ev.reply) ui.botDiv = addMdMsg(ev.reply);
      break;
    case 'error':
      finishTrace(ui.trace, true); ui.trace = null;
      addMsg('error', '出错了：' + ev.text);
      break;
  }
}

function recordEvent(turn, ev) {
  turn.events.push(ev);
  if (ev.type === 'done' || ev.type === 'error') turn.done = true;
  applyEventToUi(turn, ev);
  if (turn.done) {
    if (turn.id) delete liveTurns[turn.id];  // 已落库，之后回放走历史接口
    renderSessionList();                     // 摘掉"运行中"状态点
    updateSendState();
    if (turn.id === activeId) refreshArtifacts();
  }
}

function attachTurn(turn) {
  // 切回运行中的会话：从缓冲整轮重放到当前 DOM
  turn.ui = { trace: null, botDiv: null };
  addMsg('user', turn.userText);
  for (const ev of turn.events) applyEventToUi(turn, ev);
}

function detachActiveTurn() {
  const turn = activeId ? liveTurns[activeId] : pendingTurn;
  if (turn) turn.ui = null;   // DOM 即将被清空，只断开引用，缓冲与流照常
}

/* ---------- 历史回放（FR-19：/api/sessions/{id}/messages） ---------- */
async function loadHistory(id) {
  log.textContent = '';
  const resp = await fetch(`/api/sessions/${id}/messages`);
  if (id !== activeId) return;  // 加载期间用户又切走了
  if (!resp.ok) { addMsg('error', '历史加载失败：' + resp.status); return; }
  const data = await resp.json();
  let trace = null;
  for (const m of data.messages) {
    if (m.role === 'action') {
      if (!trace) trace = newTrace();
      traceLine(trace, [
        { text: (m.ok === false ? '✘ ' : '⚙ '), cls: m.ok === false ? 'errmark' : '' },
        { text: m.tool + (m.detail ? '  ' + m.detail : '') },
      ]);
      continue;
    }
    if (trace) { finishTrace(trace, false); trace = null; }
    if (m.role === 'user') {
      addMsg('user', m.text);
      const sess = sessions.find(s => s.id === id);
      if (sess && (!sess.title || sess.title === id)) { sess.title = m.text.slice(0, 30); store.save(sessions); renderSessionList(); }
    }
    if (m.role === 'assistant') addMdMsg(m.text);
  }
  if (trace) finishTrace(trace, false);
  scrollBottom();
}

/* ---------- 会话切换 / 新建 ---------- */
async function selectSession(id) {
  detachActiveTurn();
  viewEpoch += 1;
  activeId = id; store.active = id;
  renderSessionList(); renderHeader(); updateSendState();
  await Promise.all([loadHistory(id), refreshArtifacts()]);
  const turn = turnFor(id);
  if (turn && id === activeId) attachTurn(turn);  // 正在跑的这一轮接在历史后面
  input.focus();
}

function newChat() {
  detachActiveTurn();
  viewEpoch += 1;
  activeId = null; store.active = null;
  renderSessionList(); renderHeader(); updateSendState();
  log.textContent = '';
  const tip = el('div', 'empty-chat');
  tip.appendChild(el('div', '', '输入研究任务开始新会话'));
  tip.appendChild(el('div', '', '会话将在首条消息发出后创建并进入左栏'));
  log.appendChild(tip);
  refreshArtifacts();
  input.focus();
}
$('newChat').addEventListener('click', newChat);

/* ---------- 设置弹窗（写回后端 .env，对新会话生效） ---------- */
const settingsDialog = $('settingsDialog');

async function openSettings() {
  const resp = await fetch('/api/settings');
  if (resp.ok) {
    const s = await resp.json();
    $('setBaseUrl').value = s.base_url;
    $('setModel').value = s.model;
    $('setApiKey').value = '';
    $('setTavilyKey').value = '';
    $('apiKeyHint').textContent = s.api_key_masked ? `当前：${s.api_key_masked}（留空不修改）` : '未设置';
    $('tavilyKeyHint').textContent = s.tavily_api_key_masked
      ? `当前：${s.tavily_api_key_masked}（留空不修改）` : '未设置（联网搜索将不可用）';
  }
  $('settingsNote').textContent = '保存后写回 .env，对新建/新恢复的会话生效。';
  settingsDialog.showModal();
}
$('openSettings').addEventListener('click', openSettings);
$('settingsCancel').addEventListener('click', () => settingsDialog.close());

$('settingsForm').addEventListener('submit', async e => {
  e.preventDefault();
  const payload = {
    base_url: $('setBaseUrl').value.trim(),   // 非密钥：总是提交（可清空回 OpenAI 官方）
    model: $('setModel').value.trim(),
  };
  if ($('setApiKey').value.trim()) payload.api_key = $('setApiKey').value.trim();
  if ($('setTavilyKey').value.trim()) payload.tavily_api_key = $('setTavilyKey').value.trim();
  const resp = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) { $('settingsNote').textContent = '保存失败：HTTP ' + resp.status; return; }
  const state = await (await fetch('/api/state')).json();
  serverInfo = state;
  renderHeader();
  settingsDialog.close();
});

/* ---------- 发送 + SSE 事件流（协议见 docs/prd-web-ui-v2.md） ---------- */
form.addEventListener('submit', async e => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  if (activeId && turnFor(activeId)) return;  // 当前会话正在跑（按钮本已禁用，兜底）
  input.value = '';
  const epoch = viewEpoch;
  const originId = activeId;                  // null = 新会话
  const turn = makeTurn(text);
  if (originId) { turn.id = originId; liveTurns[originId] = turn; }
  else pendingTurn = turn;

  if (epoch === viewEpoch) {
    if (!originId) log.textContent = '';      // 清掉"新会话"提示
    turn.ui = { trace: null, botDiv: null };
    addMsg('user', text);
  }
  renderSessionList(); updateSendState();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(originId ? { message: text, session_id: originId } : { message: text }),
    });
    if (!resp.ok) {
      recordEvent(turn, { type: 'error', text: `请求失败（HTTP ${resp.status}）` });
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop();
      for (const frame of frames) {
        if (!frame.startsWith('data: ')) continue;
        const ev = JSON.parse(frame.slice(6));
        if (ev.type === 'session') {
          if (!turn.id) {
            turn.id = ev.session_id;
            liveTurns[turn.id] = turn;
            pendingTurn = null;
            upsertSession(turn.id, text.slice(0, 30));
            if (epoch === viewEpoch) {        // 用户还停在发起时的视图才接管它
              activeId = turn.id; store.active = activeId;
              renderHeader();
            }
            renderSessionList();
          }
          continue;
        }
        recordEvent(turn, ev);
      }
    }
    if (!turn.done) recordEvent(turn, { type: 'error', text: '连接中断（服务端可能仍在执行，稍后刷新历史查看）' });
  } catch (err) {
    if (!turn.done) recordEvent(turn, { type: 'error', text: '请求失败：' + err.message });
  } finally {
    updateSendState();
    if (turn.id === activeId || (!turn.id && epoch === viewEpoch)) input.focus();
  }
});

/* ---------- 启动 ---------- */
(async function init() {
  try {
    const state = await (await fetch('/api/state')).json();
    serverInfo = state;
    if (state.initial_session_id) {           // --web --resume <id> 并入左栏
      upsertSession(state.initial_session_id, '');
      if (!activeId) activeId = state.initial_session_id;
    }
  } catch { $('sessionInfo').textContent = '服务连接失败'; }
  if (activeId && !sessions.find(s => s.id === activeId)) activeId = null;
  store.active = activeId;
  renderSessionList();
  if (activeId) await selectSession(activeId); else newChat();
})();
