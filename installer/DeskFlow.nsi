Unicode True
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!ifndef DESKFLOW_RELEASE_BUILD
!error "Build with scripts\\build_release.ps1; DESKFLOW_RELEASE_BUILD is missing."
!endif

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "WinMessages.nsh"

!define MUI_CUSTOMFUNCTION_ABORT OnUserAbort

!define PRODUCT_NAME "DeskFlow"
!define PRODUCT_VERSION "5.1"
!define FILE_VERSION "5.1.0.0"
!define SOURCE_URL "https://github.com/parm2006/DeskFlow"
!define UNINSTALL_KEY \
  "Software\Microsoft\Windows\CurrentVersion\Uninstall\DeskFlow"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\DeskFlow-v${PRODUCT_VERSION}-Setup.exe"
InstallDir "$PROGRAMFILES64\DeskFlow"
Icon "..\app\assets\app_icon.ico"
VIProductVersion "${FILE_VERSION}"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "FileVersion" "${FILE_VERSION}"
VIAddVersionKey "FileDescription" "DeskFlow installer"
VIAddVersionKey "LegalCopyright" "DeskFlow contributors; GPL-3.0-or-later"

Var FirewallConsentGranted
Var FirewallConsentYesButton
Var FirewallConsentNoButton
Var InstallComplete
Var TransactionFilesWritten
Var ExistingInstallState

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
Page custom FirewallConsentPage FirewallConsentLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Function .onInit
  StrCpy $INSTDIR "$PROGRAMFILES64\DeskFlow"
  StrCpy $FirewallConsentGranted "0"
  StrCpy $InstallComplete "0"
  StrCpy $TransactionFilesWritten "0"
  StrCpy $ExistingInstallState "fresh"
  IfSilent silent_install interactive_install

silent_install:
  MessageBox MB_ICONSTOP|MB_OK \
    "Silent installation is not supported because firewall consent is required."
  SetErrorLevel 2
  Quit

interactive_install:
  Call ClassifyExistingInstall
FunctionEnd

Function ClassifyExistingInstall
  ; Classify without mutating disk. Every directory and every unknown filename
  ; fails closed, even when valid DeskFlow registry metadata is present.
  FindFirst $2 $3 "$INSTDIR\*"
  IfErrors classify_done

classify_next_entry:
  StrCmp $3 "" classify_scan_done
  StrCmp $3 "." classify_advance
  StrCmp $3 ".." classify_advance
  IfFileExists "$INSTDIR\$3\*.*" unknown_install_contents

  StrCmp $3 "DeskFlow.exe" classify_allowed_entry
  StrCmp $3 "Uninstall.exe" classify_allowed_entry
  StrCmp $3 "DeskFlow Source.url" classify_allowed_entry
  StrCmp $3 "DeskFlow.installing" classify_allowed_entry
  StrCmp $3 "THIRD_PARTY_NOTICES.txt" classify_allowed_entry
  StrCmp $3 "LICENSE" classify_allowed_entry
  Goto unknown_install_contents

classify_allowed_entry:
  StrCpy $ExistingInstallState "partial"

classify_advance:
  FindNext $2 $3
  Goto classify_next_entry

classify_scan_done:
  FindClose $2
  ${If} $ExistingInstallState == "partial"
    IfFileExists "$INSTDIR\DeskFlow.exe" 0 classify_done
    IfFileExists "$INSTDIR\Uninstall.exe" 0 classify_done
    ReadRegStr $0 HKLM "Software\DeskFlow" "InstallDir"
    ReadRegStr $1 HKLM "${UNINSTALL_KEY}" "UninstallString"
    StrCmp $0 "$INSTDIR" 0 classify_done
    StrCmp $1 '"$INSTDIR\Uninstall.exe"' 0 classify_done
    StrCpy $ExistingInstallState "upgrade"
  ${EndIf}

classify_done:
  ClearErrors
  Return

unknown_install_contents:
  FindClose $2
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow setup found an unknown file or folder in $INSTDIR. Setup will not overwrite it. Remove or move the unknown content, then retry."
  SetErrorLevel 3
  Quit
FunctionEnd

Function PreflightUpgrade
  IfFileExists "$INSTDIR\DeskFlow.upgrade-lock-test" upgrade_reserved_exists 0

  ClearErrors
  Rename "$INSTDIR\DeskFlow.exe" "$INSTDIR\DeskFlow.upgrade-lock-test"
  IfErrors upgrade_executable_locked

  ClearErrors
  Rename "$INSTDIR\DeskFlow.upgrade-lock-test" "$INSTDIR\DeskFlow.exe"
  IfErrors upgrade_restore_failed
  Return

upgrade_reserved_exists:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow cannot verify the existing installation because $INSTDIR\DeskFlow.upgrade-lock-test already exists. Remove that recovery file only after confirming DeskFlow.exe is present, then retry."
  SetErrorLevel 3
  Quit

