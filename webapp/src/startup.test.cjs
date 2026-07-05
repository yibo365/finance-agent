const assert = require('node:assert/strict');
const test = require('node:test');

test('opens settings automatically when API key is not configured', async () => {
  const { shouldOpenSettingsOnStartup } = await import('./startup.js');

  assert.equal(shouldOpenSettingsOnStartup({ api_key_configured: false }), true);
  assert.equal(shouldOpenSettingsOnStartup({ api_key_configured: true }), false);
  assert.equal(shouldOpenSettingsOnStartup({}), false);
});
