# Spouští všechny lokální služby aplikace Raven z instalační složky a otevře její rozhraní.
[CmdletBinding()]
param(
    [switch]$NoDesktop
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$logDirectory = Join-Path $root 'runtime\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$launcherLog = Join-Path $logDirectory 'launcher.log'
$stateDirectory = Join-Path $root 'runtime\state'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$ollamaProcessMarker = Join-Path $stateDirectory 'ollama-process.json'
$launcherMutex = [Threading.Mutex]::new($false, 'Local\RavenLauncherV1')
if (-not $launcherMutex.WaitOne(30000)) { throw 'Jiné spuštění aplikace Raven stále probíhá.' }

function Test-Port([int]$Port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-CommandLineContains([AllowNull()][string]$CommandLine, [string]$ExpectedText) {
    if (-not $CommandLine -or -not $ExpectedText) { return $false }
    return $CommandLine.IndexOf($ExpectedText, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-RavenPort([int]$Port, [string]$ExpectedCommand) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if (
        $process -and
        (Test-CommandLineContains -CommandLine $process.CommandLine -ExpectedText $root) -and
        (Test-CommandLineContains -CommandLine $process.CommandLine -ExpectedText $ExpectedCommand)
    ) {
        return $true
    }
    $processName = if ($process) { $process.Name } else { "PID $($listener.OwningProcess)" }
    throw "Port $Port používá jiný proces ($processName). Raven jej z bezpečnostních důvodů neukončil."
}

function Get-OwnedOllamaProcess {
    if (-not (Test-Path -LiteralPath $ollamaProcessMarker -PathType Leaf)) { return $null }
    try {
        $marker = Get-Content -LiteralPath $ollamaProcessMarker -Raw -Encoding utf8 | ConvertFrom-Json
        $markerRoot = [System.IO.Path]::GetFullPath([string]$marker.raven_root).TrimEnd('\')
        $markerExecutable = [System.IO.Path]::GetFullPath([string]$marker.executable)
        $markerPid = [int]$marker.pid
        if (
            $markerPid -le 0 -or
            -not $markerRoot.Equals($root.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            return $null
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $markerPid" -ErrorAction SilentlyContinue
        if (-not $process -or $process.Name -ne 'ollama.exe') { return $null }
        $processExecutable = [System.IO.Path]::GetFullPath([string]$process.ExecutablePath)
        if (-not $processExecutable.Equals($markerExecutable, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
        $markerStart = $marker.started_at_utc
        $recordedStart = if ($markerStart -is [DateTime]) {
            $markerStart.ToUniversalTime()
        } else {
            [DateTimeOffset]::Parse(
                [string]$markerStart,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).UtcDateTime
        }
        $actualStart = (Get-Process -Id $markerPid -ErrorAction Stop).StartTime.ToUniversalTime()
        if ([Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 10) { return $null }
        return $process
    } catch {
        return $null
    }
}

function Test-RavenOllamaPort {
    $listener = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $ownedProcess = Get-OwnedOllamaProcess
    $insideRoot = $process -and $process.ExecutablePath -and [System.IO.Path]::GetFullPath([string]$process.ExecutablePath).StartsWith(
        "$($root.TrimEnd('\'))\",
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if ($insideRoot -or ($ownedProcess -and $ownedProcess.ProcessId -eq $listener.OwningProcess)) {
        return $true
    }
    $processName = if ($process) { $process.Name } else { "PID $($listener.OwningProcess)" }
    throw "Port 11434 používá jiný proces ($processName). Raven jej z bezpečnostních důvodů nepoužil ani neukončil."
}

function Wait-RavenHttp([string]$Uri, [int]$Seconds) {
    for ($attempt = 0; $attempt -lt ($Seconds * 2); $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) { return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw "Služba Raven neodpověděla na adrese $Uri do $Seconds sekund."
}

function Wait-RavenPort([int]$Port, [string]$ExpectedCommand, [int]$Seconds) {
    for ($attempt = 0; $attempt -lt ($Seconds * 4); $attempt++) {
        if (Test-RavenPort -Port $Port -ExpectedCommand $ExpectedCommand) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "Služba Raven na portu $Port se nespustila do $Seconds sekund."
}

function Repair-PortablePythonPaths {
    $pythonPath = Join-Path $root 'src\.venv\Scripts\python.exe'
    $portablePythonRoot = Join-Path $root 'runtime\python-base'
    $portableBasePython = Join-Path $portablePythonRoot 'python.exe'
    if (-not (Test-Path -LiteralPath $portableBasePython -PathType Leaf)) {
        $managedPython = Get-ChildItem -LiteralPath (Join-Path $root 'runtime\python') -Filter 'python.exe' -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
        if ($managedPython) {
            $portableBasePython = $managedPython
            $portablePythonRoot = Split-Path -Parent $managedPython
        }
    }
    $venvConfig = Join-Path $root 'src\.venv\pyvenv.cfg'
    $sitePackages = Join-Path $root 'src\.venv\Lib\site-packages'
    $editablePath = Join-Path $sitePackages '_editable_impl_openjarvis.pth'
    $sourcePath = Join-Path $root 'src\src'
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'Python prostředí nebylo nalezeno. Nejdříve spusťte install.ps1.'
    }
    if (-not (Test-Path -LiteralPath $portableBasePython -PathType Leaf)) {
        throw 'Přenosný Python runtime nebyl nalezen. Spusťte opravu instalace pomocí install.ps1.'
    }
    if (-not (Test-Path -LiteralPath $venvConfig -PathType Leaf)) {
        throw 'Konfigurace Python prostředí nebyla nalezena.'
    }
    $configText = Get-Content -LiteralPath $venvConfig -Raw -Encoding utf8
    $repairedConfig = [regex]::Replace($configText, '(?m)^home\s*=.*$', "home = $portablePythonRoot")
    if ($repairedConfig -ne $configText) {
        Set-Content -LiteralPath $venvConfig -Value $repairedConfig.TrimEnd() -Encoding utf8
        Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) Opravena cesta základního Pythonu: $portablePythonRoot" -Encoding utf8
    }
    if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
        throw 'Balíčky Python prostředí nebyly nalezeny. Nejdříve spusťte install.ps1.'
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw 'Zdrojová část OpenJarvisu nebyla nalezena.'
    }
    $currentValue = if (Test-Path -LiteralPath $editablePath -PathType Leaf) {
        (Get-Content -LiteralPath $editablePath -Raw -Encoding utf8).Trim()
    } else { '' }
    if (-not $currentValue.Equals($sourcePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        Set-Content -LiteralPath $editablePath -Value $sourcePath -Encoding utf8
        Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) Opravena přenosná Python cesta: $sourcePath" -Encoding utf8
    }
    return $pythonPath
}

try {
    Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) Raven launcher start" -Encoding utf8
    $env:PATH = "$root\runtime\node;$env:PATH"
    $env:RAVEN_HOME = $root
    $env:OLLAMA_MODELS = "$root\runtime\ollama-models"
    $env:HF_HOME = "$root\runtime\huggingface"
    $env:PLAYWRIGHT_BROWSERS_PATH = "$root\runtime\ms-playwright"
    $env:TEMP = "$root\runtime\temp"
    $env:TMP = "$root\runtime\temp"
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    $pythonPath = Repair-PortablePythonPaths

$ollamaPath = "$root\runtime\ollama\ollama.exe"
if (-not (Test-Path -LiteralPath $ollamaPath)) {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCommand) { throw "Ollama nebyla nalezena. Nejdříve spusťte install.ps1." }
    $ollamaPath = $ollamaCommand.Source
}
if (-not (Test-RavenOllamaPort)) {
    $ollamaProcess = Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WorkingDirectory $root -WindowStyle Hidden -PassThru
    [ordered]@{
        raven_root = $root
        pid = $ollamaProcess.Id
        executable = [System.IO.Path]::GetFullPath($ollamaPath)
        started_at_utc = $ollamaProcess.StartTime.ToUniversalTime().ToString('o')
        port = 11434
    } | ConvertTo-Json | Set-Content -LiteralPath $ollamaProcessMarker -Encoding utf8
    for ($attempt = 0; $attempt -lt 40 -and -not (Test-RavenOllamaPort); $attempt++) { Start-Sleep -Milliseconds 250 }
    if (-not (Test-RavenOllamaPort)) { throw 'Lokální služba Ollama se nespustila na portu 11434.' }
}
Wait-RavenHttp -Uri 'http://127.0.0.1:11434/api/version' -Seconds 10
if (-not (Test-RavenPort -Port 8000 -ExpectedCommand 'openjarvis.cli')) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m', 'openjarvis.cli', 'serve', '--host', '127.0.0.1', '--port', '8000' -WorkingDirectory "$root\src" -WindowStyle Hidden
    Wait-RavenPort -Port 8000 -ExpectedCommand 'openjarvis.cli' -Seconds 90
}
Wait-RavenHttp -Uri 'http://127.0.0.1:8000/v1/agents/health' -Seconds 30
if (-not (Test-RavenPort -Port 5174 -ExpectedCommand 'http.server')) {
    Start-Process -FilePath $pythonPath -ArgumentList '-m', 'http.server', '5174', '--bind', '127.0.0.1' -WorkingDirectory "$root\hud" -WindowStyle Hidden
    Wait-RavenPort -Port 5174 -ExpectedCommand 'http.server' -Seconds 10
}
# Senzory CPU/GPU/disků: program, konfigurace, log i API zůstávají v instalační složce.
$hardwarePath = Get-ChildItem -Path "$root\runtime\librehardwaremonitor" -Filter "LibreHardwareMonitor.exe" -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$hardwareProcess = Get-Process -Name "LibreHardwareMonitor" -ErrorAction SilentlyContinue |
    Select-Object -First 1
$windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = [Security.Principal.WindowsPrincipal]::new($windowsIdentity)
$isAdministrator = $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($hardwarePath -and -not $hardwareProcess -and $isAdministrator) {
    try {
        Start-Process -FilePath $hardwarePath -WorkingDirectory "$root\runtime\librehardwaremonitor" -WindowStyle Hidden
        Start-Sleep -Seconds 2
    } catch {
        Write-Warning "LibreHardwareMonitor nebyl spuštěn: $($_.Exception.Message)"
    }
} elseif ($hardwarePath -and -not $hardwareProcess) {
    Write-Warning "LibreHardwareMonitor vyžaduje spuštění launcheru jako správce; pokračuji bez něj."
}
Wait-RavenHttp -Uri 'http://127.0.0.1:5174/' -Seconds 10
$telemetryProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText $root) -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText 'hardware_monitor.py')
    } |
    Select-Object -First 1
if (-not $telemetryProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList 'hardware_monitor.py' -WorkingDirectory $root -WindowStyle Hidden
}

# Trvalá pravidla a stav poskytovatele obsluhuje lokální API na loopbacku.
# Při novějším zdroji se restartuje pouze tato vlastní služba, aby HUD nikdy
# nezůstal připojený ke staré kopii backendu.
$controlProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText $root) -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText 'raven_control.py')
    }
# Služba je lehká a restart při hlavním spuštění zaručí aktuální zdroj i po
# instalaci aktualizace, bez závislosti na nespolehlivém čase procesu z WMI.
if (-not (Test-RavenPort -Port 8126 -ExpectedCommand 'raven_control.py') -or $controlProcesses) {
    $controlProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList 'raven_control.py' -WorkingDirectory $root -WindowStyle Hidden
    Wait-RavenPort -Port 8126 -ExpectedCommand 'raven_control.py' -Seconds 15
}
Wait-RavenHttp -Uri 'http://127.0.0.1:8126/settings' -Seconds 15

$networkProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText $root) -and
        (Test-CommandLineContains -CommandLine $_.CommandLine -ExpectedText 'network_monitor.py')
    } |
    Select-Object -First 1
