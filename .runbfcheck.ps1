Set-Location e:\CG
& .\.venv\Scripts\python.exe e:\CG\.bfcheck.py *>&1 | Out-File -Encoding utf8 e:\CG\.bfcheck.log
Set-Content e:\CG\.bfcheck.done done
