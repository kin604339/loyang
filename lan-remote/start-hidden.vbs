' 在背景啟動 LAN Remote（不顯示黑色命令列視窗，只會有狀態視窗）
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run """" & dir & "\venv\Scripts\pythonw.exe"" -m lanremote", 0, False
