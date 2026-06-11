' Launches its arguments as a command line with zero visible window.
'
' "powershell -WindowStyle Hidden" still flashes a console window briefly --
' conhost.exe creates the window before PowerShell processes -WindowStyle.
' WshShell.Run(cmd, 0, False) hides the window at creation time instead, so
' nothing ever flashes. Used by NanobotWatchdog and restart_helper.ps1.
'
' Usage: wscript.exe //B //Nologo run_hidden.vbs <program> [args...]

Set objShell = CreateObject("WScript.Shell")

cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    arg = WScript.Arguments(i)
    cmd = cmd & " """ & Replace(arg, """", """""") & """"
Next

objShell.Run Trim(cmd), 0, False
