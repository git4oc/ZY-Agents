export const SKILL_ZIP_INSTALL_EVENT = 'zy:install-skill-zip';

export function openSkillPackageInstaller(file?: File): void {
  window.dispatchEvent(new CustomEvent(SKILL_ZIP_INSTALL_EVENT, { detail: { file } }));
}
