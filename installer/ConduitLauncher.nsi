Unicode True
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
!include "LogicLib.nsh"
Name "Conduit"
OutFile "..\dist\ConduitLauncher.exe"
Icon "..\app\assets\app_icon.ico"
VIProductVersion "7.3.1.0"
VIAddVersionKey "ProductName" "Conduit"
VIAddVersionKey "FileDescription" "Conduit launcher"
VIAddVersionKey "FileVersion" "7.3.1.0"
VIAddVersionKey "LegalCopyright" "Conduit contributors; GPL-3.0-only"

Section
  ; Keep the shortcut target app-specific so Windows Search indexes Conduit.
  ; The approved task exists even when its optional sign-in trigger is off.
  ReadRegStr $0 HKLM "Software\Conduit" "LaunchTaskName"
  ${If} $0 == ""
    MessageBox MB_ICONSTOP|MB_OK "Conduit's approved launch task is missing. Run Conduit setup again to repair it."
    SetErrorLevel 3
    Quit
  ${EndIf}
  nsExec::ExecToStack '"$SYSDIR\schtasks.exe" /Run /TN "$0"'
  Pop $1
  Pop $2
  ${If} $1 != "0"
    MessageBox MB_ICONSTOP|MB_OK "Conduit could not start its approved launch task. Run Conduit setup again to repair it."
    SetErrorLevel 3
  ${EndIf}
SectionEnd
