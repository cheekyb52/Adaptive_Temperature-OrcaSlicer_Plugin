# SPDX-License-Identifier: GPL-3.0-only
param([Parameter(Mandatory=$true)][string]$OutputFile)
# Offline audit of actual command generators. All command submission is mocked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'calibration_response.ps1')
$script:captured=''
function Send-Command([string]$Command) { $script:captured=$Command }
$area=[Math]::PI*[Math]::Pow(1.75/2,2)
$script:cases=0;$script:segments=0;$script:queueChecks=0
$script:maxFeedShortfall=0.0;$script:maxVolumeShortfall=0.0;$script:maxDurationShortfall=0.0
$script:failures=[Collections.Generic.List[object]]::new()

function Test-ResponseCase([double]$Flow,[double]$DistanceLimit) {
    $script:extrusionDistanceLimit=$DistanceLimit
    $feed=[Math]::Floor(($Flow/$area)*60000.0)/1000.0
    $script:captured=''
    $script:cases++
    try {
        Add-ResponseExtrusion $feed
        if (-not $script:captured) { throw 'No command batch generated' }
        $total=[decimal]0
        foreach ($line in $script:captured.Split("`n")) {
            if ($line -notmatch '^G1 E([0-9]+\.[0-9]{5}) F([0-9]+\.[0-9]{3})$') { throw 'Unexpected command or numeric serialization' }
            $distance=[decimal]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)
            $actualFeed=[double]::Parse($Matches[2],[Globalization.CultureInfo]::InvariantCulture)
            if ($distance -le 0 -or [double]$distance -gt $DistanceLimit+1e-12) { throw 'Zero-length or over-limit E segment' }
            if ([Math]::Abs($actualFeed-$feed) -gt 1e-10 -or $actualFeed/60*$area -gt $Flow+1e-10) { throw 'Serialized feed exceeds requested flow or changed unexpectedly' }
            $total+=$distance;$script:segments++
        }
        $expected=$feed/60.0
        $gap=$expected-[double]$total
        if ($gap -lt -1e-12 -or $gap -gt 0.000010001) { throw 'Refill distance differs by more than one rounding unit' }
        $script:maxFeedShortfall=[Math]::Max($script:maxFeedShortfall,$Flow-$feed/60*$area)
        $script:maxVolumeShortfall=[Math]::Max($script:maxVolumeShortfall,$Flow-[double]$total*$area)
        $script:maxDurationShortfall=[Math]::Max($script:maxDurationShortfall,1.0-[double]$total/$expected)
        $seconds=Get-ResponseQueueSeconds ([pscustomobject]@{queued_extrusion_mm=$expected*0.9999}) $feed
        if ([Math]::Abs($seconds-0.9999) -gt 1e-10 -or $seconds -ge 1) { throw 'Fractional queue time rounded and would postpone refill' }
        $script:queueChecks++
    } catch {
        $script:failures.Add(@{flow_mm3_s=$Flow;distance_limit_mm=$DistanceLimit;reason=$_.Exception.Message})
    }
}

# 9,801 requested flows: every 0.01 mm3/s from 2 through 100 inclusive.
for ($hundredths=200;$hundredths -le 10000;$hundredths++) {
    Test-ResponseCase ($hundredths/100.0) 100.0
    if (($hundredths-200)%2000 -eq 0) { Write-Output "Dense response sweep: $($hundredths/100.0) mm3/s" }
}
# Force splitting at both ordinary and non-integer firmware distance limits.
foreach ($limit in @(0.5,1.0,1.234567,10.0,10.123456)) {
    for ($tenths=20;$tenths -le 1000;$tenths++) { Test-ResponseCase ($tenths/10.0) $limit }
    Write-Output "Split response sweep: distance limit $limit mm complete"
}
# Values immediately below/at/above serialized feed boundaries.
$boundaryCases=0
for ($feedUnits=50000;$feedUnits -le 2400000;$feedUnits+=12347) {
    $flowAtBoundary=$feedUnits/60000.0*$area
    foreach ($epsilon in @(-1e-10,0.0,1e-10)) {
        $flow=$flowAtBoundary+$epsilon
        if ($flow -ge 2 -and $flow -le 100) { Test-ResponseCase $flow 1.234567;$boundaryCases++ }
    }
}
$result=[ordered]@{range_mm3_s=@(2,100);dense_step_mm3_s=0.01;dense_flow_count=9801;
    split_sweep_step_mm3_s=0.1;distance_limits_mm=@(0.5,1.0,1.234567,10.0,10.123456,100.0);
    feed_boundary_cases=$boundaryCases;response_cases=$script:cases;segments_checked=$script:segments;
    fractional_queue_checks=$script:queueChecks;failures=@($script:failures);
    max_feed_shortfall_mm3_s=$script:maxFeedShortfall;max_one_second_volume_shortfall_mm3=$script:maxVolumeShortfall;
    max_one_second_duration_shortfall_s=$script:maxDurationShortfall;
    hardware_commands_sent=$false;scope='Numerical command generation and serialization only; no physical hotend capacity assertion'}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
Write-Output ($result | ConvertTo-Json -Depth 8)
if ($script:failures.Count) { throw "$($script:failures.Count) rounding audit cases failed" }