upgrade_executable_locked:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow appears to be running or locked. Close DeskFlow completely, then run setup again. The existing installation was not removed."
  SetErrorLevel 3
  Quit

upgrade_restore_failed:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow could not restore its lock test. Rename $INSTDIR\DeskFlow.upgrade-lock-test to $INSTDIR\DeskFlow.exe, then retry setup. The existing uninstaller was not started."
  SetErrorLevel 3
  Quit
FunctionEnd

Function CleanupPartialInstall
  ; These are the complete installer-owned file allowlist. Never execute them.
  ClearErrors
  Delete "$INSTDIR\DeskFlow.exe"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$INSTDIR\DeskFlow Source.url"
  Delete "$INSTDIR\DeskFlow.installing"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.txt"
  Delete "$INSTDIR\LICENSE"
  IfErrors partial_cleanup_failed
  RMDir "$INSTDIR"
  IfErrors partial_cleanup_failed

  Delete "$DESKTOP\DeskFlow.lnk"
  Delete "$SMPROGRAMS\DeskFlow.lnk"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\DeskFlow"
  Return

partial_cleanup_failed:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow could not remove its incomplete installer-owned files. Close DeskFlow and retry. Unknown files were not removed."
  SetErrorLevel 3
  Quit
FunctionEnd

Function CleanupUpgradeRemnants
  ; The verified old uninstaller owns removal. Clean only the same known files
  ; if an older uninstaller leaves installer-owned remnants behind.
  ClearErrors
  Delete "$INSTDIR\DeskFlow.exe"
  IfErrors upgrade_cleanup_executable_locked
  Delete "$INSTDIR\DeskFlow Source.url"
  Delete "$INSTDIR\DeskFlow.installing"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  IfErrors upgrade_cleanup_failed
  Return

upgrade_cleanup_executable_locked:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow.exe is still in use. Close DeskFlow completely, then retry setup. The new version was not installed."
  SetErrorLevel 3
  Quit

upgrade_cleanup_failed:
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow could not remove an installer-owned file left by the previous version. Close DeskFlow completely, then retry setup."
  SetErrorLevel 3
  Quit
FunctionEnd

Function PrepareExistingInstall
  ${If} $ExistingInstallState == "upgrade"
    Call PreflightUpgrade
  ${ElseIf} $ExistingInstallState == "partial"
    Call CleanupPartialInstall
  ${EndIf}
FunctionEnd

Function FirewallConsentPage
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 70u \
    "Allow DeskFlow Server on private local networks (TCP ports 28903-28905).$\r$\n$\r$\nOnly this DeskFlow executable may receive connections from the local subnet. If Windows has a matching block, setup may disable only that exact executable rule. Public networks remain blocked."
  Pop $0

  ${NSD_CreateButton} 8% 85u 84% 22u "Yes - Continue"
  Pop $FirewallConsentYesButton
  ${NSD_OnClick} $FirewallConsentYesButton FirewallConsentYes

  ${NSD_CreateButton} 8% 114u 84% 22u \
    "No - Cancel installation"
  Pop $FirewallConsentNoButton
  ${NSD_OnClick} $FirewallConsentNoButton FirewallConsentNo

  GetDlgItem $0 $HWNDPARENT 1
  EnableWindow $0 0
  nsDialogs::Show
FunctionEnd

Function FirewallConsentYes
  Pop $0
  ${If} $FirewallConsentGranted == "denied"
    Return
  ${EndIf}
  StrCpy $FirewallConsentGranted "1"
  GetDlgItem $0 $HWNDPARENT 1
  EnableWindow $0 1
  SendMessage $HWNDPARENT ${WM_COMMAND} 1 0
FunctionEnd

Function FirewallConsentNo
  Pop $0
  StrCpy $FirewallConsentGranted "denied"
  EnableWindow $FirewallConsentYesButton 0
  EnableWindow $FirewallConsentNoButton 0
  SetErrorLevel 2
  SendMessage $HWNDPARENT ${WM_COMMAND} 2 0
FunctionEnd

Function FirewallConsentLeave
  ${If} $FirewallConsentGranted != "1"
    SetErrorLevel 2
    Quit
  ${EndIf}
FunctionEnd

Function RollbackInstall
  ; The repair helper owns all firewall rollback before it returns failure.
  ; Before repair runs, this installer has not changed firewall state.
  Call CleanupTransactionFiles
FunctionEnd

Function CleanupTransactionFiles
  Delete "$DESKTOP\DeskFlow.lnk"
  Delete "$SMPROGRAMS\DeskFlow.lnk"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$INSTDIR\DeskFlow Source.url"
  Delete "$INSTDIR\DeskFlow.installing"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\DeskFlow.exe"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\DeskFlow"
  RMDir "$INSTDIR"
FunctionEnd

Function OnUserAbort
  ${If} $InstallComplete != "1"
    ${If} $TransactionFilesWritten == "1"
      Call RollbackInstall
    ${EndIf}
  ${EndIf}
