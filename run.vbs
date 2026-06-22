On Error Resume Next
Set shell = CreateObject("WScript.Shell")
batPath = Replace(WScript.ScriptFullName, ".vbs", ".bat")

shell.Run "wt --size 110,40 new-tab cmd /c """ & batPath & """ --wt-launched", 0, False

If Err.Number <> 0 Then
    shell.Run "cmd /c """ & batPath & """", 1, False
End If
On Error GoTo 0
