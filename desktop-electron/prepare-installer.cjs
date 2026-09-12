// Adapt the MIT-licensed electron-builder NSIS template without editing node_modules.
// Updates must never run legacy uninstallers: Raven data lives inside INSTDIR.
const fs = require('node:fs');
const path = require('node:path');
const templates = path.join(path.dirname(require.resolve('app-builder-lib/package.json')), 'templates', 'nsis');
const output = path.join(__dirname, 'generated-installer');
let section = fs.readFileSync(path.join(templates, 'installSection.nsh'), 'utf8');
// The second call is indented in upstream templates.
section = section.replace(/^[ \t]*!insertmacro uninstallOldVersion (SHELL_CONTEXT|HKEY_CURRENT_USER)\r?\n[ \t]*!insertmacro handleUninstallResult \1/gm, '; Raven: overlay program files; preserve existing application data.');
if (section.includes('!insertmacro uninstallOldVersion')) throw new Error('Unrecognized NSIS uninstall hook; refusing unsafe installer');
const original = fs.readFileSync(path.join(templates, 'installSection.nsh'), 'utf8');
if ((original.match(/!insertmacro uninstallOldVersion /g) || []).length !== 2) throw new Error('NSIS template changed; review upgrade safety');
let installer = fs.readFileSync(path.join(templates, 'installer.nsi'), 'utf8');
installer = '!pragma warning disable 6010 ; legacy uninstall functions intentionally unused\n' + installer;
const needle = '!include "installSection.nsh"';
if (!installer.includes(needle)) throw new Error('NSIS section hook missing');
fs.mkdirSync(output, {recursive: true});
let applicationFiles = fs.readFileSync(path.join(templates, 'include', 'installer.nsh'), 'utf8');
const uninstallerFile = 'File "/oname=${UNINSTALL_FILENAME}" "${UNINSTALLER_OUT_FILE}"';
if (!applicationFiles.includes(uninstallerFile)) throw new Error('NSIS uninstaller packaging changed');
applicationFiles = applicationFiles.replace(uninstallerFile, 'WriteUninstaller "$INSTDIR\\${UNINSTALL_FILENAME}"');
section = section.replace('!include installer.nsh', `!include "${path.join(output, 'application-files.nsh')}"`);
installer = installer.replace(needle, `!include "${path.join(output, 'installSection.nsh')}"`);
// A custom script generates its own small data-preserving uninstaller.
installer = installer.replace(/!ifdef BUILD_UNINSTALLER\r?\n  !include "uninstaller.nsh"\r?\n!endif/, `
Section "Uninstall"
  SetShellVarContext current
  Delete "$INSTDIR\\Raven.exe"
  Delete "$INSTDIR\\Uninstall Raven.exe"
  RMDir /r "$INSTDIR\\locales"
  RMDir /r "$INSTDIR\\resources"
  ReadRegStr $0 HKCU "\${INSTALL_REGISTRY_KEY}" "InstallLocation"
  \${If} $0 == $INSTDIR
    DeleteRegKey HKCU "\${INSTALL_REGISTRY_KEY}"
    DeleteRegKey HKCU "\${UNINSTALL_REGISTRY_KEY}"
    Delete "$DESKTOP\\Raven 1.2.lnk"
    Delete "$SMPROGRAMS\\Raven 1.2.lnk"
  \${EndIf}
  ; Deliberately retain runtime, src, user files, and nonempty installation folder.
  RMDir "$INSTDIR"
SectionEnd
`);
installer = installer.replace('!include "MUI2.nsh"', '!include "MUI2.nsh"\n!insertmacro MUI_UNPAGE_INSTFILES');
fs.writeFileSync(path.join(output, 'application-files.nsh'), applicationFiles);
fs.writeFileSync(path.join(output, 'installSection.nsh'), section);
fs.writeFileSync(path.join(output, 'installer.nsi'), installer);
