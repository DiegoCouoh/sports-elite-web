Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
' Ejecuta el .bat en la misma carpeta y MUESTRA la ventana
' (asi si hay error no se cierra sin que lo veas)
sh.CurrentDirectory = dir
sh.Run "cmd /c """ & dir & "\Abrir_Punto_de_Venta.bat""", 1, False
