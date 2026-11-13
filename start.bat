@echo off
title Medanta Hospital - ICU PPE Kit Compliance Monitor
echo ======================================================================
echo   Medanta Hospital - ICU PPE Kit Compliance Monitor
echo   Opening at: http://localhost:8186
echo ======================================================================
cd /d "%~dp0backend"
python ppe_tracker.py
pause
