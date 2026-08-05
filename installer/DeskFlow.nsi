Unicode True
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "WinMessages.nsh"

!define MUI_CUSTOMFUNCTION_ABORT OnUserAbort

!define PRODUCT_NAME "DeskFlow"
!define PRODUCT_VERSION "4.3s"
!define FILE_VERSION "4.3.0.0"
!define SOURCE_URL "https://github.com/parm2006/DeskFlow"
!define UNINSTALL_KEY \
  "Software\Microsoft\Windows\CurrentVersion\Uninstall\DeskFlow"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\DeskFlow-${PRODUCT_VERSION}-Setup.exe"
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
  IfSilent silent_install interactive_install

silent_install:
  MessageBox MB_ICONSTOP|MB_OK \
    "Silent installation is not supported because firewall consent is required."
  SetErrorLevel 2
  Quit

interactive_install:
  IfFileExists "$INSTDIR\*.*" existing_install allow_install

existing_install:
    MessageBox MB_ICONSTOP|MB_OK \
      "DeskFlow will not overwrite existing files. If this is an incomplete installation, run its Uninstall.exe to retry firewall cleanup first."
  SetErrorLevel 3
  Quit

allow_install:
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
  StrCpy $FirewallConsentGranted "1"
  GetDlgItem $0 $HWNDPARENT 1
  EnableWindow $0 1
  SendMessage $HWNDPARENT ${WM_COMMAND} 1 0
FunctionEnd

Function FirewallConsentNo
  MessageBox MB_ICONINFORMATION|MB_OK \
    "DeskFlow was not installed because firewall consent is required."
  SetErrorLevel 2
  Quit
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
  SetOutPath "$INSTDIR"
  File "..\dist\DeskFlow.exe"
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

  CreateShortCut "$DESKTOP\DeskFlow.lnk" "$INSTDIR\DeskFlow.exe"
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
  GetDlgItem $2 $HWNDPARENT 2
  EnableWindow $2 0
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
    MessageBox MB_ICONEXCLAMATION|MB_OK \
      "DeskFlow could not remove its firewall rule. Windows policy may already have removed or may manage the rule. Uninstall will continue."
  ${EndIf}

  Delete "$DESKTOP\DeskFlow.lnk"
  Delete "$INSTDIR\DeskFlow Source.url"
  Delete "$INSTDIR\THIRD_PARTY_NOTICES.txt"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\DeskFlow.exe"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKLM "${UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\DeskFlow"
  RMDir "$INSTDIR"
SectionEnd