if (-not $networkProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList 'network_monitor.py' -WorkingDirectory $root -WindowStyle Hidden
}

# Nový Electron shell obsahuje skutečný prohlížeč WebContentsView, Monaco a pracovní karty.
if (-not $NoDesktop) {
    $installedShell = "$root\Raven.exe"
    $desktopApp = "$root\desktop\Raven-Desktop.exe"
    $useDevelopmentShell = $env:RAVEN_DESKTOP_DEV -eq '1'
    if ((Test-Path -LiteralPath $installedShell) -and -not $useDevelopmentShell) {
        Start-Process -FilePath $installedShell -WorkingDirectory $root
    } elseif ((Test-Path -LiteralPath $desktopApp) -and -not $useDevelopmentShell) {
        Start-Process -FilePath $desktopApp -WorkingDirectory "$root\desktop"
    } else {
        $electron = "$root\desktop-electron\node_modules\electron\dist\electron.exe"
        if (-not (Test-Path -LiteralPath $electron)) {
            throw "Desktopová vrstva Raven nebyla nalezena. Spusťte install.ps1."
        }
        $electronArguments = @()
        if ($env:RAVEN_DEBUG_PORT -match '^\d{2,5}$') {
            $electronArguments += "--remote-debugging-port=$($env:RAVEN_DEBUG_PORT)"
        }
        $electronArguments += '.'
        Start-Process -FilePath $electron -ArgumentList $electronArguments -WorkingDirectory "$root\desktop-electron"
    }
}
    Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) Raven launcher success" -Encoding utf8
} catch {
    $message = "Raven se nepodařilo spustit: $($_.Exception.Message)"
    Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) $message`n$($_.ScriptStackTrace)" -Encoding utf8
    try {
        $popup = New-Object -ComObject WScript.Shell
        $popup.Popup("$message`n`nPodrobnosti: $launcherLog", 0, 'Raven 1.2', 16) | Out-Null
    } catch {}
    throw
} finally {
    try { $launcherMutex.ReleaseMutex() } catch {}
    $launcherMutex.Dispose()
}
