@echo off
cd /d D:\CG
D:\CG\.venv\Scripts\python.exe D:\CG\phase3_verify.py > D:\CG\p3_run_out2.txt 2> D:\CG\p3_run_err2.txt
echo EXIT=%ERRORLEVEL%
pause
