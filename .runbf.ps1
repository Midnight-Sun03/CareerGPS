Set-Location e:\CG
& .\.venv\Scripts\python.exe manage.py backfill_occupational_intelligence --verify-determinism *>&1 | Out-File -Encoding utf8 e:\CG\.bf.log
Set-Content e:\CG\.bf.done done
