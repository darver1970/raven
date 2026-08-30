<#
.SYNOPSIS
    Instaluje Raven 1.2 do jediné projektové složky.

.DESCRIPTION
    Stahuje pouze bezplatné závislosti z oficiálních zdrojů, založí lokální
    samostatný OpenJarvis runtime, vytvoří konfiguraci a zástupce na ploše. Neodesílá
    obsah instalace ani uživatelská data mimo počítač.
#>
[CmdletBinding()]
param(
    [string]$InstallPath,
    [switch]$SkipModel,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$installLogDirectory = Join-Path $env:LOCALAPPDATA 'Raven\logs'
New-Item -ItemType Directory -Path $installLogDirectory -Force | Out-Null
$installLog = Join-Path $installLogDirectory 'install-latest.log'
try {
    Start-Transcript -LiteralPath $installLog -Force | Out-Null
} catch {
    Write-Warning "Instalační protokol nelze otevřít: $($_.Exception.Message)"
}

trap {
    Write-Host ''
    Write-Host 'INSTALACE RAVEN SELHALA' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Podrobný protokol: $installLog" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    if (-not $env:RAVEN_INSTALL_NONINTERACTIVE) {
        Read-Host 'Stisknutím Enter zavřete toto okno'
    }
    exit 1
}

function Write-Step([string]$Message) {
    Write-Host "[RAVEN] $Message" -ForegroundColor Cyan
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $FilePath @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$FailureMessage (kód $exitCode)."
    }
}

function Assert-Command([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Chybí program '$Name'. Nainstalujte jej a spusťte instalátor znovu."
    }
    return $command.Source
}

function Copy-RavenFiles([string]$From, [string]$To) {
    $fileNames = @(
        '.gitignore', 'AGENTS.md', 'LICENSE', 'NOTICE', 'README.md', 'RELEASE_NOTES.md', 'VERSION', 'install.ps1', 'spustit-raven.ps1', 'stop-raven.ps1',
        'hardware_monitor.py', 'telemetry_extensions.py', 'raven_control.py', 'network_monitor.py',
        'agent_runtime.py', 'raven_brain.py', 'raven_intelligence.py', 'raven_next.py'
    )
    foreach ($name in $fileNames) {
        Copy-Item -LiteralPath (Join-Path $From $name) -Destination (Join-Path $To $name) -Force
    }
    foreach ($directory in @('defaults', 'hud', 'desktop', 'desktop-electron')) {
        $sourceDirectory = Join-Path $From $directory
        $destination = Join-Path $To $directory
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Get-ChildItem -LiteralPath $sourceDirectory -File -Recurse | ForEach-Object {
            $relative = $_.FullName.Substring($sourceDirectory.Length).TrimStart('\')
            if ($_.Extension -eq '.exe' -or $relative -match '(^|\\)node_modules(\\|$)') { return }
            $target = Join-Path $destination $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:PATH = [Environment]::ExpandEnvironmentVariables("$machine;$user")
}

function Get-WingetCommand {
    $command = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
        return $command
    }
    $userAlias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
    if (Test-Path -LiteralPath $userAlias -PathType Leaf) {
        return Get-Command $userAlias -ErrorAction SilentlyContinue
    }
    return $null
}

function Test-VisualCppRuntime {
    $systemDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::System)
    if ([string]::IsNullOrWhiteSpace($systemDirectory)) { return $false }
    foreach ($name in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $systemDirectory $name) -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

function Ensure-VisualCppRuntime {
    if (Test-VisualCppRuntime) {
        Write-Step 'Používám existující Microsoft Visual C++ Runtime.'
        return
    }
    $winget = Get-WingetCommand
    if (-not $winget) {
        throw 'Chybí Microsoft Visual C++ Runtime 2015-2022 x64 a winget není dostupný.'
    }
    Write-Step 'Instaluji bezplatný Microsoft Visual C++ Runtime 2015-2022 x64.'
    Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
        'install', '--id', 'Microsoft.VCRedist.2015+.x64', '--exact', '--silent',
        '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
    ) -FailureMessage 'Instalace Microsoft Visual C++ Runtime selhala'
    if (-not (Test-VisualCppRuntime)) {
        throw 'Microsoft Visual C++ Runtime byl nainstalován, ale jeho systémové knihovny nebyly nalezeny.'
    }
}

function Ensure-Node {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $command) {
        $winget = Get-WingetCommand
        if (-not $winget) { throw 'Chybí Node.js 22+ a winget není dostupný.' }
        Write-Step 'Instaluji bezplatný Node.js LTS.'
        Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
            'install', '--id', 'OpenJS.NodeJS.LTS', '--silent', '--accept-source-agreements',
            '--accept-package-agreements', '--disable-interactivity'
        ) -FailureMessage 'Instalace Node.js LTS selhala'
        Refresh-ProcessPath
        $command = Get-Command node.exe -ErrorAction SilentlyContinue
        if (-not $command -and (Test-Path -LiteralPath "$env:ProgramFiles\nodejs\node.exe")) {
            $env:PATH = "$env:ProgramFiles\nodejs;$env:PATH"
            $command = Get-Command node.exe -ErrorAction SilentlyContinue
        }
    }
    if (-not $command) { throw 'Node.js se po instalaci nepodařilo najít.' }
    $version = [Version]((& $command.Source --version) -replace '^v', '')
    if ($version.Major -lt 22) { throw "Raven vyžaduje Node.js 22+, nalezena verze $version." }
    return $command
}

