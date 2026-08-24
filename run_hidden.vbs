' Sa-Ra-L — start the dashboard with NO visible console window.
' Uses pythonw (windowless Python) so nothing shows on screen; the server runs in
' the background and the browser opens to it. Requires Python installed with the
' project deps. For a self-contained build with no Python needed, see build_exe.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
' 0 = hidden window, False = don't wait
sh.Run "pythonw launcher.py", 0, False
