@echo off
chcp 65001 >nul
title Punto de Venta - SportElite
cd /d "%~dp0"

echo ============================================
echo   Punto de Venta - SportElite
echo   Carpeta: %CD%
echo ============================================
echo.

if not exist "main.py" (
    echo [ERROR] No se encuentra main.py
    pause
    exit /b 1
)

set "PYTHON_EXE="

REM Preferir Python 3.12 / 3.11 (estables con customtkinter)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Python\pythoncore-3.12-64\python.exe" set "PYTHON_EXE=%LocalAppData%\Python\pythoncore-3.12-64\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Python\pythoncore-3.11-64\python.exe" set "PYTHON_EXE=%LocalAppData%\Python\pythoncore-3.11-64\python.exe"

REM py -3.12 / -3.11
if not defined PYTHON_EXE (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 (
    for /f "delims=" %%i in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
  )
)
if not defined PYTHON_EXE (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 (
    for /f "delims=" %%i in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
  )
)

REM Cualquier python (incluye 3.14 como ultimo recurso)
if not defined PYTHON_EXE (
  where py >nul 2>&1
  if %ERRORLEVEL%==0 (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"
  )
)
if not defined PYTHON_EXE (
  where python >nul 2>&1
  if %ERRORLEVEL%==0 for /f "delims=" %%i in ('where python') do if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)
if not defined PYTHON_EXE if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" set "PYTHON_EXE=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"

if not defined PYTHON_EXE (
    echo [ERROR] No se encontro Python.
    pause
    exit /b 1
)

echo Usando Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import sys; print('Version:', sys.version)"
echo.

"%PYTHON_EXE%" -c "import sys; v=sys.version_info; raise SystemExit(0 if v.major==3 and v.minor<=12 else 1)"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ************************************************************
    echo  AVISO: Estas usando Python 3.13 o superior.
    echo  El codigo -1073741819 suele ser un CIERRE por incompatibilidad.
    echo.
    echo  Solucion recomendada:
    echo   1. Instala Python 3.12:
    echo      https://www.python.org/downloads/release/python-31210/
    echo   2. Marca "Add python.exe to PATH"
    echo   3. Ejecuta INSTALAR_LIBRERIAS.bat
    echo   4. Vuelve a abrir este archivo
    echo ************************************************************
    echo.
    echo Intentando abrir de todos modos...
    echo.
)

"%PYTHON_EXE%" -c "import tkinter" 1>nul 2>error_pos.log
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Falta tkinter.
    type error_pos.log
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import customtkinter,PIL" 1>nul 2>error_pos.log
if %ERRORLEVEL% NEQ 0 (
    echo Instalando librerias...
    "%PYTHON_EXE%" -m pip install --upgrade "customtkinter>=5.2.0,<6.0" "Pillow>=10.0"
)

"%PYTHON_EXE%" -c "import customtkinter,PIL; print('Librerias OK')" 2>error_pos.log
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Librerias no cargan.
    type error_pos.log
    pause
    exit /b 1
)

echo Iniciando aplicacion...
echo.
"%PYTHON_EXE%" main.py
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
    echo.
    echo [ERROR] Codigo de salida: %ERR%
    if %ERR%==-1073741819 (
        echo Esto es un cierre de Windows ^(ACCESS_VIOLATION^).
        echo Casi siempre se resuelve instalando Python 3.12 y:
        echo   py -3.12 -m pip install "customtkinter>=5.2.0,^<6.0" Pillow
    )
    if exist error_inicio.log (
        echo.
        echo --- error_inicio.log ---
        type error_inicio.log
    )
    echo.
    pause
)