function Find-CompatiblePython {
    $candidates = New-Object System.Collections.Generic.List[string]
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $launcherResult = & $launcher.Source -3.13 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $launcherResult) {
                $candidates.Add(([string]$launcherResult).Trim())
            }
        } catch {}
    }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe')
    )) {
        if ($candidate) { $candidates.Add($candidate) }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $candidates.Add($pythonCommand.Source) }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $versionText = & $candidate --version 2>&1
            if ($LASTEXITCODE -ne 0) { continue }
            $match = [regex]::Match([string]$versionText, 'Python\s+3\.(1[0-3])(?:\.|$)')
            if ($match.Success) { return [System.IO.Path]::GetFullPath($candidate) }
        } catch {}
    }
    return $null
}

function Ensure-CompatiblePython {
    $python = Find-CompatiblePython
    if (-not $python) {
        $winget = Get-WingetCommand
        if (-not $winget) {
            throw 'Chybí kompatibilní Python 3.10-3.13 a winget není dostupný.'
        }
        Write-Step 'Instaluji bezplatný Python 3.13.'
        Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
            'install', '--id', 'Python.Python.3.13', '--exact', '--scope', 'user', '--silent',
            '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
        ) -FailureMessage 'Instalace Pythonu 3.13 selhala'
        Refresh-ProcessPath
        $python = Find-CompatiblePython
    }
    if (-not $python) { throw 'Python 3.13 se po instalaci nepodařilo najít.' }

    $pythonDirectory = Split-Path -Parent $python
    $windowsApps = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps')).TrimEnd('\')
    $filteredPath = $env:PATH -split ';' | Where-Object {
        if (-not $_) { return $false }
        try {
            return [System.IO.Path]::GetFullPath($_).TrimEnd('\') -ne $windowsApps
        } catch {
            return $true
        }
    }
    $env:PATH = "$pythonDirectory;$($filteredPath -join ';')"
    Write-Step "Používám kompatibilní Python: $python"
    return $python
}

function Copy-PortablePythonRuntime([string]$SourcePython, [string]$RuntimeDirectory) {
    $sourceDirectory = Split-Path -Parent $SourcePython
    $portableDirectory = Join-Path $RuntimeDirectory 'python-base'
    $portablePython = Join-Path $portableDirectory 'python.exe'
    if (-not (Test-Path -LiteralPath $portablePython -PathType Leaf)) {
        Write-Step 'Kopíruji vlastní Python runtime pro přenosné spuštění na jiném počítači.'
        New-Item -ItemType Directory -Path $portableDirectory -Force | Out-Null
        Copy-Item -Path (Join-Path $sourceDirectory '*') -Destination $portableDirectory -Recurse -Force
    }
    if (-not (Test-Path -LiteralPath $portablePython -PathType Leaf)) {
        throw 'Vlastní přenosný Python runtime se nepodařilo připravit.'
    }
    return $portablePython
}

