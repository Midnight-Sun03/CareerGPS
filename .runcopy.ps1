Set-Location e:\CG
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
Copy-Item db.sqlite3 ("db.sqlite3.pre_recovery_" + $ts)
if (Test-Path db.sqlite3-journal) { Copy-Item db.sqlite3-journal ("db.sqlite3-journal.pre_recovery_" + $ts) }
'a_live=' + (Get-Item db.sqlite3).Length | Out-File -Encoding utf8 e:\CG\.copy.log
'pair=db.sqlite3.pre_recovery_' + $ts | Out-File -Append -Encoding utf8 e:\CG\.copy.log
'b_copy=' + (Get-Item ("db.sqlite3.pre_recovery_" + $ts)).Length | Out-File -Append -Encoding utf8 e:\CG\.copy.log
Set-Content e:\CG\.copy.done done
