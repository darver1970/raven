# Spouští všechny lokální služby aplikace Raven z instalační složky a otevře její rozhraní.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$logDirectory = Join-Path $root 'runtime\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$launcherLog = Join-Path $logDirectory 'launcher.log'
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

$ollamaPath = "$root\runtime\ollama\ollama.exe"
if (-not (Test-Path -LiteralPath $ollamaPath)) {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCommand) { throw "Ollama nebyla nalezena. Nejdříve spusťte install.ps1." }
    $ollamaPath = $ollamaCommand.Source
}
if (-not (Test-Port 11434)) {
    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WorkingDirectory $root -WindowStyle Hidden
    for ($attempt = 0; $attempt -lt 40 -and -not (Test-Port 11434); $attempt++) { Start-Sleep -Milliseconds 250 }
    if (-not (Test-Port 11434)) { throw 'Lokální služba Ollama se nespustila na portu 11434.' }
}
Wait-RavenHttp -Uri 'http://127.0.0.1:11434/api/version' -Seconds 10
if (-not (Test-RavenPort -Port 8000 -ExpectedCommand 'jarvis')) {
    Start-Process -FilePath "$root\src\.venv\Scripts\jarvis.exe" -ArgumentList "serve", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory "$root\src" -WindowStyle Hidden
    Wait-RavenPort -Port 8000 -ExpectedCommand 'jarvis' -Seconds 90
}
Wait-RavenHttp -Uri 'http://127.0.0.1:8000/v1/agents/health' -Seconds 30
$pythonPath = "$root\src\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python prostředí nebylo nalezeno. Nejdříve spusťte install.ps1."
}
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
$desktopApp = "$root\desktop\Raven-Desktop.exe"
if (Test-Path -LiteralPath $desktopApp) {
    Start-Process -FilePath $desktopApp -WorkingDirectory "$root\desktop"
} else {
    $electron = "$root\desktop-electron\node_modules\electron\dist\electron.exe"
    if (-not (Test-Path -LiteralPath $electron)) {
        throw "Desktopová vrstva Raven nebyla nalezena. Spusťte install.ps1."
    }
    Start-Process -FilePath $electron -ArgumentList '.' -WorkingDirectory "$root\desktop-electron"
}
    Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) Raven launcher success" -Encoding utf8
} catch {
    $message = "Raven se nepodařilo spustit: $($_.Exception.Message)"
    Add-Content -LiteralPath $launcherLog -Value "$(Get-Date -Format o) $message`n$($_.ScriptStackTrace)" -Encoding utf8
    try {
        $popup = New-Object -ComObject WScript.Shell
        $popup.Popup("$message`n`nPodrobnosti: $launcherLog", 0, 'Raven 1.0', 16) | Out-Null
    } catch {}
    throw
} finally {
    try { $launcherMutex.ReleaseMutex() } catch {}
    $launcherMutex.Dispose()
}
