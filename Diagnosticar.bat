@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === DIAGNOSTICO Punto de Venta ===
echo Carpeta: %CD%
echo.
echo main.py existe?
if exist main.py (echo   SI) else (echo   NO - coloca este archivo junto a main.py)
echo.
echo Buscando Python...
where py 2>nul
where python 2>nul
echo.
py -3 -c "import sys; print('py OK', sys.version)" 2>nul
python -c "import sys; print('python OK', sys.version)" 2>nul
echo.
echo Probando librerias...
py -3 -c "import customtkinter; print('customtkinter OK')" 2>nul
py -3 -c "import PIL; print('Pillow OK')" 2>nul
python -c "import customtkinter; print('customtkinter OK')" 2>nul
python -c "import PIL; print('Pillow OK')" 2>nul
echo.
echo Si algo fallo, ejecuta:
echo   pip install customtkinter Pillow
echo.
pause
