Set-Location e:\CG
while (-not (Test-Path e:\CG\.bf.done)) { Start-Sleep -Seconds 5 }
Start-Sleep -Seconds 3
& .\.venv\Scripts\python.exe e:\CG\.bfcheck.py *>&1 | Out-File -Encoding utf8 e:\CG\.bfcheck.log
Set-Content e:\CG\.bfpost.done done
