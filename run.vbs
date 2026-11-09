Option Explicit

Dim fso, shell, carpeta, archivo, comando, resultado
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
archivo = fso.BuildPath(carpeta, "run.bat")
shell.CurrentDirectory = carpeta
comando = Chr(34) & shell.ExpandEnvironmentStrings("%ComSpec%") & Chr(34) _
    & " /d /c " & Chr(34) & archivo & Chr(34)
resultado = shell.Run(comando, 0, True)

If resultado <> 0 Then
    MsgBox "No se pudo iniciar Jarvis. Verifica que exista el entorno virtual y que las dependencias esten instaladas." _
        & vbCrLf & vbCrLf & "Codigo de error: " & resultado, _
        vbExclamation, "Jarvis"
End If
