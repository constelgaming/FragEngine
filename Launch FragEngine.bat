@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\pythonw.exe" (
    echo FragEngine could not find its Python environment in .venv.
    echo See the Windows installation steps in README.md.
    pause
    exit /b 1
)
start "FragEngine" ".venv\Scripts\pythonw.exe" -m fragengine
popd
