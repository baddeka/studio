# Video pipeline setup for Windows. Double-click setup.cmd, or run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
# Safe to run again at any time: every step checks what is already there and only does what is missing.
# Options: -SkipTests (no unit tests), -SkipSelftest (no test render).
param(
    [switch]$SkipTests,
    [switch]$SkipSelftest
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell downloads are very slow with the progress bar on
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
# $IsLinux and $IsMacOS exist only in PowerShell 7; this lets the script be tested on Linux too.
$OnWindows = -not ($IsLinux -or $IsMacOS)
$VenvPython = if ($OnWindows) { 'Scripts\python.exe' } else { 'bin/python' }
$script:Problems = @()

function Say([string]$Text) { Write-Host $Text }
function Ok([string]$Text) { Write-Host "  ok    $Text" -ForegroundColor Green }
function Note([string]$Text) { Write-Host "  note  $Text" -ForegroundColor Yellow }
function Bad([string]$Text) { Write-Host "  FAIL  $Text" -ForegroundColor Red; $script:Problems += $Text }
function Step([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

function Get-PyVersion([string]$Exe, [string[]]$Pre) {
    # Version of a Python interpreter, or $null if it does not run (the Microsoft Store stub does not).
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    if (-not $Pre) { $Pre = @() }
    try {
        $out = & $Exe @Pre -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $line = ($out | Select-Object -Last 1).ToString().Trim()
    if ($line -match '^\d+\.\d+\.\d+$') { return [version]$line }
    return $null
}

function Find-Python {
    $cands = New-Object System.Collections.ArrayList
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('3.14', '3.13', '3.12', '3.11', '3')) { [void]$cands.Add(@('py', "-$v")) }
    }
    foreach ($name in @('python', 'python3')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { [void]$cands.Add(@($name)) }
    }
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Python') }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    foreach ($dir in $roots) {
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        $found = Get-ChildItem -LiteralPath $dir -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending
        foreach ($d in $found) {
            $exe = Join-Path $d.FullName 'python.exe'
            if (Test-Path -LiteralPath $exe) { [void]$cands.Add(@($exe)) }
        }
    }
    foreach ($c in $cands) {
        $pre = @()
        if ($c.Count -gt 1) { $pre = @($c[1..($c.Count - 1)]) }
        $ver = Get-PyVersion $c[0] $pre
        if ($ver -and $ver -ge [version]'3.11') { return @{ Exe = $c[0]; Pre = $pre; Version = $ver } }
    }
    return $null
}

function Test-Ffmpeg([string]$Exe) {
    # First line of "ffmpeg -version" if this ffmpeg runs and has the parts the pipeline needs, else $null.
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    try {
        $ver = & $Exe -hide_banner -version 2>$null
        $filters = (& $Exe -hide_banner -filters 2>$null) -join "`n"
        $encoders = (& $Exe -hide_banner -encoders 2>$null) -join "`n"
    } catch { return $null }
    if (-not $ver) { return $null }
    if ($filters -notmatch '\sass\s' -or $encoders -notmatch '\slibx264\s') { return $null }
    return (($ver | Select-Object -First 1).ToString() -replace ' Copyright.*$', '')
}

function Get-Download([string]$Url, [string]$OutFile) {
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -Headers @{ 'User-Agent' = 'vidpipe-setup' } -ErrorAction Stop
}

function Install-Ffmpeg([string]$Dest) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('vp-ffmpeg-' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($tmp)
    $zip = Join-Path $tmp 'ffmpeg.zip'
    $ok = $false
    try {
        Say '  downloading the ffmpeg "essentials" build from gyan.dev (about 100 MB) ...'
        Get-Download 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $zip
        $want = (Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' -UseBasicParsing -ErrorAction Stop).Content
        if ($want -is [byte[]]) { $want = [Text.Encoding]::ASCII.GetString($want) }
        $want = ($want.Trim() -split '\s+')[0]
        $got = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
        if ($got -ne $want) { throw "checksum does not match ($got vs $want)" }
        $ok = $true
    } catch {
        Note "gyan.dev did not work ($($_.Exception.Message)); trying the same build on GitHub ..."
        try {
            $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/GyanD/codexffmpeg/releases/latest' -UseBasicParsing -Headers @{ 'User-Agent' = 'vidpipe-setup' } -ErrorAction Stop
            $asset = $rel.assets | Where-Object { $_.name -like '*essentials_build.zip' } | Select-Object -First 1
            if (-not $asset) { throw 'no essentials build in the latest release' }
            Get-Download $asset.browser_download_url $zip
            if ($asset.digest -and $asset.digest -like 'sha256:*') {
                $got = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
                if ($got -ne $asset.digest.Substring(7)) { throw 'checksum does not match' }
            }
            $ok = $true
        } catch {
            Bad "could not download ffmpeg: $($_.Exception.Message)"
        }
    }
    if ($ok) {
        $out = Join-Path $tmp 'x'
        [void][IO.Directory]::CreateDirectory($out)
        if (Get-Command tar.exe -ErrorAction SilentlyContinue) { & tar.exe -xf $zip -C $out 2>$null }
        $exe = Get-ChildItem -LiteralPath $out -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $exe) {
            Expand-Archive -LiteralPath $zip -DestinationPath $out -Force
            $exe = Get-ChildItem -LiteralPath $out -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if ($exe) {
            [void][IO.Directory]::CreateDirectory($Dest)
            Copy-Item -Path (Join-Path $exe.DirectoryName '*') -Destination $Dest -Force
        } else {
            Bad 'ffmpeg.exe was not found inside the download'
            $ok = $false
        }
    }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    return $ok
}

function Finish {
    Step 'Summary'
    if ($script:Problems.Count -eq 0) {
        Write-Host 'Setup finished. Everything works.' -ForegroundColor Green
        Say ''
        Say 'Next steps:'
        Say '  1. Open .env in Notepad and paste your ElevenLabs API key (and, optionally, an Anthropic API key).'
        Say '  2. Make a video, in PowerShell in this folder:'
        Say '       .\vp new ftx "How FTX lost 8 billion dollars of customer money"'
        Say '       .\vp run ftx'
        Say '  The guide is in INSTALL.md and README.md.'
        exit 0
    }
    Write-Host "Setup finished with $($script:Problems.Count) problem(s):" -ForegroundColor Red
    foreach ($p in $script:Problems) { Say "  - $p" }
    Say 'Fix them and run setup.cmd again; it only redoes what is missing.'
    exit 1
}

function Main {
    Set-Location -LiteralPath $Root
    Say "Video pipeline setup in $Root"

    Step 'Python'
    $py = Find-Python
    if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Say '  Python 3.11 or newer was not found. Installing Python 3.14 for this user with winget ...'
        & winget install --id Python.Python.3.14 -e --scope user --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Bad 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run setup.cmd again.'
        Finish
    }
    Ok "Python $($py.Version) ($((@($py.Exe) + $py.Pre) -join ' '))"

    Step 'Python environment (.venv) and packages'
    $venvDir = Join-Path $Root '.venv'
    $venvPy = Join-Path $venvDir $VenvPython
    $vv = $null
    if (Test-Path -LiteralPath $venvPy) { $vv = Get-PyVersion $venvPy @() }
    if (-not $vv -or $vv -lt [version]'3.11') {
        if (Test-Path -LiteralPath $venvDir) { Remove-Item -LiteralPath $venvDir -Recurse -Force }
        $pre = $py.Pre
        & $py.Exe @pre -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPy)) {
            Bad 'could not create the .venv folder with Python'
            Finish
        }
        Ok 'created .venv'
    } else {
        Ok "using the existing .venv (Python $vv)"
    }
    & $venvPy -m pip install --disable-pip-version-check --quiet --upgrade pip
    & $venvPy -m pip install --disable-pip-version-check --quiet -r (Join-Path $Root 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Bad 'installing the Python packages failed (see the messages above); check the internet connection'
    } else {
        Ok 'numpy, pillow, requests, pyyaml, python-dotenv, rapidfuzz'
    }
    & $venvPy -m pip install --disable-pip-version-check --quiet 'anthropic>=2.12'
    if ($LASTEXITCODE -ne 0) {
        Note 'the anthropic package did not install; it is only needed if you put an ANTHROPIC_API_KEY in .env'
    } else {
        Ok 'anthropic (used only with an Anthropic API key)'
    }

    Step 'ffmpeg'
    $ffBin = Join-Path $Root 'tools\ffmpeg\bin'
    $ffv = Test-Ffmpeg (Join-Path $ffBin 'ffmpeg.exe')
    if ($ffv) {
        Ok "$ffv in tools\ffmpeg\bin"
    } else {
        $pathFf = Test-Ffmpeg 'ffmpeg'
        if ($pathFf) {
            Ok "$pathFf (already installed on this computer)"
        } elseif (Install-Ffmpeg $ffBin) {
            $ffv = Test-Ffmpeg (Join-Path $ffBin 'ffmpeg.exe')
            if ($ffv) { Ok "$ffv installed in tools\ffmpeg\bin" } else { Bad 'the downloaded ffmpeg does not run' }
        }
    }

    Step 'Fonts'
    $fontDir = Join-Path $Root 'library\fonts'
    [void][IO.Directory]::CreateDirectory($fontDir)
    $fonts = @(
        @('Anton-Regular.ttf', 'ofl/anton/Anton-Regular.ttf'),
        @('Inter[opsz,wght].ttf', 'ofl/inter/Inter%5Bopsz,wght%5D.ttf'),
        @('IBMPlexMono-Regular.ttf', 'ofl/ibmplexmono/IBMPlexMono-Regular.ttf'),
        @('IBMPlexMono-SemiBold.ttf', 'ofl/ibmplexmono/IBMPlexMono-SemiBold.ttf')
    )
    $fontOk = $true
    foreach ($f in $fonts) {
        # .NET file calls, because PowerShell reads [ ] in a path as a wildcard
        $path = [IO.Path]::Combine($fontDir, $f[0])
        if ([IO.File]::Exists($path)) { continue }
        $tmpf = Join-Path ([IO.Path]::GetTempPath()) ('vp-font-' + [guid]::NewGuid().ToString('N') + '.ttf')
        try {
            Get-Download ('https://raw.githubusercontent.com/google/fonts/main/' + $f[1]) $tmpf
            [IO.File]::Move($tmpf, $path)
            Say "  downloaded $($f[0])"
        } catch {
            $fontOk = $false
            Bad "could not download the font $($f[0]): $($_.Exception.Message)"
        }
    }
    if ($fontOk) { Ok 'Anton, Inter, IBM Plex Mono in library\fonts' }

    Step 'Settings'
    $envFile = Join-Path $Root '.env'
    if (Test-Path -LiteralPath $envFile) {
        Ok '.env already exists (left as it is)'
    } else {
        Copy-Item -LiteralPath (Join-Path $Root '.env.example') -Destination $envFile
        Ok 'created .env from .env.example; paste your keys into it'
    }
    foreach ($d in @('videos', 'library\music', 'library\sfx')) {
        [void][IO.Directory]::CreateDirectory((Join-Path $Root $d))
    }

    Step 'Checking the tools (vp doctor)'
    & $venvPy -m vidpipe doctor
    if ($LASTEXITCODE -ne 0) { Bad 'vp doctor found a problem (see above)' }

    if (-not $SkipTests) {
        Step 'Unit tests'
        & $venvPy -m unittest discover -s tests -q
        if ($LASTEXITCODE -ne 0) { Bad 'some unit tests failed (see above)' } else { Ok 'unit tests passed' }
    }

    if (-not $SkipSelftest) {
        Step 'Self-test: rendering a 40-second test video (1-3 minutes)'
        & $venvPy -m vidpipe selftest
        if ($LASTEXITCODE -ne 0) { Bad 'the self-test failed (see above)' }
    }

    Finish
}

if ($MyInvocation.InvocationName -ne '.') { Main }
