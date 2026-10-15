Set-Location e:\CG
& .\.venv\Scripts\python.exe e:\CG\.precheck.py *>&1 | Out-File -Encoding utf8 e:\CG\.pre.log
Set-Content e:\CG\.pre.done done
