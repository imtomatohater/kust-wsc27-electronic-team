@echo off
REM Build WS22_VCU.exe (one file) and, if Inno Setup 6 is installed, WS22_VCU_Setup.exe.
REM Run from anywhere:  vcu\packaging\build_windows.bat      Output: vcu\dist\
setlocal
cd /d "%~dp0.."
python -m pip install -r requirements.txt pyinstaller || goto :err
python -m unittest discover -s tests || goto :err
python -m PyInstaller --noconfirm --clean --onefile --windowed --name WS22_VCU ^
  --add-data "dbc;dbc" --add-data "config.json;." --add-data "config_bench24v.json;." ^
  --collect-submodules can --hidden-import can.interfaces.pcan vcu_app.py || goto :err
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% packaging\installer.iss || goto :err
) else (
  echo Inno Setup 6 not found - skipped installer. dist\WS22_VCU.exe is ready.
)
echo Done: %cd%\dist
exit /b 0
:err
echo BUILD FAILED
exit /b 1
