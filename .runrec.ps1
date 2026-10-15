Set-Location e:\CG
$env:CGPS_RECOVERY_DB = "C:\Users\matse\AppData\Local\Temp\cgps_older_deep.sqlite3"
& .\.venv\Scripts\python.exe e:\CG\.precheck.py --deep *>&1 | Out-File -Encoding utf8 e:\CG\.rec.log
Set-Content e:\CG\.rec.done done
