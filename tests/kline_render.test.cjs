// kline-html-report 渲染骨架单测：纯函数行为 + 模板资产无 CDN 依赖。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const SKILL_DIR = path.join(__dirname, '..', 'src', 'finance_agent', 'skills', 'builtin', 'kline-html-report');
const K = require(path.join(SKILL_DIR, 'assets', 'render.js'));

const rows = [
  { date: '2024-01-02', open: 48.1, high: 49.0, low: 47.8, close: 48.0, volume: 1000 },
  { date: '2024-01-03', open: 47.5, high: 48.2, low: 47.0, close: 47.0, volume: 1100 },
  { date: '2024-01-05', open: 46.8, high: 47.0, low: 46.0, close: 50.0, volume: 1200 },
  { date: '2024-01-08', open: 50.1, high: 51.0, low: 49.8, close: 52.0, volume: 1300 },
];

test('movingAverage fills leading positions with null', () => {
  const ma = K.movingAverage([1, 2, 3, 4], 2);
  assert.deepEqual(ma, [null, 1.5, 2.5, 3.5]);
});

test('maxDrawdown tracks peak and trough dates', () => {
  const { maxDD, peakDate, troughDate } = K.maxDrawdown(
    rows.map(r => r.close), rows.map(r => r.date),
  );
  assert.ok(maxDD < -2);
  assert.equal(peakDate, '2024-01-02');
  assert.equal(troughDate, '2024-01-03');
});

test('mapEventsToTradingDays maps non-trading day forward and computes lag', () => {
  const [e] = K.mapEventsToTradingDays(rows, [
    { date: '2024-01-04', title: '周四事件', category: 'x', direction: 'up', impact: 3 },
  ]);
  assert.equal(e.tradingDate, '2024-01-05'); // 1月4日无行情 → 映射到下一交易日
  assert.equal(e.lagDays, 1);
  assert.equal(e.close, 50.0);
});

test('filterEvents applies search, impact and direction criteria', () => {
  const events = K.mapEventsToTradingDays(rows, [
    { date: '2024-01-02', title: 'ChatGPT 发布', category: 'AI', direction: 'up', impact: 5, notes: '' },
    { date: '2024-01-03', title: '出口管制', category: '监管', direction: 'down', impact: 4, notes: '' },
  ]);
  assert.equal(K.filterEvents(events, { q: 'chatgpt' }).length, 1);
  assert.equal(K.filterEvents(events, { minImpact: 5 }).length, 1);
  assert.equal(K.filterEvents(events, { direction: 'down' })[0].title, '出口管制');
  assert.equal(K.filterEvents(events, { category: 'AI' }).length, 1);
});

test('buildEventTrace scales marker size by impact and colors by direction', () => {
  const events = K.mapEventsToTradingDays(rows, [
    { date: '2024-01-02', title: 'A', category: 'x', direction: 'up', impact: 5 },
    { date: '2024-01-03', title: 'B', category: 'x', direction: 'down', impact: 3 },
  ]);
  const trace = K.buildEventTrace(events);
  assert.ok(trace.marker.size[0] > trace.marker.size[1]);
  assert.equal(trace.marker.color[0], K.COLORS.up);
  assert.equal(trace.marker.color[1], K.COLORS.down);
  assert.equal(trace.text[0], '★5');
});

test('buildChangepointTrace plots below the low and labels kinds in Chinese', () => {
  const trace = K.buildChangepointTrace(rows, [
    { date: '2024-01-05', kind: 'accel_up', rule: '+3σ', severity: 2, window: ['2024-01-02', '2024-01-05'] },
    { date: '2099-01-01', kind: 'rally', rule: '幽灵日期应被跳过', severity: 1, window: ['a', 'b'] },
  ]);
  assert.equal(trace.x.length, 1);
  assert.ok(trace.y[0] < 46.0);
  assert.equal(trace.customdata[0][0], K.CP_LABELS.accel_up);
});

test('eventsToCsv escapes quotes and joins sources', () => {
  const csv = K.eventsToCsv([{
    date: '2024-01-02', tradingDate: '2024-01-02', title: '含"引号"', category: 'x',
    direction: 'up', move: '', impact: 3, close: 48, ret5: null, ret20: null, notes: '',
    sources: [{ name: 'HN', url: 'https://example.com' }],
  }]);
  assert.match(csv, /含""引号""/);
  assert.match(csv, /HN https:\/\/example\.com/);
});

test('template is self-contained: placeholders present, no external script/css', () => {
  const tpl = fs.readFileSync(path.join(SKILL_DIR, 'templates', 'report_template.html'), 'utf8');
  for (const token of ['__TITLE__', '__PAYLOAD_JSON__', '__PLOTLY_JS__', '__RENDER_JS__', '__SECTIONS_HTML__', '__EVIDENCE_HTML__']) {
    assert.ok(tpl.includes(token), `缺少占位符 ${token}`);
  }
  assert.doesNotMatch(tpl, /<script[^>]+src=/);
  assert.doesNotMatch(tpl, /<link[^>]+href="http/);
  assert.ok(fs.existsSync(path.join(SKILL_DIR, 'assets', 'plotly.min.js')));
});
