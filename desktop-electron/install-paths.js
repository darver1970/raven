const fs = require('fs');
const path = require('path');

function resolveExecutableContext({
  isPackaged,
  execPath,
  portableExecutableDir = '',
  savedRoot = '',
  existsSync = fs.existsSync
}) {
  const executableDirectory = path.dirname(execPath);
  const portableDirectory = String(portableExecutableDir || '').trim();
  const isPortableBuild = Boolean(isPackaged && portableDirectory);
  const isNsisInstall = Boolean(isPackaged && !isPortableBuild);
  const savedRootIsValid = Boolean(
    savedRoot
    && path.isAbsolute(savedRoot)
    && existsSync(path.join(path.resolve(savedRoot), 'raven_control.py'))
  );
  const portableRoot = portableDirectory
    ? path.basename(portableDirectory).toLowerCase() === 'desktop'
      ? path.resolve(portableDirectory, '..')
      : path.resolve(portableDirectory)
    : '';
  const executableRoot = isNsisInstall
    ? executableDirectory
    : isPortableBuild
      ? portableRoot
      : 'C:\\Raven';
  const installedRoot = isNsisInstall
    ? path.resolve(executableRoot)
    : savedRootIsValid
      ? path.resolve(savedRoot)
      : path.resolve(executableRoot);

  return {
    executableDirectory,
    executableRoot,
    installedRoot,
    isNsisInstall,
    isPortableBuild,
    savedRootIsValid
  };
}

module.exports = { resolveExecutableContext };
