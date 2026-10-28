Set WshShell = CreateObject("WScript.Shell")
Dim fso, folder
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = folder

' Mata servidor e watchdog antigos
WshShell.Run "cmd /c taskkill /f /im python.exe >nul 2>&1", 0, True
WScript.Sleep 1000

' Inicia o WATCHDOG (que sobe o servidor e fica de olho)
WshShell.Run "cmd /c python watchdog.py", 0, False
