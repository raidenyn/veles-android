; Veles Native Bridge - per-user Windows installer frontend (OTP-25 Task 10).
;
; A THIN frontend over the Rust transaction engine (carrier.exe, the signed
; maintenance binary). It extracts only the authenticated product inputs to a
; private plugin-staging directory, starts the engine with fixed, quoted
; arguments through nsExec (CreateProcess; no shell), and shows the result. It
; owns no install algorithm, writes nothing under the install root, never
; touches Chrome registration (the engine alone publishes the Native Messaging
; manifest and key) and never starts the native host.
;
; STATUS: this script is NOT COMPILED here (no makensis on the development host)
; and NOT RUN (no Windows runner). Its contract is pinned by
; scripts/test_windows_package.py; compilation with the pinned NSIS 3.08 and all
; runtime behaviour are Task 13 / Windows-runner evidence.
;
; Packaging contract (Task 13 drives these; the script refuses to compile without
; the required ones):
;   /DVERSION=<x.y.z>              release version shown in Installed Apps
;   /DINPUT_DIR=<dir>              unsigned input tree: carrier.exe, descriptor.json,
;                                  inventory.json, [sealed-inventory.json], payload\
;   /DOUTFILE=<setup.exe>          installer output
;   /DUNINSTALLER_SIGNED=<exe>     the uninstaller, generated and SIGNED during
;                                  packaging (see below) and embedded verbatim
;   /DGENERATE_UNINSTALLER         packaging-time mode: the produced executable only
;                                  writes uninstall.exe next to itself and quits.
;
; Signed uninstaller recipe (packaging time, never at end-user runtime):
;   1. makensis /DGENERATE_UNINSTALLER /DVERSION=... /DINPUT_DIR=... /DOUTFILE=gen.exe installer.nsi
;   2. run gen.exe once; it writes uninstall.exe beside itself, then quits
;   3. sign uninstall.exe with the release publisher identity (Task 13)
;   4. makensis /DVERSION=... /DINPUT_DIR=... /DOUTFILE=setup.exe /DUNINSTALLER_SIGNED=uninstall.exe installer.nsi
;   5. the signed uninstaller is inventoried as a signable payload image; sign
;      setup.exe last. NSIS's own extraction/preparation belongs to the unsigned
;      packaging step, not to the signing job.
;
; Seam owned by Task 13: the engine must publish the signed uninstaller and a
; retained maintenance copy of the engine at the fixed paths named by
; UNINSTALL_EXE and MAINTENANCE_ENGINE_EXE below. Until it does, the HKCU
; "Installed Apps" entry is NOT published (never a dangling entry).

Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma
CRCCheck on

!ifndef VERSION
  !error "VERSION is required: /DVERSION=<x.y.z>"
!endif
!ifndef INPUT_DIR
  !error "INPUT_DIR is required: /DINPUT_DIR=<unsigned input tree>"
!endif
!ifndef OUTFILE
  !error "OUTFILE is required: /DOUTFILE=<setup.exe>"
!endif
!ifndef GENERATE_UNINSTALLER
  !ifndef UNINSTALLER_SIGNED
    !error "UNINSTALLER_SIGNED is required: the signed uninstaller built during packaging"
  !endif
!endif

!include MUI2.nsh
!include LogicLib.nsh
!include nsDialogs.nsh
!include FileFunc.nsh

!insertmacro GetParameters
!insertmacro GetOptions

Name "Veles Native Bridge"
OutFile "${OUTFILE}"
BrandingText "Veles Native Bridge ${VERSION}"

; Private staging only. The engine verifies this package (signature, descriptor,
; inventory) before trusting any byte; nothing here is a caller-selected path.
!define PACKAGE_DIR "$PLUGINSDIR\package"
!define ENGINE_EXE "${PACKAGE_DIR}\carrier.exe"

; The fixed per-user root is owned by the engine; these two paths are read-only
; references to its maintenance assets (Task 13 seam, see the header).
!define MAINTENANCE_ENGINE_EXE "$LOCALAPPDATA\Veles\NativeBridge\maintenance\veles-bridge-maintenance.exe"
!define UNINSTALL_EXE "$LOCALAPPDATA\Veles\NativeBridge\maintenance\uninstall.exe"

; HKCU only: discoverable in Installed Apps without any machine-scope write.
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VelesNativeBridge"

