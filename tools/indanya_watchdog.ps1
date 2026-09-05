param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [Parameter(Mandatory = $true)]
    [string]$SiteRoot,
    [switch]$Show
)

$ErrorActionPreference = "Stop"
$exe = [System.IO.Path]::GetFullPath($Executable)
$root = [System.IO.Path]::GetFullPath($SiteRoot)

if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    exit 2
}

$running = Get-CimInstance Win32_Process -Filter "Name = 'IndanyaStudio.exe'" |
    Where-Object {
        $_.ExecutablePath -eq $exe -or
        ($_.CommandLine -and $_.CommandLine.Contains($root))
    } |
    Select-Object -First 1

if ($running) {
    if ($Show -and $running.ProcessId) {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class IndanyaWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@ -ErrorAction SilentlyContinue
        $process = Get-Process -Id $running.ProcessId -ErrorAction SilentlyContinue
        if ($process -and $process.MainWindowHandle -ne 0) {
            [IndanyaWindow]::ShowWindowAsync($process.MainWindowHandle, 9) | Out-Null
            [IndanyaWindow]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
        }
    }
    exit 0
}

$arguments = @("--site-root", ('"' + $root + '"'))
if (-not $Show) {
    $arguments = @("--background") + $arguments
    Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden
} else {
    Start-Process -FilePath $exe -ArgumentList $arguments
}
