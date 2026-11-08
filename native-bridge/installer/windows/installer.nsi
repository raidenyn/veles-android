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
; STATUS: compiled on Windows with the pinned NSIS 3.08 on 2026-11-08; the
; uninstall staging below was exercised with a stub engine that records its own
; path and deletes the installed maintenance images. A full install/uninstall
; run still needs a signed package (Task 13). The contract is pinned by
; scripts/test_windows_package.py.
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
SetCompressor zlib
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
; The uninstaller runs a copy of the maintenance engine here, outside the
; removable root, so the installed image is not a running mapped file.
!define STAGED_ENGINE_EXE "$PLUGINSDIR\veles-bridge-maintenance.exe"

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
Var /GLOBAL Field
Var /GLOBAL FieldKey
Var /GLOBAL FieldEnd
Var /GLOBAL ReportOperation
Var /GLOBAL ReportState
Var /GLOBAL ReportFailure
Var /GLOBAL ReportWarnings
Var /GLOBAL ExpectOperation
Var /GLOBAL ExpectOperationAlt
Var /GLOBAL ExpectState
Var /GLOBAL ExpectStateAlt

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

; Read one value out of $EngineReport, the engine's canonical one-line JSON (sorted keys, no spaces).
; In: $FieldKey = the text that precedes the value (for example "state":"), $FieldEnd = the character
; that ends it. Out: $Field = the value, or "" when the key is absent, null or unterminated. The report
; is only ever read, never executed or displayed.
!macro DefineReadField PREFIX
  Function ${PREFIX}ReadField
    StrCpy $Field ""
    StrCpy $Haystack $EngineReport
    StrCpy $Needle $FieldKey
    Call ${PREFIX}Contains
    ${If} $Found != "1"
      Return
    ${EndIf}
    IntOp $Cursor $Cursor + $NeedleLength
    ReadLoop:
      StrCpy $Window $EngineReport 1 $Cursor
      StrCmp $Window "" ReadUnterminated
      StrCmpS $Window $FieldEnd ReadDone
      StrCpy $Field "$Field$Window"
      IntOp $Cursor $Cursor + 1
      Goto ReadLoop
    ReadUnterminated:
      StrCpy $Field ""
    ReadDone:
  FunctionEnd
!macroend
!insertmacro DefineReadField ""
!insertmacro DefineReadField "un."

; The structured meaning of the report: operation, state, failure code and warning list.
; An unreadable report leaves operation and state empty, which is never a success.
!macro DefineParseReport PREFIX
  Function ${PREFIX}ParseReport
    StrCpy $FieldEnd '"'
    StrCpy $FieldKey '"operation":"'
    Call ${PREFIX}ReadField
    StrCpy $ReportOperation $Field
    StrCpy $FieldKey '"state":"'
    Call ${PREFIX}ReadField
    StrCpy $ReportState $Field
    StrCpy $FieldKey '"failure":"'
    Call ${PREFIX}ReadField
    StrCpy $ReportFailure $Field
    StrCpy $FieldEnd ']'
    StrCpy $FieldKey '"warnings":['
    Call ${PREFIX}ReadField
    StrCpy $ReportWarnings $Field
    ; A pending cleanup is a failure whether the engine reported it as the failure or as a warning.
    StrCpy $Haystack $ReportWarnings
    StrCpy $Needle "cleanup-pending"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $ReportFailure "cleanup-pending"
    ${EndIf}
  FunctionEnd
!macroend
!insertmacro DefineParseReport ""
!insertmacro DefineParseReport "un."

; The failure code (a closed vocabulary) selects the guidance; an unknown code gets the generic text.
!macro DefineGuidance PREFIX
  Function ${PREFIX}SelectGuidance
    StrCpy $Guidance "The operation did not complete. Your previous Veles connector installation was not changed by this attempt where it could be kept. Try again; if it persists, download a fresh installer."
    StrCmpS $ReportFailure "cleanup-pending" GuidancePending
    StrCmpS $ReportFailure "host-active" GuidanceHostActive
    StrCmpS $ReportFailure "unsupported-state" GuidanceUnsupported
    StrCmpS $ReportFailure "trust-unavailable" GuidanceTrustUnavailable
    StrCmpS $ReportFailure "trust-denied" GuidanceTrustDenied
    StrCmpS $ReportFailure "recovery-required" GuidanceRecovery
    StrCmpS $ReportFailure "foreign-state" GuidanceForeign
    StrCmpS $ReportFailure "no-rollback" GuidanceNoRollback
    StrCmpS $ReportFailure "repair-package-required" GuidanceRepairPackage
    Return
    GuidancePending:
      StrCpy $Guidance "Some files could not be removed yet. Close Chrome and the Veles connector, then run this again. The product is not reported as removed."
      Return
    GuidanceHostActive:
      StrCpy $Guidance "Veles is in use. Close all Veles connector tabs in Chrome, then retry."
      Return
    GuidanceUnsupported:
      StrCpy $Guidance "This installation was written by a newer Veles installer and was left untouched. Use the newer installer."
      Return
    GuidanceTrustUnavailable:
      StrCpy $Guidance "Windows could not check the installer's signature right now. Connect to the network if needed, then retry."
      Return
    GuidanceTrustDenied:
      StrCpy $Guidance "Windows did not accept the signature of this installer. It was not installed. Download a fresh copy."
      Return
    GuidanceRecovery:
      StrCpy $Guidance "A previous change was interrupted. Run this installer again to finish or restore it."
      Return
    GuidanceForeign:
      StrCpy $Guidance "Another program owns the Veles connector registration. It was not changed."
      Return
    GuidanceNoRollback:
      StrCpy $Guidance "There is no earlier verified version to go back to. Use Repair or install again."
      Return
    GuidanceRepairPackage:
      StrCpy $Guidance "Repair needs this exact version. Run the installer you downloaded for the installed version."
  FunctionEnd
!macroend
!insertmacro DefineGuidance ""
!insertmacro DefineGuidance "un."

; Warnings of a COMPLETE operation are appended to its message, never dropped: preserved files the product
; does not own, and a host/extension API window that does not overlap.
!macro DefineNotes PREFIX
  Function ${PREFIX}AppendNotes
    StrCpy $Haystack $ReportWarnings
    StrCpy $Needle "compatibility-window"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "$Guidance This version may not be compatible with your installed Veles extension; update the extension, then retry the connector."
    ${EndIf}
    StrCpy $Needle "leftover-files"
    Call ${PREFIX}Contains
    ${If} $Found == "1"
      StrCpy $Guidance "$Guidance Some files that Veles does not own were left in its folder and were not deleted."
    ${EndIf}
  FunctionEnd
!macroend
!insertmacro DefineNotes ""
!insertmacro DefineNotes "un."

; What an inspection found. Inspect exits 0 for every report it produces - the state IS the answer - so none
; of these messages is a completed installation and none says "Done".
!macro DefineInspectGuidance PREFIX
  Function ${PREFIX}InspectGuidance
    StrCpy $Succeeded ""
    StrCmpS $ReportState "absent" InspectAbsent
    StrCmpS $ReportState "installed" InspectInstalled
    StrCmpS $ReportState "damaged" InspectDamaged
    StrCmpS $ReportState "foreign" InspectForeign
    StrCmpS $ReportState "recovery-required" InspectRecovery
    ; An unknown state is not understood, so it is not reported as anything.
    StrCpy $Guidance "The installation state could not be understood. Download a fresh installer."
    Push "1"
    Return
    InspectAbsent:
      StrCpy $Guidance "Veles Native Bridge is not installed for this Windows user."
      Push "0"
      Return
    InspectInstalled:
      StrCpy $Guidance "Veles Native Bridge is installed and complete."
      Push "0"
      Return
    InspectDamaged:
      StrCpy $Guidance "Veles Native Bridge was found but is damaged. Run this installer and choose Repair."
      Push "0"
      Return
    InspectForeign:
      StrCpy $Guidance "Another program owns the Veles connector registration. It was not changed."
      Push "0"
      Return
    InspectRecovery:
      StrCpy $Guidance "A previous change was interrupted. Run this installer again to finish or restore it."
      Push "0"
  FunctionEnd
!macroend
!insertmacro DefineInspectGuidance ""
!insertmacro DefineInspectGuidance "un."

; The operation and settled state a COMPLETE mutation must report. Install and update may report the repair
; that a same-version rerun performs.
!macro DefineExpectations PREFIX
  Function ${PREFIX}SetExpectations
    StrCpy $ExpectOperation ""
    StrCpy $ExpectOperationAlt ""
    StrCpy $ExpectState ""
    StrCpy $ExpectStateAlt ""
    StrCmpS $Operation "install" ExpectInstall
    StrCmpS $Operation "update" ExpectInstall
    StrCmpS $Operation "repair" ExpectRepair
    StrCmpS $Operation "rollback" ExpectRollback
    StrCmpS $Operation "uninstall" ExpectUninstall
    Return
    ExpectInstall:
      StrCpy $ExpectOperation "install"
      StrCpy $ExpectOperationAlt "repair"
      StrCpy $ExpectState "installed"
      StrCpy $ExpectStateAlt "repaired"
      Return
    ExpectRepair:
      StrCpy $ExpectOperation "repair"
      StrCpy $ExpectOperationAlt "repair"
      StrCpy $ExpectState "repaired"
      StrCpy $ExpectStateAlt "repaired"
      Return
    ExpectRollback:
      StrCpy $ExpectOperation "rollback"
      StrCpy $ExpectOperationAlt "rollback"
      StrCpy $ExpectState "rolled-back"
      StrCpy $ExpectStateAlt "rolled-back"
      Return
    ExpectUninstall:
      StrCpy $ExpectOperation "uninstall"
      StrCpy $ExpectOperationAlt "uninstall"
      StrCpy $ExpectState "uninstalled"
      StrCpy $ExpectStateAlt "uninstalled"
  FunctionEnd
!macroend
!insertmacro DefineExpectations ""
!insertmacro DefineExpectations "un."

; Surface the engine's result by its STRUCTURED meaning, not by the exit code alone. Exit 0 is a complete
; operation and exit 1 an operation failure (cleanup-pending and unsupported-state included); inspect exits 0
; for any report because the state is the answer. A report that is unreadable, names another operation, an
; unsettled state, a failure or a pending cleanup is never shown as done.
!macro DefineHandleResult PREFIX ENGINE_DOWN ENGINE_INVALID
  Function ${PREFIX}HandleEngineResult
    Pop $ExitCode
    Pop $EngineReport
    StrCpy $Succeeded ""
    StrCmp $ExitCode "0" Completed
    StrCmp $ExitCode "1" Failed
    StrCmp $ExitCode "2" Invalid
    StrCpy $Guidance "${ENGINE_DOWN}"
    SetErrorLevel 3
    Goto ReportOutcome
    Invalid:
      StrCpy $Guidance "${ENGINE_INVALID}"
      SetErrorLevel 2
      Goto ReportOutcome
    Failed:
      Call ${PREFIX}ParseReport
      Call ${PREFIX}SelectGuidance
      SetErrorLevel 1
      Goto ReportOutcome
    Unreadable:
      StrCpy $Guidance "The installer engine returned a result that could not be understood, so nothing is reported as done. Download a fresh installer."
      SetErrorLevel 1
      Goto ReportOutcome
    Completed:
      Call ${PREFIX}ParseReport
      StrCmpS $ReportOperation "" Unreadable
      StrCmpS $ReportState "" Unreadable
      ; A pending cleanup is a failure whatever the exit code says.
      StrCmpS $ReportFailure "cleanup-pending" Failed
      StrCmpS $Operation "inspect" Inspected
      ; Any other failure with exit 0 contradicts the exit status; it is never a success.
      StrCmpS $ReportFailure "" 0 Failed
      Call ${PREFIX}SetExpectations
      StrCmpS $ExpectOperation "" Unreadable
      StrCmpS $ReportOperation $ExpectOperation OperationMatches
      StrCmpS $ReportOperation $ExpectOperationAlt OperationMatches
      Goto Unreadable
      OperationMatches:
        StrCmpS $ReportState $ExpectState Settled
        StrCmpS $ReportState $ExpectStateAlt Settled
        Goto Unreadable
      Settled:
        StrCpy $Succeeded "1"
        SetErrorLevel 0
        StrCpy $Guidance "Done. Open the Veles connector in Chrome and retry; Bluetooth permission is still yours to grant."
        StrCmpS $Operation "uninstall" Removed
        Goto AddNotes
      Removed:
        StrCpy $Guidance "Veles Native Bridge was removed for this Windows user."
      AddNotes:
        Call ${PREFIX}AppendNotes
        Goto ReportOutcome
      Inspected:
        StrCmpS $ReportOperation "inspect" InspectReadable
        Goto Unreadable
      InspectReadable:
        Call ${PREFIX}InspectGuidance
        Pop $ExitCode
        StrCmp $ExitCode "0" InspectKnown
        SetErrorLevel 1
        Goto ReportOutcome
      InspectKnown:
        SetErrorLevel 0
        Call ${PREFIX}AppendNotes
    ReportOutcome:
      DetailPrint "$Guidance"
      MessageBox MB_OK|MB_ICONINFORMATION "$Guidance" /SD IDOK
  FunctionEnd
!macroend

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
; A section is still required: an NSIS script with only .onInit is rejected
; ("no sections specified"). It never executes because .onInit quits first.
Section "Generate uninstaller"
SectionEnd

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

!insertmacro DefineHandleResult "" "The installer engine could not be started. Download a fresh installer." "The installer engine refused an internal request. Download a fresh installer."

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
; The uninstaller. NSIS relaunches an uninstaller from %TEMP%, so the installed
; uninstall.exe is not the running image. The engine is likewise started from a
; staged copy under PLUGINSDIR (never the installed maintenance path), so the
; mapped images inside the removable root are deletable in one pass. It is the
; signed uninstaller embedded by packaging; nothing here is generated at
; runtime.
; ---------------------------------------------------------------------------

Function un.onInit
  InitPluginsDir
FunctionEnd

!insertmacro DefineHandleResult "un." "The maintenance engine could not be started. Run the installer again and choose Uninstall." "The maintenance engine refused an internal request. Run the installer again and choose Uninstall."

Section "Uninstall"
  StrCpy $Operation "uninstall"
  IfFileExists "${MAINTENANCE_ENGINE_EXE}" 0 EngineMissing
  ; Windows cannot delete a mapped image while it runs. NSIS already relaunched
  ; this uninstaller from %TEMP%, so the installed uninstaller is not running;
  ; the engine is executed from a staged copy under PLUGINSDIR as well. Both
  ; installed maintenance images are then ordinary files and one pass removes
  ; them, instead of ending cleanup-pending until a second run from the
  ; installer (whose engine is outside the root).
  CopyFiles /SILENT "${MAINTENANCE_ENGINE_EXE}" "${STAGED_ENGINE_EXE}"
  IfFileExists "${STAGED_ENGINE_EXE}" 0 EngineMissing
  nsExec::ExecToStack '"${STAGED_ENGINE_EXE}" uninstall'
  Call un.HandleEngineResult
  Delete "${STAGED_ENGINE_EXE}"
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
