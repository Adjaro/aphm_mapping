@echo off
rem Double-clic : demarre le Referentiel mappings OMOP (options : voir scripts\start.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 pause
