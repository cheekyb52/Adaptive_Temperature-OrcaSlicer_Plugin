# SPDX-License-Identifier: GPL-3.0-only
# Offline only: inspect generated E-only segments; never contact a printer.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'calibration_response.ps1')
$script:batches=[Collections.Generic.List[string]]::new()
function Send-Command([string]$Command) { $script:batches.Add($Command) }
$area=[Math]::PI*[Math]::Pow(1.75/2,2)
foreach ($flow in @(16.0,0.1,0.5,1.0,3.75,35.0,75.0,120.0,500.0)) {
    foreach ($limit in @(0.5,10.0,100.0)) {
        $feed=[Math]::Floor(($flow/$area)*60*1000)/1000
        $script:extrusionDistanceLimit=$limit
        $script:batches.Clear()
        Add-ResponseExtrusion $feed
        if ($script:batches.Count -ne 1) { throw 'A refill must submit exactly one bounded batch' }
        $total=0.0
        foreach ($line in $script:batches[0].Split("`n")) {
            if ($line -notmatch '^G1 E([0-9]+\.[0-9]{5}) F([0-9]+\.[0-9]{3})$') { throw 'Response refill contains an unexpected command' }
            $distance=[double]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)
            $actualFeed=[double]::Parse($Matches[2],[Globalization.CultureInfo]::InvariantCulture)
            if ($distance -le 0 -or $distance -gt $limit -or [Math]::Abs($actualFeed-$feed) -gt .000001) { throw 'Segment exceeds reviewed feed/distance or has zero length' }
            $total+=$distance
        }
        $expected=$feed/60
        if ($total -gt $expected+0.000000001 -or $expected-$total -gt 0.000010001) { throw 'Quantized batch must stay within one G-code distance unit of a one-second refill' }
    }
}
foreach ($case in @(@(0.0,10.0),@(-1.0,10.0),@([double]::NaN,10.0),@(600.0,0.0))) {
    $script:batches.Clear();$script:extrusionDistanceLimit=$case[1]
    $refused=$false
    try { Add-ResponseExtrusion $case[0] } catch { $refused=$true }
    if (-not $refused -or $script:batches.Count) { throw 'Invalid segment inputs must be refused before submission' }
}
Write-Output 'Response segmentation across 9 flows and 3 firmware distance limits: passed'
