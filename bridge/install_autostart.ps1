# Starts the bridge (hidden) every time you log in to Windows. No admin rights needed.
#   .\install_autostart.ps1            install
#   .\install_autostart.ps1 -Remove    uninstall
# It drops a small launcher script in your Startup folder. iCUE starts at login too, so the
# bridge waits a few seconds first; it logs to bridge\bridge.log.
param([switch]$Remove, [int]$DelaySeconds = 20)

$bridgeDir = $PSScriptRoot
$python = Join-Path $bridgeDir "..\..\.venv\Scripts\python.exe"
$launcher = Join-Path ([Environment]::GetFolderPath('Startup')) "iCUE Bridge.vbs"

if ($Remove) {
    Remove-Item $launcher -ErrorAction SilentlyContinue
    "Removed $launcher"
    return
}

$python = (Resolve-Path $python).Path
# Run python.exe (not pythonw.exe) hidden, so the existing Windows Firewall allowance still applies.
$vbs = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$bridgeDir"
sh.Run """$python"" server.py --delay $DelaySeconds", 0, False
"@
[IO.File]::WriteAllText($launcher, $vbs, (New-Object Text.ASCIIEncoding))
"Installed $launcher"
"The bridge will start $DelaySeconds s after you next log in."
