@echo off
rem Launch the setup GUI in the setup_env conda environment.
rem Used by the desktop shortcut "Bumblebee"; can also be double-clicked.

call "%USERPROFILE%\anaconda3\Scripts\activate.bat" setup_env
cd /d "%~dp0"
python setup_gui.py

rem keep the window open if the GUI stopped with an error, to read the message
if errorlevel 1 pause
