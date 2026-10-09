@echo off
setlocal
cd /d "%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo Visual Studio C++ build tools are required.
  exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
if not defined VSINSTALL exit /b 1
call "%VSINSTALL%\VC\Auxiliary\Build\vcvars32.bat"
if errorlevel 1 exit /b 1
if not exist build mkdir build
cd build
cl /nologo /LD /MT /O2 /EHsc /std:c++17 /I"..\..\..\AviUtl1\IntervalPngExport\sdk" "..\launcher.cpp" /link /DEF:"..\launcher.def" /OUT:EditablePptxExport.auf user32.lib comdlg32.lib
if errorlevel 1 exit /b 1
copy /y "EditablePptxExport.auf" "..\..\EditablePptxExport.auf" >nul
if errorlevel 1 exit /b 1
cl /nologo /LD /MT /O2 /EHsc /std:c++17 /I"..\..\..\AviUtl1\IntervalPngExport\sdk" "..\citation_launcher.cpp" /link /DEF:"..\launcher.def" /OUT:CitationManager.auf user32.lib comdlg32.lib
if errorlevel 1 exit /b 1
copy /y "CitationManager.auf" "..\..\CitationManager.auf" >nul
exit /b %errorlevel%
