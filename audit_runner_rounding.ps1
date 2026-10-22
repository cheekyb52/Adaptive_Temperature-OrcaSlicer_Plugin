# SPDX-License-Identifier: GPL-3.0-only
param([Parameter(Mandatory=$true)][string]$FixtureFile,[Parameter(Mandatory=$true)][string]$OutputFile)
# Parse and execute only the command-building loops from the actual runner.
# The loops perform arithmetic and append strings; no runner body or I/O runs.
$ErrorActionPreference='Stop'
$errors=$null;$tokens=$null
$tree=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'run_moonraker_calibration.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$splitLoop=@($tree.FindAll({param($node) $node -is [Management.Automation.Language.ForEachStatementAst] -and $node.Condition.Extent.Text -eq '$plan.commands'},$true))
if ($splitLoop.Count -ne 1) { throw 'Expected exactly one loaded probe re-split loop' }
$staticLoop=@($tree.FindAll({param($node) $node -is [Management.Automation.Language.WhileStatementAst] -and $node.Condition.Extent.Text -eq '$elapsed -lt $FlowSeconds-0.001'},$true))
if ($staticLoop.Count -ne 1) { throw 'Expected exactly one static extrusion duration loop' }
$area=[Math]::PI*[Math]::Pow(1.75/2,2)
$script:extrusionVelocityLimit=100.0
$plans=Get-Content -LiteralPath $FixtureFile -Raw | ConvertFrom-Json
$failures=[Collections.Generic.List[object]]::new()
$cases=0;$segments=0;$maxVolumeDifference=0.0;$staticCases=0
foreach ($plan in $plans) {
    foreach ($distanceLimit in @(0.5,1.234567,10.123456,100.0)) {
        $cases++
        $limit=[double]$plan.maximum_requested_flow_mm3_s
        $script:extrusionDistanceLimit=$distanceLimit
        $commands=[Collections.Generic.List[string]]::new();$duration=0.0
        try {
            Invoke-Expression $splitLoop[0].Extent.Text
            $expected=[decimal]0
            foreach ($source in $plan.commands) { $expected+=[decimal]::Parse($source.Split(' ')[1].Substring(1),[Globalization.CultureInfo]::InvariantCulture) }
            $actual=[decimal]0
            foreach ($line in $commands) {
                if ($line -notmatch '^G1 E([0-9]+\.[0-9]{5}) F([0-9]+\.[0-9]{3})$') { throw 'Invalid command serialization' }
                $e=[decimal]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)
                $feed=[double]::Parse($Matches[2],[Globalization.CultureInfo]::InvariantCulture)
                if ($e -le 0 -or $e -gt $distanceLimit+1e-12 -or $feed/60*$area -gt $limit+1e-10) { throw 'Zero, over-distance or over-flow segment' }
                $actual+=$e;$segments++
            }
            if ($actual -ne $expected) { throw "Re-splitting changed total extrusion: $($actual-$expected) mm" }
            $maxVolumeDifference=[Math]::Max($maxVolumeDifference,[double][Math]::Abs($actual-$expected)*$area)
            if ([Math]::Abs($duration-55) -gt .01) { throw 'Re-split load plan would fail reviewed duration guard' }
        } catch { $failures.Add(@{path='Loaded';flow_mm3_s=$limit;distance_limit_mm=$distanceLimit;reason=$_.Exception.Message}) }
    }
}
foreach ($flow in @(2.0,16.0,35.0,100.0)) {
    foreach ($FlowSeconds in @(1.0,1.25,2.49,2.5,20.0,20.25,59.9,60.0)) {
        $staticCases++
        $feed=[Math]::Floor(($flow/$area)*60*1000)/1000
        $filamentVelocity=$feed/60;$script:extrusionDistanceLimit=100.0
        $elapsed=0.0;$commands=[Collections.Generic.List[string]]::new()
        try {
            Invoke-Expression $staticLoop[0].Extent.Text
            $actual=0.0
            foreach ($line in $commands) {
                $e=[double]::Parse($line.Split(' ')[1].Substring(1),[Globalization.CultureInfo]::InvariantCulture)
                if ($e -le 0) { throw 'Zero-length static segment' }
                $actual+=$e/$filamentVelocity
            }
            if ([Math]::Abs($actual-$FlowSeconds) -gt .001) { throw 'Static duration rounded unexpectedly' }
        } catch { $failures.Add(@{path='StaticFlow';flow_mm3_s=$flow;duration_s=$FlowSeconds;reason=$_.Exception.Message}) }
    }
}
$result=[ordered]@{loaded_resplit_cases=$cases;loaded_segments_checked=$segments;max_loaded_volume_difference_mm3=$maxVolumeDifference;
    static_duration_cases=$staticCases;failures=@($failures);hardware_commands_sent=$false}
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
Write-Output ($result | ConvertTo-Json -Depth 6)
if ($failures.Count) { throw "$($failures.Count) runner rounding audit cases failed" }
