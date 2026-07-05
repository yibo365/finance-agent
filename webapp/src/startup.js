export function shouldOpenSettingsOnStartup(state) {
  return state?.api_key_configured === false;
}
