Set-Location e:\CG
$env:PYTHONUNBUFFERED = '1'
& .\.venv\Scripts\python.exe -u e:\CG\.recover.py *>&1 | Out-File -Encoding utf8 e:\CG\.recover.log
Set-Content e:\CG\.recover.done done