function Import-VisualStudioBuildEnvironment {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { return $false }
    $installationPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $installationPath) { return $false }
    $developerCommand = Join-Path ([string]$installationPath).Trim() 'Common7\Tools\VsDevCmd.bat'
    if (-not (Test-Path -LiteralPath $developerCommand -PathType Leaf)) { return $false }

    $environmentLines = & cmd.exe /d /s /c "`"$developerCommand`" -no_logo -arch=x64 -host_arch=x64 && set"
    if ($LASTEXITCODE -ne 0) { return $false }
    foreach ($line in $environmentLines) {
        $separator = $line.IndexOf('=')
        if ($separator -le 0) { continue }
        $name = $line.Substring(0, $separator)
        $value = $line.Substring($separator + 1)
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
    return $null -ne (Get-Command link.exe -ErrorAction SilentlyContinue)
}

function Ensure-VisualStudioBuildTools {
    if (Import-VisualStudioBuildEnvironment) {
        Write-Step 'Používám existující Microsoft C++ Build Tools.'
        return
    }
    $winget = Get-WingetCommand
    if (-not $winget) {
        throw 'Chybí Microsoft C++ Build Tools a winget není dostupný.'
    }
    Write-Step 'Instaluji bezplatné Microsoft Visual Studio Build Tools pro nativní modul OpenJarvis.'
    Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
        'install', '--id', 'Microsoft.VisualStudio.2022.BuildTools', '--exact', '--silent',
        '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity',
        '--override', '--wait --quiet --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
    ) -FailureMessage 'Instalace Microsoft C++ Build Tools selhala'
    if (-not (Import-VisualStudioBuildEnvironment)) {
        throw 'Microsoft C++ Build Tools jsou nainstalované, ale link.exe se nepodařilo načíst.'
    }
}

function Find-UvCommand {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) { return $command.Source }
    $localUv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $localUv -PathType Leaf) { return $localUv }
    $wingetPackages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $wingetUv = Get-ChildItem -LiteralPath $wingetPackages -Filter 'uv.exe' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*astral-sh.uv*' } |
        Select-Object -First 1 -ExpandProperty FullName
    if ($wingetUv) { return $wingetUv }
    return $null
}

function Ensure-Uv {
    $uvPath = Find-UvCommand
    if (-not $uvPath) {
        $winget = Get-WingetCommand
        if (-not $winget) { throw 'Chybí správce Python prostředí uv a winget není dostupný.' }
        Write-Step 'Instaluji bezplatný správce Python prostředí uv.'
        Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
            'install', '--id', 'astral-sh.uv', '--exact', '--version', '0.12.5', '--scope', 'user', '--silent',
            '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
        ) -FailureMessage 'Instalace uv selhala'
        Refresh-ProcessPath
        $uvPath = Find-UvCommand
    }
    if (-not $uvPath) { throw 'Program uv se po instalaci nepodařilo najít.' }
    $env:PATH = "$(Split-Path -Parent $uvPath);$env:PATH"
    return $uvPath
}

function Grant-InstallDirectoryAccess([string]$Directory) {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $acl = Get-Acl -LiteralPath $Directory
        $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $propagation = [Security.AccessControl.PropagationFlags]::None
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [Security.AccessControl.FileSystemRights]::Modify,
            $inheritance,
            $propagation,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $Directory -AclObject $acl
    } catch {
        throw "Cílové složce nelze nastavit oprávnění pro uživatele: $($_.Exception.Message)"
    }
}

if (-not $InstallPath) {
    $defaultDrive = 'C:\Raven'
    Write-Host ''
    Write-Host 'Zvolte jednu pracovní složku pro celý Raven, modely, runtime a data.' -ForegroundColor Yellow
    $InstallPath = Read-Host "Cílová složka Ravenu [výchozí: $defaultDrive]"
    if (-not $InstallPath) { $InstallPath = $defaultDrive }
}

$installRoot = [System.IO.Path]::GetFullPath($InstallPath)
$sourceRoot = [System.IO.Path]::GetFullPath($sourceRoot)
$installMarker = Join-Path $installRoot '.raven-installing'
if ($installRoot.Length -lt 4 -or $installRoot -eq $installRoot.Substring(0, 3)) {
    throw 'Jako cíl nelze použít kořen disku. Zvolte samostatnou složku.'
}

$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installRoot))
if (-not $drive.IsReady) { throw "Disk $($drive.Name) není připraven." }
if ($drive.AvailableFreeSpace -lt 24GB) {
    throw "Na disku $($drive.Name) je méně než 24 GB volného místa. Zvolte jiný disk."
}

if ($installRoot -ne $sourceRoot) {
    $resuming = Test-Path -LiteralPath $installMarker
    $sourceIsBundledInsideTarget = $sourceRoot.StartsWith(
        $installRoot.TrimEnd('\') + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if (Test-Path -LiteralPath $installRoot) {
        $existing = Get-ChildItem -LiteralPath $installRoot -Force | Where-Object Name -ne '.raven-installing' | Select-Object -First 1
        if ($existing -and -not $resuming -and -not $sourceIsBundledInsideTarget) {
            throw "Cílová složka není prázdná: $installRoot"
        }
    }
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    Set-Content -LiteralPath $installMarker -Value 'Raven 1.2 installation in progress' -Encoding utf8
    Write-Step "Kopíruji soubory Raven 1.2 do $installRoot"
    Copy-RavenFiles -From $sourceRoot -To $installRoot
} else {
    Set-Content -LiteralPath $installMarker -Value 'Raven 1.2 installation in progress' -Encoding utf8
}

$runtime = Join-Path $installRoot 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Grant-InstallDirectoryAccess -Directory $installRoot
$env:OPENJARVIS_HOME = $installRoot
$env:OPENJARVIS_SKIP_SERVICE = '1'
$env:OLLAMA_MODELS = Join-Path $runtime 'ollama-models'
$env:UV_CACHE_DIR = Join-Path $runtime 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP, $env:OLLAMA_MODELS, $env:UV_CACHE_DIR -Force | Out-Null

Write-Step 'Kontroluji Git.'
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    $winget = Get-WingetCommand
    if (-not $winget) { throw 'Chybí Git i winget. Nainstalujte Git pro Windows a spusťte instalátor znovu.' }
    Write-Step 'Instaluji Git pro Windows.'
    Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
        'install', '--id', 'Git.Git', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
    ) -FailureMessage 'Instalace Gitu selhala'
    Refresh-ProcessPath
    $git = Assert-Command 'git'
}

Write-Step 'Kontroluji kompatibilní Python pro OpenJarvis.'
$compatiblePython = Ensure-CompatiblePython
$compatiblePython = Copy-PortablePythonRuntime -SourcePython $compatiblePython -RuntimeDirectory $runtime
Write-Step 'Kontroluji Microsoft Visual C++ Runtime pro nativní Python komponenty.'
Ensure-VisualCppRuntime
Write-Step 'Kontroluji Microsoft C++ Build Tools pro OpenJarvis.'
Ensure-VisualStudioBuildTools

$openJarvisCommit = 'fd4490ca747e5b0d837b575c6b52fd024e5c24ac'
$openJarvisArchiveHash = '29F2FF5B7427A3E6FA3947AC824D737D2289FE3C7232EC90D2DF3A8B62931398'
$openJarvisSource = Join-Path $installRoot 'src'
$openJarvisVersionFile = Join-Path $openJarvisSource '.raven-source-version'
$sourceVersion = if (Test-Path -LiteralPath $openJarvisVersionFile -PathType Leaf) {
    (Get-Content -LiteralPath $openJarvisVersionFile -Raw).Trim()
} else { '' }
if ($sourceVersion -ne $openJarvisCommit -or -not (Test-Path -LiteralPath (Join-Path $openJarvisSource 'pyproject.toml') -PathType Leaf)) {
    Write-Step 'Stahuji ověřený zdroj OpenJarvisu pro Raven 1.2.'
    $openJarvisArchive = Join-Path $runtime "OpenJarvis-$openJarvisCommit.zip"
    $openJarvisStaging = Join-Path $runtime "OpenJarvis-$openJarvisCommit-staging"
    $previousProgress = $ProgressPreference
    try {
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri "https://github.com/open-jarvis/OpenJarvis/archive/$openJarvisCommit.zip" -OutFile $openJarvisArchive -UseBasicParsing -TimeoutSec 600
    } finally {
        $ProgressPreference = $previousProgress
    }
    $downloadedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $openJarvisArchive).Hash
    if ($downloadedHash -ne $openJarvisArchiveHash) {
        throw "Kontrolní součet zdroje OpenJarvis nesouhlasí. Očekáváno $openJarvisArchiveHash, získáno $downloadedHash."
    }
    if (Test-Path -LiteralPath $openJarvisStaging) { Remove-Item -LiteralPath $openJarvisStaging -Recurse -Force }
    New-Item -ItemType Directory -Path $openJarvisStaging -Force | Out-Null
    Expand-Archive -LiteralPath $openJarvisArchive -DestinationPath $openJarvisStaging -Force
    $extractedSource = Get-ChildItem -LiteralPath $openJarvisStaging -Directory | Select-Object -First 1
    if (-not $extractedSource -or -not (Test-Path -LiteralPath (Join-Path $extractedSource.FullName 'pyproject.toml') -PathType Leaf)) {
        throw 'Archiv OpenJarvis neobsahuje očekávaný zdrojový projekt.'
    }
    if (Test-Path -LiteralPath $openJarvisSource) { Remove-Item -LiteralPath $openJarvisSource -Recurse -Force }
    Move-Item -LiteralPath $extractedSource.FullName -Destination $openJarvisSource
    Set-Content -LiteralPath $openJarvisVersionFile -Value $openJarvisCommit -Encoding ascii
    Remove-Item -LiteralPath $openJarvisArchive -Force
    Remove-Item -LiteralPath $openJarvisStaging -Recurse -Force
} else {
    Write-Step 'Používám ověřený zdroj OpenJarvisu pro Raven 1.2.'
}
$uv = Ensure-Uv
$venvPython = Join-Path $openJarvisSource '.venv\Scripts\python.exe'
$jarvisCommand = Join-Path $openJarvisSource '.venv\Scripts\jarvis.exe'
$openJarvisSyncMarker = Join-Path $openJarvisSource '.raven-uv-sync-complete'
$openJarvisEnvironmentReady =
    (Test-Path -LiteralPath $openJarvisSyncMarker -PathType Leaf) -and
    (Test-Path -LiteralPath $venvPython -PathType Leaf) -and
    (Test-Path -LiteralPath $jarvisCommand -PathType Leaf)
if ($openJarvisEnvironmentReady) {
    Write-Step 'Používám již připravené lokální Python prostředí OpenJarvisu.'
} else {
    Write-Step 'Připravuji lokální Python prostředí OpenJarvisu. Kompilace nativních součástí může na pomalejším PC několik minut pokračovat bez dalšího výpisu; instalační okno nezavírejte.'
    Invoke-NativeChecked -FilePath $uv -Arguments @(
        'sync', '--project', $openJarvisSource, '--python', $compatiblePython,
        '--extra', 'desktop', '--group', 'desktop-native'
    ) -FailureMessage 'Příprava prostředí OpenJarvisu selhala'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw 'OpenJarvis nevytvořil očekávané Python prostředí.'
    }
    if (-not (Test-Path -LiteralPath $jarvisCommand -PathType Leaf)) {
        throw 'OpenJarvis nevytvořil očekávaný příkaz jarvis.exe.'
    }
    Set-Content -LiteralPath $openJarvisSyncMarker -Value $openJarvisCommit -Encoding ascii
}

$python = $venvPython
if (-not (Test-Path -LiteralPath $python)) { throw 'OpenJarvis nevytvořil očekávané Python prostředí.' }
if (-not (Test-Path -LiteralPath $jarvisCommand -PathType Leaf)) { throw 'OpenJarvis nevytvořil očekávaný příkaz jarvis.exe.' }
$pipModule = Join-Path $installRoot 'src\.venv\Lib\site-packages\pip\__init__.py'
if (-not (Test-Path -LiteralPath $pipModule -PathType Leaf)) {
    Write-Step 'Doplňuji pip do lokálního Python prostředí.'
    Invoke-NativeChecked -FilePath $python -Arguments @('-m', 'ensurepip', '--upgrade') -FailureMessage 'Do prostředí OpenJarvisu se nepodařilo nainstalovat pip'
}

Write-Step 'Odstraňuji nepoužívané hlasové balíčky a modely.'
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $pipUninstallOutput = & $python -m pip uninstall -y openwakeword piper-tts sounddevice soundfile 2>&1
    $pipUninstallExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
$pipUninstallOutput | ForEach-Object { Write-Host $_ }
if ($pipUninstallExitCode -ne 0) { throw 'Odstranění nepoužívaných hlasových balíčků selhalo.' }
foreach ($voiceCache in @(
    (Join-Path $runtime 'piper'),
    (Join-Path $runtime 'voice'),
    (Join-Path $runtime 'huggingface\hub\models--hexgrad--Kokoro-82M'),
    (Join-Path $runtime 'huggingface\hub\models--Systran--faster-whisper-base'),
    (Join-Path $runtime 'huggingface\hub\models--Systran--faster-whisper-small')
)) {
    if ($voiceCache.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $voiceCache)) {
        Remove-Item -LiteralPath $voiceCache -Recurse -Force
    }
}

Write-Step 'Instaluji bezplatnou lokální telemetrii procesů.'
Invoke-NativeChecked -FilePath $python -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', 'psutil') -FailureMessage 'Nelze nainstalovat telemetrii procesů psutil'

Write-Step 'Instaluji bezplatné agentní jádro, webového agenta a lokální crawler.'
Invoke-NativeChecked -FilePath $python -Arguments @(
    '-m', 'pip', 'install', '--disable-pip-version-check', 'pydantic-ai-slim==2.32.0',
    'browser-use==0.13.8', 'crawl4ai==0.9.2', 'mcp==1.26.0', 'starlette==0.52.1', 'pytest==9.1.1'
) -FailureMessage 'Instalace agentních komponent selhala'
Invoke-NativeChecked -FilePath $python -Arguments @('-m', 'pip', 'check') -FailureMessage 'Python závislosti agentního jádra nejsou kompatibilní'

Write-Step 'Vytvářím lokální konfiguraci Raven 1.2.'
foreach ($template in Get-ChildItem -LiteralPath (Join-Path $installRoot 'defaults') -Filter '*.json') {
    $destination = Join-Path $runtime $template.Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $template.FullName -Destination $destination
    }
}
$settingsPath = Join-Path $runtime 'raven-settings.json'
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$settings | Add-Member -NotePropertyName storage_root -NotePropertyValue $installRoot -Force
$settings | Add-Member -NotePropertyName ai_provider -NotePropertyValue 'automatic' -Force
$settings | Add-Member -NotePropertyName router_mode -NotePropertyValue 'automatic' -Force
$settings | Add-Member -NotePropertyName permission_mode -NotePropertyValue 'full' -Force
$settings | Add-Member -NotePropertyName cloud_api -NotePropertyValue $true -Force
$settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding utf8

Write-Step 'Instaluji bezplatnou desktopovou vrstvu Electron, skutečný web a Monaco editor.'
$desktopPath = Join-Path $installRoot 'desktop'
$electronProject = Join-Path $installRoot 'desktop-electron'
$installedShell = Join-Path $installRoot 'Raven.exe'
if (Test-Path -LiteralPath $installedShell -PathType Leaf) {
    Write-Step 'Používám desktopovou vrstvu obsaženou v instalačním EXE.'
    $desktopExecutable = $installedShell
} else {
    $node = Ensure-Node
    $npm = Assert-Command 'npm.cmd'
    $env:npm_config_cache = Join-Path $runtime 'npm-cache'
    New-Item -ItemType Directory -Path $env:npm_config_cache -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $electronProject 'package.json'))) {
        throw 'Zdroj desktopové vrstvy Raven chybí.'
    }
    Push-Location -LiteralPath $electronProject
    try {
        Invoke-NativeChecked -FilePath $npm -Arguments @('ci', '--no-audit', '--no-fund') -FailureMessage 'Instalace bezplatných desktopových závislostí selhala'
        Invoke-NativeChecked -FilePath $npm -Arguments @('run', 'pack:portable') -FailureMessage 'Vytvoření desktopového EXE selhalo'
    } finally {
        Pop-Location
    }
    $builtDesktop = Join-Path $installRoot 'desktop-dist\Raven-Desktop.exe'
    if (-not (Test-Path -LiteralPath $builtDesktop)) { throw 'Sestavený Raven-Desktop.exe nebyl nalezen.' }
    $desktopExecutable = Join-Path $desktopPath 'Raven-Desktop.exe'
    Copy-Item -LiteralPath $builtDesktop -Destination $desktopExecutable -Force
}

function Find-OllamaExecutable([string]$RuntimeRoot) {
    $command = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $candidates = @(
        (Join-Path $RuntimeRoot 'ollama\ollama.exe'),
        $(if ($command) { $command.Source } else { $null }),
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:LOCALAPPDATA 'Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

$ollamaPath = Find-OllamaExecutable -RuntimeRoot $runtime
if (-not (Test-Path -LiteralPath $ollamaPath)) {
    $winget = Get-WingetCommand
    if (-not $winget) { throw 'Chybí Ollama a winget není dostupný.' }
    Write-Step 'Instaluji bezplatnou Ollamu.'
    Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
        'install', '--id', 'Ollama.Ollama', '--silent', '--accept-source-agreements',
        '--accept-package-agreements', '--disable-interactivity'
    ) -FailureMessage 'Instalace Ollamy selhala'
    Refresh-ProcessPath
    $ollamaPath = Find-OllamaExecutable -RuntimeRoot $runtime
}
if (-not (Test-Path -LiteralPath $ollamaPath -PathType Leaf)) {
    throw 'Ollama nebyla po instalaci nalezena.'
}
$modelStatus = @{}
if (-not $SkipModel) {
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $portProbe.Start()
    $ollamaInstallPort = ([System.Net.IPEndPoint]$portProbe.LocalEndpoint).Port
    $portProbe.Stop()
    $previousOllamaHost = $env:OLLAMA_HOST
    $ollamaServer = $null
    try {
        $env:OLLAMA_HOST = "127.0.0.1:$ollamaInstallPort"
        Write-Step "Spouštím izolovanou Ollamu pro instalaci modelů na portu $ollamaInstallPort."
        $ollamaServer = Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WorkingDirectory $installRoot -WindowStyle Hidden -PassThru
        $ollamaReady = $false
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            if ($ollamaServer.HasExited) { break }
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$ollamaInstallPort/api/version" -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    $ollamaReady = $true
                    break
                }
            } catch {}
            Start-Sleep -Milliseconds 250
        }
        if (-not $ollamaReady) {
            throw 'Izolovaný server Ollama se během instalace nespustil.'
        }
        foreach ($model in @('qwen3.5:4b', 'qwen3.5:9b', 'qwen2.5-coder:7b')) {
            Write-Step "Stahuji lokální bezplatný model $model."
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                & $ollamaPath pull $model 2>&1 | ForEach-Object { Write-Host $_ }
                $modelExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            $modelStatus[$model] = $modelExitCode -eq 0
            if ($modelExitCode -ne 0) { Write-Warning "Model $model se nestáhl. Později spusťte: ollama pull $model" }
        }
        if (-not ($modelStatus.Values -contains $true)) {
            throw 'Nepodařilo se připravit žádný lokální model. Instalaci lze zopakovat po kontrole připojení a Ollamy.'
        }
    } finally {
        if ($ollamaServer -and -not $ollamaServer.HasExited) {
            Stop-Process -Id $ollamaServer.Id -Force -ErrorAction SilentlyContinue
            $ollamaServer.WaitForExit(5000) | Out-Null
        }
        $env:OLLAMA_HOST = $previousOllamaHost
    }
}

Write-Step 'Připravuji lokálního agenta OpenClaw bez Gateway a bez oprávnění k příkazům.'
$node = Ensure-Node
$npm = Assert-Command 'npm.cmd'
$openClawRoot = Join-Path $runtime 'openclaw'
$env:npm_config_cache = Join-Path $openClawRoot 'npm-cache'
New-Item -ItemType Directory -Path $openClawRoot, $env:npm_config_cache -Force | Out-Null
Invoke-NativeChecked -FilePath $npm -Arguments @('install', '--prefix', $openClawRoot, '--omit=dev', 'openclaw@2026.7.1-2') -FailureMessage 'Instalace OpenClaw selhala'
$openClawConfig = @{
    models = @{ providers = @{ ollama = @{
        baseUrl = 'http://127.0.0.1:11434'; apiKey = 'ollama-local'; api = 'ollama'; timeoutSeconds = 300
        models = @(@{ id = 'qwen2.5-coder:7b'; name = 'Qwen 2.5 Coder 7B'; input = @('text'); params = @{ keep_alive = '15m' } })
    } } }
    agents = @{ defaults = @{
        model = @{ primary = 'ollama/qwen2.5-coder:7b' }
        models = @{ 'ollama/qwen2.5-coder:7b' = @{} }
        workspace = (Join-Path $runtime 'agents\openclaw')
    } }
    gateway = @{ mode = 'local' }
    tools = @{ exec = @{ host = 'gateway'; security = 'deny'; ask = 'off' } }
}
$openClawConfigPath = Join-Path $openClawRoot 'openclaw.json'
$openClawConfig | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $openClawConfigPath -Encoding utf8
$env:OPENCLAW_STATE_DIR = Join-Path $openClawRoot 'state'
$env:OPENCLAW_CONFIG_PATH = $openClawConfigPath
$env:OPENCLAW_WORKSPACE_DIR = Join-Path $runtime 'agents\openclaw'
New-Item -ItemType Directory -Path $env:OPENCLAW_STATE_DIR, $env:OPENCLAW_WORKSPACE_DIR -Force | Out-Null
$openClawCommand = Join-Path $openClawRoot 'node_modules\.bin\openclaw.cmd'
if (-not (Test-Path -LiteralPath $openClawCommand -PathType Leaf)) {
    throw 'OpenClaw se nainstaloval bez očekávaného příkazu openclaw.cmd.'
}
Invoke-NativeChecked -FilePath $openClawCommand -Arguments @('exec-policy', 'preset', 'deny-all') -FailureMessage 'Nelze nastavit bezpečnostní politiku OpenClaw'
$agentsPath = Join-Path $runtime 'raven-agents.json'
$agents = Get-Content -LiteralPath $agentsPath -Raw | ConvertFrom-Json
$openClawAgent = $agents.agents | Where-Object { $_.id -eq 'openclaw' } | Select-Object -First 1
if ($openClawAgent) {
    $openClawAgent.model = 'qwen2.5-coder:7b'
    $openClawReady = -not $SkipModel -and $modelStatus['qwen2.5-coder:7b'] -eq $true
    $openClawAgent.status = if ($openClawReady) { 'ready' } else { 'planned' }
    $openClawAgent.rules = @('Používá jen lokální Ollama a nemá spuštěnou Gateway.', 'Pracuje pouze v izolovaném pracovním adresáři.', 'Systémové změny, síť a externí účty zůstávají zablokované.')
    $openClawAgent.permissions = if ($openClawReady) { @('Lokální textová inference', 'Bez příkazů, sítě a externích účtů') } else { @('Čeká na stažení qwen2.5-coder:7b') }
    $agents | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $agentsPath -Encoding utf8
}
$openClawModulePath = Join-Path $runtime 'openclaw-module.json'
$openClawModule = Get-Content -LiteralPath $openClawModulePath -Raw | ConvertFrom-Json
$openClawModule.state = 'installed_local'
$openClawModule.required_checks = @('OpenClaw CLI ověřen', 'Lokální Ollama ověřena', 'Politika příkazů deny-all')
$openClawModule.blocked_actions = @('Automatické spouštění po Windows', 'Automatický přístup k účtům', 'Automatické systémové změny')
$openClawModule | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $openClawModulePath -Encoding utf8

Write-Step 'Připravuji volitelnou lokální telemetrii.'
try {
    $lhmZip = Join-Path $runtime 'LibreHardwareMonitor.zip'
    Invoke-WebRequest -Uri 'https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest/download/LibreHardwareMonitor.zip' -OutFile $lhmZip -UseBasicParsing -TimeoutSec 90
    Expand-Archive -LiteralPath $lhmZip -DestinationPath (Join-Path $runtime 'librehardwaremonitor') -Force
    Remove-Item -LiteralPath $lhmZip -Force
} catch {
    Write-Warning 'LibreHardwareMonitor se nepodařilo stáhnout; ostatní části zůstávají funkční.'
}

try {
    $winget = Get-WingetCommand
    if (-not $winget) { throw 'winget není dostupný.' }
    $presentMonDirectory = Join-Path $runtime 'presentmon'
    New-Item -ItemType Directory -Path $presentMonDirectory -Force | Out-Null
    $presentMonTarget = Join-Path $presentMonDirectory 'PresentMon.exe'
    $presentMonExisting = Get-Command PresentMon.exe -ErrorAction SilentlyContinue
    if ($presentMonExisting -and (Test-Path -LiteralPath $presentMonExisting.Source -PathType Leaf)) {
        Copy-Item -LiteralPath $presentMonExisting.Source -Destination $presentMonTarget -Force
    } else {
        Write-Step 'Instaluji bezplatný PresentMon pro FPS a frametime.'
        Invoke-NativeChecked -FilePath $winget.Source -Arguments @(
            'install', '--id', 'Intel.PresentMon.Console', '--exact', '--location', $presentMonDirectory,
            '--silent', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'
        ) -FailureMessage 'Instalace PresentMon selhala'
    }
    if (-not (Test-Path -LiteralPath $presentMonTarget -PathType Leaf)) {
        $presentMonInstalled = Get-ChildItem -LiteralPath $presentMonDirectory -Filter 'PresentMon.exe' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $presentMonInstalled) { throw 'PresentMon.exe nebyl po instalaci nalezen.' }
    }
} catch {
    Write-Warning 'PresentMon se nepodařilo nainstalovat; ostatní telemetrie zůstává funkční.'
}

try {
    $handleDirectory = Join-Path $runtime 'sysinternals-handle'
    $handleArchive = Join-Path $handleDirectory 'Handle.zip'
    New-Item -ItemType Directory -Path $handleDirectory -Force | Out-Null
    Write-Step 'Stahuji podepsaný Microsoft Sysinternals Handle.'
    Invoke-WebRequest -Uri 'https://download.sysinternals.com/files/Handle.zip' -OutFile $handleArchive -UseBasicParsing -TimeoutSec 90
    Expand-Archive -LiteralPath $handleArchive -DestinationPath $handleDirectory -Force
    $handleBinary = Join-Path $handleDirectory 'handle64.exe'
    $handleSignature = Get-AuthenticodeSignature -LiteralPath $handleBinary
    if ($handleSignature.Status -ne 'Valid' -or $handleSignature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {
        throw 'Digitální podpis Microsoft Handle není platný.'
    }
    Remove-Item -LiteralPath $handleArchive -Force
} catch {
    Write-Warning 'Microsoft Handle se nepodařilo ověřit; registry handly zůstanou nedostupné.'
}

Write-Step 'Provádím závěrečnou kontrolu nainstalované aplikace.'
$requiredFiles = @(
    (Join-Path $installRoot 'raven_control.py'),
    (Join-Path $installRoot 'raven_brain.py'),
    (Join-Path $installRoot 'raven_intelligence.py'),
    (Join-Path $installRoot 'raven_next.py'),
    (Join-Path $installRoot 'agent_runtime.py'),
    (Join-Path $installRoot 'spustit-raven.ps1'),
    (Join-Path $installRoot 'stop-raven.ps1'),
    (Join-Path $installRoot 'hud\index.html'),
    (Join-Path $installRoot 'desktop-electron\main.js'),
    (Join-Path $installRoot 'desktop-electron\preload.js'),
    $desktopExecutable,
    $python,
    $jarvisCommand,
    $settingsPath,
    $agentsPath
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Závěrečná kontrola nenašla povinný soubor: $requiredFile"
    }
}
if ((Get-Item -LiteralPath $desktopExecutable).Length -lt 1MB) {
    throw 'Desktopový spouštěcí soubor Raven je neúplný nebo poškozený.'
}
$pythonValidation = @'
import importlib
import json
import py_compile
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
for name in (
    "agent_runtime.py",
    "raven_brain.py",
    "hardware_monitor.py",
    "network_monitor.py",
    "raven_control.py",
    "raven_intelligence.py",
    "raven_next.py",
    "telemetry_extensions.py",
):
    py_compile.compile(str(root / name), doraise=True)
for module in ("browser_use", "crawl4ai", "mcp", "onnxruntime", "psutil", "pydantic_ai", "starlette"):
    importlib.import_module(module)
for name in ("raven-settings.json", "raven-agents.json", "model-router.json"):
    with (root / "runtime" / name).open("r", encoding="utf-8-sig") as handle:
        json.load(handle)
'@
$pythonValidationPath = Join-Path $env:TEMP 'raven-install-validation.py'
Set-Content -LiteralPath $pythonValidationPath -Value $pythonValidation -Encoding utf8
try {
    Invoke-NativeChecked -FilePath $python -Arguments @($pythonValidationPath, $installRoot) -FailureMessage 'Python a konfigurace neprošly závěrečnou kontrolou'
} finally {
    Remove-Item -LiteralPath $pythonValidationPath -Force -ErrorAction SilentlyContinue
}
$node = Ensure-Node
Invoke-NativeChecked -FilePath $node.Source -Arguments @('--check', (Join-Path $electronProject 'main.js')) -FailureMessage 'Electron main.js neprošel kontrolou syntaxe'
Invoke-NativeChecked -FilePath $node.Source -Arguments @('--check', (Join-Path $electronProject 'preload.js')) -FailureMessage 'Electron preload.js neprošel kontrolou syntaxe'
$powerShellScripts = @('spustit-raven.ps1', 'stop-raven.ps1')
foreach ($scriptName in $powerShellScripts) {
    $scriptTokens = $null
    $scriptErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $installRoot $scriptName),
        [ref]$scriptTokens,
        [ref]$scriptErrors
    ) | Out-Null
    if ($scriptErrors.Count -gt 0) {
        throw "Skript $scriptName obsahuje chybu: $($scriptErrors[0].Message)"
    }
}
$verificationReport = [ordered]@{
    version = '1.2'
    verified_at = [DateTime]::UtcNow.ToString('o')
    install_root = $installRoot
    python = $python
    desktop_exe = $desktopExecutable
    status = 'passed'
}
$verificationReport | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtime 'install-verification.json') -Encoding utf8

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Raven 1.2.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $desktopExecutable
$shortcut.Arguments = ''
$shortcut.WorkingDirectory = $installRoot
$shortcut.Description = 'Spustit lokální Raven 1.2'
$shortcut.IconLocation = "$desktopExecutable,0"
$shortcut.Save()

$installConfigDirectory = Join-Path $env:LOCALAPPDATA 'Raven'
New-Item -ItemType Directory -Path $installConfigDirectory -Force | Out-Null
Set-Content -LiteralPath (Join-Path $installConfigDirectory 'install-path.txt') -Value $installRoot -Encoding utf8
Remove-Item -LiteralPath $installMarker -Force -ErrorAction SilentlyContinue
Write-Step "Instalace dokončena v $installRoot"
try { Stop-Transcript | Out-Null } catch {}

if (-not $NoLaunch) {
    Write-Step 'Spouštím Raven 1.2.'
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', "`"$installRoot\spustit-raven.ps1`"" `
        -WorkingDirectory $installRoot
}

exit 0
