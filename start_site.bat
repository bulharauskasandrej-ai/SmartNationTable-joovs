@echo off
cd /d "%~dp0"
python student_grades_site.py
if errorlevel 1 pause
