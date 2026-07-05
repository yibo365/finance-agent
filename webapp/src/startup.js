export function shouldOpenSettingsOnStartup(state) {
  return state?.api_key_configured === false;
}

export const APP_TITLE = 'finance-agent 投研工作台';
export const SIDEBAR_TITLE_CHARS = 30;
export const HEADER_TITLE_CHARS = 42;

function compactText(text, maxChars) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  return Array.from(normalized).slice(0, maxChars).join('');
}

export function deriveSessionTitles(text) {
  return {
    sidebar: compactText(text, SIDEBAR_TITLE_CHARS),
    header: compactText(text, HEADER_TITLE_CHARS),
  };
}
