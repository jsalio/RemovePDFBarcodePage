@echo off
REM Compila RemovePDFBarcodePage.exe. Ejecutar en Windows con Python 3.9+ instalado.
py -m venv .venv || goto :error
call .venv\Scripts\activate.bat || goto :error
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller --onefile --console --name RemovePDFBarcodePage --hidden-import fitz main.py || goto :error
echo.
echo Listo: dist\RemovePDFBarcodePage.exe
echo Recuerda colocar el archivo .env junto al ejecutable antes de usarlo.
goto :eof

:error
echo La compilacion fallo. Revisa el mensaje anterior.
exit /b 1
