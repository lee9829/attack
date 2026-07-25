' Octo Automation - double-click launcher (ASCII only)
Option Explicit

Dim fso, sh, dir, pyw, py, cmd, marker, useHidden, which, reg, lines

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

pyw = ""
py = ""

On Error Resume Next
pyw = sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python313\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = ""
If pyw = "" Then
  pyw = sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python312\pythonw.exe"
  If Not fso.FileExists(pyw) Then pyw = ""
End If
If pyw = "" Then
  pyw = sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python311\pythonw.exe"
  If Not fso.FileExists(pyw) Then pyw = ""
End If
On Error GoTo 0

If pyw = "" Then
  On Error Resume Next
  reg = sh.RegRead("HKCU\Software\Python\PythonCore\3.13\InstallPath\")
  If Err.Number <> 0 Then
    Err.Clear
    reg = sh.RegRead("HKCU\Software\Python\PythonCore\3.12\InstallPath\")
  End If
  If Err.Number <> 0 Then
    Err.Clear
    reg = sh.RegRead("HKCU\Software\Python\PythonCore\3.11\InstallPath\")
  End If
  If Err.Number = 0 And reg <> "" Then
    If fso.FileExists(reg & "pythonw.exe") Then pyw = reg & "pythonw.exe"
    If fso.FileExists(reg & "python.exe") Then py = reg & "python.exe"
  End If
  Err.Clear
  On Error GoTo 0
End If

If pyw = "" Or py = "" Then
  On Error Resume Next
  which = sh.Exec("cmd /c where pythonw 2>nul").StdOut.ReadAll
  If which <> "" Then
    lines = Split(which, vbCrLf)
    If UBound(lines) >= 0 Then
      If Trim(lines(0)) <> "" And fso.FileExists(Trim(lines(0))) Then
        If pyw = "" Then pyw = Trim(lines(0))
      End If
    End If
  End If
  which = sh.Exec("cmd /c where python 2>nul").StdOut.ReadAll
  If which <> "" Then
    lines = Split(which, vbCrLf)
    If UBound(lines) >= 0 Then
      If Trim(lines(0)) <> "" And fso.FileExists(Trim(lines(0))) Then
        If py = "" Then py = Trim(lines(0))
      End If
    End If
  End If
  Err.Clear
  On Error GoTo 0
End If

If pyw = "" And py = "" Then
  MsgBox "Python not found." & vbCrLf & vbCrLf & _
    "1) Install Python 3.10+ from python.org" & vbCrLf & _
    "2) Check 'Add python.exe to PATH'" & vbCrLf & _
    "3) Double-click start.vbs again", _
    vbCritical, "Octo Automation"
  WScript.Quit 1
End If

marker = dir & "\.deps_ok"
useHidden = fso.FileExists(marker)

If useHidden And pyw <> "" Then
  cmd = """" & pyw & """ main.py"
  sh.Run cmd, 0, False
Else
  If py <> "" Then
    cmd = "cmd /c """ & py & """ main.py & if errorlevel 1 pause"""
  Else
    cmd = "cmd /c """ & pyw & """ main.py & if errorlevel 1 pause"""
  End If
  sh.Run cmd, 1, False
End If
