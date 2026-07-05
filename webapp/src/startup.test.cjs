const assert = require('node:assert/strict');
const test = require('node:test');

test('opens settings automatically when API key is not configured', async () => {
  const { shouldOpenSettingsOnStartup } = await import('./startup.js');

  assert.equal(shouldOpenSettingsOnStartup({ api_key_configured: false }), true);
  assert.equal(shouldOpenSettingsOnStartup({ api_key_configured: true }), false);
  assert.equal(shouldOpenSettingsOnStartup({}), false);
});

test('derives a longer header title from the same session prompt', async () => {
  const { deriveSessionTitles } = await import('./startup.js');
  const prompt = '请构建黄金与比特币作为避险/抗通胀资产的可交互比较分析体系，产物包括Excel回测底稿、PPT决策框架、Word策略报告。';

  const titles = deriveSessionTitles(prompt);

  assert.equal(titles.sidebar, '请构建黄金与比特币作为避险/抗通胀资产的可交互比较分析体系，');
  assert.equal(titles.header, '请构建黄金与比特币作为避险/抗通胀资产的可交互比较分析体系，产物包括Excel回测底');
  assert.equal(titles.header.startsWith(titles.sidebar), true);
});
