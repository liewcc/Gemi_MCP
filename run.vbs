Option Explicit

Dim shell, fso, batPath, localAppData, wtPath, runCmd, fallbackCmd
Dim wtLaunched, wtErrNum, wtErrDesc, fallbackErrNum, fallbackErrDesc

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get the absolute path to run.bat (located in the same folder as the VBScript)
batPath = Replace(WScript.ScriptFullName, ".vbs", ".bat")

' Locate wt.exe in the user's Local AppData directory
localAppData = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
wtPath = localAppData & "\Microsoft\WindowsApps\wt.exe"

wtLaunched = False

' 1. Try launching with Windows Terminal if wt.exe exists
If fso.FileExists(wtPath) Then
    ' Quoting wtPath and batPath to support paths containing spaces
    runCmd = """" & wtPath & """ --size 110,40 new-tab cmd /c """ & batPath & """ --wt-launched"
    
    On Error Resume Next
    shell.Run runCmd, 0, False
    If Err.Number = 0 Then
        wtLaunched = True
    Else
        wtErrNum = Err.Number
        wtErrDesc = Err.Description
    End If
    On Error GoTo 0
End If

' 2. Fallback to launching in standard cmd.exe if Windows Terminal is not found or failed to start
If Not wtLaunched Then
    ' Quoting batPath to support paths containing spaces
    fallbackCmd = "cmd /c """ & batPath & """"
    
    On Error Resume Next
    shell.Run fallbackCmd, 1, False
    If Err.Number <> 0 Then
        fallbackErrNum = Err.Number
        fallbackErrDesc = Err.Description
        
        ' Display an error message if both launch methods fail
        Dim msg
        msg = "Failed to launch GEMI MCP." & vbCrLf & vbCrLf
        If fso.FileExists(wtPath) Then
            msg = msg & "1. Windows Terminal launch failed: " & wtErrDesc & " (0x" & Hex(wtErrNum) & ")" & vbCrLf
        Else
            msg = msg & "1. Windows Terminal not found at: " & wtPath & vbCrLf
        End If
        msg = msg & "2. Fallback Command Prompt launch failed: " & fallbackErrDesc & " (0x" & Hex(fallbackErrNum) & ")"
        
        MsgBox msg, vbCritical, "GEMI MCP Launcher Error"
    End If
    On Error GoTo 0
End If