FunctionEnd

Function AbortAfterRollback
  Call RollbackInstall
  MessageBox MB_ICONSTOP|MB_OK \
    "Windows Firewall setup failed. DeskFlow was not installed."
  SetErrorLevel 3
  Quit
FunctionEnd

Section "DeskFlow" SEC_DESKFLOW
  ; Consent is complete. Do not allow interruption once an existing packaged
  ; install or partial remnant can be mutated.
  GetDlgItem $2 $HWNDPARENT 2
  EnableWindow $2 0
  Call PrepareExistingInstall

  ${If} $ExistingInstallState == "upgrade"
    ExecWait '"$INSTDIR\Uninstall.exe" /S _?=$INSTDIR' $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK \
        "The existing DeskFlow uninstaller failed. The new version was not installed."
      SetErrorLevel 3
      Quit
    ${EndIf}
    ; _?= keeps the old uninstaller in place so ExecWait observes its real
    ; completion. The elevated parent can delete that exact verified file once
    ; it returns, then enumerate rather than confusing existence with content.
    Call CleanupUpgradeRemnants
    FindFirst $2 $3 "$INSTDIR\*"
    IfErrors upgrade_ready

upgrade_check_next_entry:
    StrCmp $3 "" upgrade_scan_empty
    StrCmp $3 "." upgrade_check_advance
    StrCmp $3 ".." upgrade_check_advance
    FindClose $2
    Goto upgrade_directory_not_empty

upgrade_check_advance:
    FindNext $2 $3
    Goto upgrade_check_next_entry

upgrade_scan_empty:
    FindClose $2
    Goto upgrade_ready

upgrade_directory_not_empty:
    MessageBox MB_ICONSTOP|MB_OK \
      "The previous DeskFlow installation left files behind in $INSTDIR. The new version was not installed."
    SetErrorLevel 3
    Quit

upgrade_ready:
    ClearErrors
  ${EndIf}

  SetOutPath "$INSTDIR"
  File "/oname=DeskFlow.exe" "..\dist\DeskFlow-v${PRODUCT_VERSION}.exe"
  File "..\LICENSE"
  File "..\build\THIRD_PARTY_NOTICES.txt"
  ClearErrors
  FileOpen $1 "$INSTDIR\DeskFlow.installing" w
  IfErrors marker_write_failed
  FileClose $1
  StrCpy $TransactionFilesWritten "1"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  FileOpen $1 "$INSTDIR\DeskFlow Source.url" w
  FileWrite $1 "[InternetShortcut]$\r$\nURL=${SOURCE_URL}$\r$\n"
  FileClose $1

  CreateShortCut "$DESKTOP\DeskFlow.lnk" "$INSTDIR\DeskFlow.exe" "" "$INSTDIR\DeskFlow.exe" 0
  CreateShortCut "$SMPROGRAMS\DeskFlow.lnk" "$INSTDIR\DeskFlow.exe" "" "$INSTDIR\DeskFlow.exe" 0
  WriteRegStr HKLM "Software\DeskFlow" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" \
    "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKLM "${UNINSTALL_KEY}" \
    "DisplayIcon" "$INSTDIR\DeskFlow.exe"
  WriteRegStr HKLM "${UNINSTALL_KEY}" \
    "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINSTALL_KEY}" "URLInfoAbout" "${SOURCE_URL}"
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair" 1

  ; Repair performs its own effective reinspection and rollback. Keep it as
  ; the final fallible step so no later installer failure can strand disabled
  ; conflict rules outside that exact-object transaction.
  ExecWait \
    '"$INSTDIR\DeskFlow.exe" --deskflow-firewall-helper repair --base-port 28903' $0
  ${If} $0 != 0
    Call AbortAfterRollback
  ${EndIf}

  StrCpy $InstallComplete "1"
  Delete "$INSTDIR\DeskFlow.installing"
  Goto install_complete

marker_write_failed:
  Call CleanupTransactionFiles
  MessageBox MB_ICONSTOP|MB_OK \
    "DeskFlow could not create its installation recovery marker. No firewall rule was changed."
  SetErrorLevel 3
  Quit

install_complete:
SectionEnd

Section "Uninstall"
  ExecWait \
    '"$INSTDIR\DeskFlow.exe" --deskflow-firewall-helper remove' $0
  ${If} $0 != 0
    IfSilent uninstall_firewall_warning_done 0
    MessageBox MB_ICONEXCLAMATION|MB_OK \
      "DeskFlow could not remove its firewall rule. Windows policy may already have removed or may manage the rule. Uninstall will continue."
uninstall_firewall_warning_done:
  ${EndIf}

  Delete "$DESKTOP\DeskFlow.lnk"
  Delete "$SMPROGRAMS\DeskFlow.lnk"
  Delete "$INSTDIR\DeskFlow Source.url"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\DeskFlow.exe"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\DeskFlow"
  RMDir "$INSTDIR"
SectionEnd