Var /GLOBAL Operation
Var /GLOBAL Dialog
Var /GLOBAL RadioInstall
Var /GLOBAL RadioRepair
Var /GLOBAL RadioRollback
Var /GLOBAL RadioUninstall
Var /GLOBAL Haystack
Var /GLOBAL Needle
Var /GLOBAL Found
Var /GLOBAL Cursor
Var /GLOBAL Window
Var /GLOBAL HaystackLength
Var /GLOBAL NeedleLength
Var /GLOBAL ExitCode
Var /GLOBAL EngineReport
Var /GLOBAL Guidance
Var /GLOBAL Succeeded

; A substring test without a plugin: $Found is "1" when $Needle occurs in $Haystack.
!macro DefineContains PREFIX
  Function ${PREFIX}Contains
    StrCpy $Found ""
    StrLen $HaystackLength $Haystack
    StrLen $NeedleLength $Needle
    StrCpy $Cursor 0
    ContainsLoop:
      IntOp $Window $Cursor + $NeedleLength
      IntCmp $Window $HaystackLength ContainsCheck ContainsCheck ContainsDone
    ContainsCheck:
      StrCpy $Window $Haystack $NeedleLength $Cursor
      StrCmp $Window $Needle ContainsFound
      IntOp $Cursor $Cursor + 1
      Goto ContainsLoop
    ContainsFound:
      StrCpy $Found "1"
    ContainsDone:
  FunctionEnd
!macroend
!insertmacro DefineContains ""
!insertmacro DefineContains "un."

; The report's failure code selects the guidance. The engine's stable vocabulary
; is closed; an unknown code gets the generic text. $EngineReport is bounded by
; the NSIS string size and only ever searched, never executed or displayed.
!macro DefineGuidance PREFIX
  Function ${PREFIX}SelectGuidance
    StrCpy $Guidance "The operation did not complete. Your previous Veles connector installation was not changed by this attempt where it could be kept. Try again; if it persists, download a fresh installer."
    StrCpy $Haystack $EngineReport

    StrCpy $Needle "cleanup-pending"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Some files could not be removed yet. Close Chrome and the Veles connector, then run this again. The product is not reported as removed."
      Return
    ${EndIf}
    StrCpy $Needle "host-active"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Veles is in use. Close all Veles connector tabs in Chrome, then retry."
      Return
    ${EndIf}
    StrCpy $Needle "unsupported-state"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "This installation was written by a newer Veles installer and was left untouched. Use the newer installer."
      Return
    ${EndIf}
    StrCpy $Needle "trust-unavailable"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Windows could not check the installer's signature right now. Connect to the network if needed, then retry."
      Return
    ${EndIf}
    StrCpy $Needle "trust-denied"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Windows did not accept the signature of this installer. It was not installed. Download a fresh copy."
      Return
    ${EndIf}
    StrCpy $Needle "recovery-required"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "A previous change was interrupted. Run this installer again to finish or restore it."
      Return
    ${EndIf}
    StrCpy $Needle "foreign-state"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Another program owns the Veles connector registration. It was not changed."
      Return
    ${EndIf}
    StrCpy $Needle "no-rollback"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "There is no earlier verified version to go back to. Use Repair or install again."
      Return
    ${EndIf}
    StrCpy $Needle "repair-package-required"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "Repair needs this exact version. Run the installer you downloaded for the installed version."
      Return
    ${EndIf}
  FunctionEnd
!macroend
!insertmacro DefineGuidance ""
!insertmacro DefineGuidance "un."

; Remove the one HKCU entry. Defined once and expanded for installer and uninstaller.
!macro DefineRemoveEntry PREFIX
  Function ${PREFIX}RemoveUninstallEntry
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
  FunctionEnd
!macroend
!insertmacro DefineRemoveEntry ""
!insertmacro DefineRemoveEntry "un."

!ifdef GENERATE_UNINSTALLER

; Packaging-time mode: emit the uninstaller beside this executable and quit.
Function .onInit
  WriteUninstaller "$EXEDIR\uninstall.exe"
  Quit
FunctionEnd

!else

