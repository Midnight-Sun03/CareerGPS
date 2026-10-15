$i = 0
Get-Content 'D:/CG/core/models.py' | ForEach-Object {
    $i++
    if ($_ -match 'class (OccupationSourceResolutionCandidate|OpportunityTitleResolution|OccupationSourceResolution)') {
        Write-Output ($i.ToString() + ' ' + $_.Trim())
    }
}
