@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Instalando librerias en Python 3.12 si existe, si no en el default...
set "PYCMD="
where py >nul 2>&1 && (
  py -3.12 -c "import sys" 2>nul && set "PYCMD=py -3.12"
)
if not defined PYCMD where py >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
  echo No hay Python.
  pause
  exit /b 1
)
echo Usando: %PYCMD%
%PYCMD% -c "import sys; print(sys.executable); print(sys.version)"
%PYCMD% -m pip install --upgrade "customtkinter>=5.2.0,<6.0" "Pillow>=10.0"
%PYCMD% -c "import customtkinter,PIL,tkinter; print('TODO OK')"
pause