!insertmacro MUI_PAGE_WELCOME
Page custom OperationPageCreate OperationPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  InitPluginsDir
  ; Authenticated product inputs only, extracted to private staging. The engine
  ; verifies every one of them; this script trusts none of them.
  SetOutPath "${PACKAGE_DIR}"
  File "/oname=carrier.exe" "${INPUT_DIR}\carrier.exe"
  File "${INPUT_DIR}\descriptor.json"
  File "${INPUT_DIR}\inventory.json"
  File /nonfatal "${INPUT_DIR}\sealed-inventory.json"
  SetOutPath "${PACKAGE_DIR}\payload"
  File /r "${INPUT_DIR}\payload\*.*"
  SetOutPath "${PACKAGE_DIR}\payload\maintenance"
  File "/oname=uninstall.exe" "${UNINSTALLER_SIGNED}"

  ; The operation comes from a closed list. Silent callers pass /OPERATION=<word>;
  ; anything outside the list is an invalid invocation, never a command.
  ${GetParameters} $0
  ${GetOptions} $0 "/OPERATION=" $Operation
  ${If} ${Errors}
    StrCpy $Operation "install"
  ${EndIf}
  StrCmp $Operation "inspect" OperationKnown
  StrCmp $Operation "install" OperationKnown
  StrCmp $Operation "update" OperationKnown
  StrCmp $Operation "repair" OperationKnown
  StrCmp $Operation "rollback" OperationKnown
  StrCmp $Operation "uninstall" OperationKnown
  SetErrorLevel 2
  Abort
  OperationKnown:
FunctionEnd

Function OperationPageCreate
  ; Silent runs take the operation from /OPERATION= and show no page.
  ${If} ${Silent}
    Abort
  ${EndIf}
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 24u "Choose what to do with the Veles Native Bridge for this Windows user."
  Pop $0
  ${NSD_CreateRadioButton} 0 30u 100% 12u "Install or update"
  Pop $RadioInstall
  ${NSD_CreateRadioButton} 0 46u 100% 12u "Repair"
  Pop $RadioRepair
  ${NSD_CreateRadioButton} 0 62u 100% 12u "Roll back to the previous version"
  Pop $RadioRollback
  ${NSD_CreateRadioButton} 0 78u 100% 12u "Uninstall"
  Pop $RadioUninstall
  ${NSD_Check} $RadioInstall
  nsDialogs::Show
FunctionEnd

Function OperationPageLeave
  StrCpy $Operation "install"
  ${NSD_GetState} $RadioRepair $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Operation "repair"
  ${EndIf}
  ${NSD_GetState} $RadioRollback $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Operation "rollback"
  ${EndIf}
  ${NSD_GetState} $RadioUninstall $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Operation "uninstall"
  ${EndIf}
FunctionEnd

; Surface the engine's result. Exit 0 is a complete operation, 1 an operation
; failure (cleanup-pending and unsupported-state included), 2 an invalid
; invocation of the engine. Anything else means the engine did not run. A
; cleanup-pending report is never shown as done, whatever the exit code says.
Function HandleEngineResult
  Pop $ExitCode
  Pop $EngineReport
  StrCpy $Succeeded ""
  StrCmp $ExitCode "0" EngineCompleted
  StrCmp $ExitCode "1" EngineFailed
  StrCmp $ExitCode "2" EngineInvalid
  StrCpy $Guidance "The installer engine could not be started. Download a fresh installer."
  SetErrorLevel 3
  Goto ReportOutcome
  EngineInvalid:
    StrCpy $Guidance "The installer engine refused an internal request. Download a fresh installer."
    SetErrorLevel 2
    Goto ReportOutcome
  EngineFailed:
    Call SelectGuidance
    SetErrorLevel 1
    Goto ReportOutcome
  EngineCompleted:
    StrCpy $Haystack $EngineReport
    StrCpy $Needle "cleanup-pending"
    Call Contains
    ${If} $Found == "1"
      Call SelectGuidance
      SetErrorLevel 1
      Goto ReportOutcome
    ${EndIf}
    StrCpy $Succeeded "1"
    SetErrorLevel 0
    StrCpy $Guidance "Done. Open the Veles connector in Chrome and retry; Bluetooth permission is still yours to grant."
  ReportOutcome:
    DetailPrint "$Guidance"
    MessageBox MB_OK|MB_ICONINFORMATION "$Guidance" /SD IDOK
FunctionEnd

