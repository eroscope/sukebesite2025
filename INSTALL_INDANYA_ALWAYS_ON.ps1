param(
    [string]$SiteRoot = $PSScriptRoot,
    [string]$BuildName = "dist-growth-v33",
    [switch]$Enable
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($SiteRoot)
$exe = Join-Path $root "$BuildName\IndanyaStudio\IndanyaStudio.exe"
$watchdog = Join-Path $root "tools\indanya_watchdog.ps1"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Application executable not found: $exe"
}
if (-not (Test-Path -LiteralPath $watchdog -PathType Leaf)) {
    throw "Watchdog script not found: $watchdog"
}

$taskName = "IndanyaStudio Always On"
$arguments = (
    '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" ' +
    '-Executable "{1}" -SiteRoot "{2}"'
) -f $watchdog, $exe, $root
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Indanya Studio background automation watchdog" `
    -Force | Out-Null
if ($Enable) {
    Enable-ScheduledTask -TaskName $taskName | Out-Null
} else {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
}

$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
New-Item -ItemType Directory -Path $startup -Force | Out-Null
$startupFile = Join-Path $startup "IndanyaStudioBackground.cmd"
$startupContent = (
    "@echo off" + [Environment]::NewLine +
    ('powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass ' +
     '-File "{0}" -Executable "{1}" -SiteRoot "{2}"' -f $watchdog, $exe, $root) +
    [Environment]::NewLine
)
$disabledStartupFile = "$startupFile.disabled"
if ($Enable) {
    [System.IO.File]::WriteAllText(
        $startupFile,
        $startupContent,
        [System.Text.UTF8Encoding]::new($false)
    )
    Remove-Item -LiteralPath $disabledStartupFile -Force -ErrorAction SilentlyContinue
} else {
    if (Test-Path -LiteralPath $startupFile -PathType Leaf) {
        Move-Item -LiteralPath $startupFile -Destination $disabledStartupFile -Force
    }
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $desktop "Indanya Article Studio.lnk"
foreach ($candidatePath in Get-ChildItem -LiteralPath $desktop -Filter "*.lnk" -File) {
    $candidate = $shell.CreateShortcut($candidatePath.FullName)
    if (
        $candidate.TargetPath -like "*IndanyaStudio.exe" -or
        $candidate.Arguments -like "*indanya_watchdog.ps1*"
    ) {
        $shortcutPath = $candidatePath.FullName
        break
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = (
    '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" ' +
    '-Executable "{1}" -SiteRoot "{2}" -Show'
) -f $watchdog, $exe, $root
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = "Open Indanya Article Studio"
$shortcut.Save()

Write-Output "TASK=$taskName"
Write-Output "ENABLED=$([bool]$Enable)"
Write-Output "SHORTCUT=$shortcutPath"
Write-Output "EXE=$exe"
