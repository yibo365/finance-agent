const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

test('HTML uses local Plotly so the chart does not depend on CDN availability', () => {
  const html = fs.readFileSync('nvda_ai_events_candlestick.html', 'utf8');

  assert.match(html, /<script src="\.\/plotly-2\.35\.2\.min\.js"><\/script>/);
  assert.ok(fs.existsSync('plotly-2.35.2.min.js'));
});