; Publish the Installed Apps entry only after a COMPLETE engine result, and only
; when the signed maintenance entry really exists under the fixed root.
Function PublishUninstallEntry
  IfFileExists "${UNINSTALL_EXE}" 0 EntryUnavailable
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Veles Native Bridge"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Veles"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"${UNINSTALL_EXE}"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"${UNINSTALL_EXE}" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  Return
  EntryUnavailable:
    DetailPrint "The signed maintenance entry is not available yet; Installed Apps is not updated."
FunctionEnd

Section "Apply"
  StrCmp $Operation "inspect" RunInspect
  StrCmp $Operation "install" RunInstall
  StrCmp $Operation "update" RunInstall
  StrCmp $Operation "repair" RunRepair
  StrCmp $Operation "rollback" RunRollback
  StrCmp $Operation "uninstall" RunUninstall
  SetErrorLevel 2
  Abort

  RunInspect:
    nsExec::ExecToStack '"${ENGINE_EXE}" inspect'
    Call HandleEngineResult
    Return

  RunInstall:
    nsExec::ExecToStack '"${ENGINE_EXE}" install --package "${PACKAGE_DIR}"'
    Call HandleEngineResult
    Goto AfterMutation

  RunRepair:
    nsExec::ExecToStack '"${ENGINE_EXE}" repair --package "${PACKAGE_DIR}"'
    Call HandleEngineResult
    Goto AfterMutation

  RunRollback:
    nsExec::ExecToStack '"${ENGINE_EXE}" rollback'
    Call HandleEngineResult
    Goto AfterMutation

  RunUninstall:
    nsExec::ExecToStack '"${ENGINE_EXE}" uninstall'
    Call HandleEngineResult
    ${If} $Succeeded == "1"
      Call RemoveUninstallEntry
    ${EndIf}
    Return

  AfterMutation:
    ${If} $Succeeded == "1"
      Call PublishUninstallEntry
    ${EndIf}
SectionEnd

!endif

; ---------------------------------------------------------------------------
; The uninstaller. NSIS cannot carry files in an uninstaller, so it runs the
; retained maintenance engine under the fixed per-user root. It is the signed
; uninstaller embedded by packaging; nothing here is generated at runtime.
; ---------------------------------------------------------------------------

Function un.onInit
  InitPluginsDir
FunctionEnd

Function un.HandleEngineResult
  Pop $ExitCode
  Pop $EngineReport
  StrCpy $Succeeded ""
  StrCmp $ExitCode "0" UnEngineCompleted
  StrCmp $ExitCode "1" UnEngineFailed
  StrCmp $ExitCode "2" UnEngineInvalid
  StrCpy $Guidance "The maintenance engine could not be started. Run the installer again and choose Uninstall."
  SetErrorLevel 3
  Goto UnReportOutcome
  UnEngineInvalid:
    StrCpy $Guidance "The maintenance engine refused an internal request. Run the installer again and choose Uninstall."
    SetErrorLevel 2
    Goto UnReportOutcome
  UnEngineFailed:
    Call un.SelectGuidance
    SetErrorLevel 1
    Goto UnReportOutcome
  UnEngineCompleted:
    StrCpy $Haystack $EngineReport
    StrCpy $Needle "cleanup-pending"
    Call un.Contains
    ${If} $Found == "1"
      Call un.SelectGuidance
      SetErrorLevel 1
      Goto UnReportOutcome
    ${EndIf}
    StrCpy $Succeeded "1"
    SetErrorLevel 0
    StrCpy $Guidance "Veles Native Bridge was removed for this Windows user."
  UnReportOutcome:
    DetailPrint "$Guidance"
    MessageBox MB_OK|MB_ICONINFORMATION "$Guidance" /SD IDOK
FunctionEnd

Section "Uninstall"
  IfFileExists "${MAINTENANCE_ENGINE_EXE}" 0 EngineMissing
  nsExec::ExecToStack '"${MAINTENANCE_ENGINE_EXE}" uninstall'
  Call un.HandleEngineResult
  ${If} $Succeeded == "1"
    Call un.RemoveUninstallEntry
  ${EndIf}
  Return
  EngineMissing:
    StrCpy $Guidance "The maintenance engine is missing. Run the installer again and choose Uninstall."
    SetErrorLevel 3
    DetailPrint "$Guidance"
    MessageBox MB_OK|MB_ICONEXCLAMATION "$Guidance" /SD IDOK
SectionEnd
