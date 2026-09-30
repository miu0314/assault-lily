' 双击启动（不弹命令行窗口）。
' 先找 Codex 内置运行时的 pythonw（本机路径），找不到就用 PATH 里的 pythonw。
Option Explicit

Dim fso, shell, base, exe, candidates, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = base

candidates = Array( _
    shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe", _
    "pythonw.exe", _
    "python.exe")

exe = ""
For i = 0 To UBound(candidates)
    If InStr(candidates(i), "\") = 0 Then
        exe = candidates(i)
        Exit For
    ElseIf fso.FileExists(candidates(i)) Then
        exe = candidates(i)
        Exit For
    End If
Next

If exe = "" Then exe = "pythonw.exe"

shell.Run """" & exe & """ """ & base & "\launcher.py""", 0, False
