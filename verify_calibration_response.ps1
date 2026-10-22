# SPDX-License-Identifier: GPL-3.0-only
# Offline simulations only. All printer I/O, time and waits are replaced below.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'calibration_response.ps1')
$errors=$null;$tokens=$null
$null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'run_moonraker_calibration.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$rows=@(0..42 | ForEach-Object { [pscustomobject]@{time_s=$_*.5;temperature_c=230+0.8*[Math]::Sin($_*[Math]::PI/4);target_c=230;flow_mm3_s=35} })
if (-not (Get-ResponseStability $rows 230 35 0.5).stable) { throw 'PID ripple should pass within selected percentage band' }
if ((Get-ResponseStability $rows 230 35 0.1).stable) { throw 'Ripple outside selected band must fail' }
foreach ($row in $rows) { $row.temperature_c=230+($row.time_s-10)*.05 }
if ((Get-ResponseStability $rows 230 35 0.5).stable) { throw 'Drift inside the band must still fail' }
$rows[-1].flow_mm3_s=0
if ((Get-ResponseStability $rows 230 35 0.5).stable) { throw 'Interrupted flow must fail' }
foreach ($bad in @($null,[double]::NaN,200)) {
    $rejected=$false
    try { $null=Get-ResponseQueueSeconds ([pscustomobject]@{queued_extrusion_mm=$bad}) 600 } catch { $rejected=$true }
    if (-not $rejected) { throw 'Unverified queue must be refused' }
}
# Recorded subsecond queue from the interrupted-flow regression. It must not
# round to one second and postpone refilling until the nozzle stops extruding.
$fraction=Get-ResponseQueueSeconds ([pscustomobject]@{queued_extrusion_mm=14.530125966831292}) 873.078
if ([Math]::Abs($fraction-0.9985448700000201) -gt 0.000001 -or $fraction -ge 1) { throw 'Subsecond queue was rounded; refill would wait until flow stops' }

$BaselineTemperature=230;$PeakTemperature=240;$heaterName='extruder';$SessionDirectory='offline-simulation'
$script:extrusionDistanceLimit=10
$script:events=[Collections.Generic.List[object]]::new()
$script:sent=[Collections.Generic.List[string]]::new()
$script:simRows=[Collections.Generic.List[object]]::new()
function Write-Event($Name,$Data) { $script:events.Add(@{event=$Name;data=$Data}) }
function Test-Path { return $script:stopSimulation -and $script:simTime -gt 10 }
function Start-Sleep { param($Milliseconds) }
function Assert-ThermalState($Sample) {}
function New-ResponseClock {
    $clock=[pscustomobject]@{start=$script:simTime}
    $clock | Add-Member ScriptProperty Elapsed { return [TimeSpan]::FromSeconds($script:simTime-$this.start) }
    return $clock
}
function Send-Command([string]$Command) {
    $script:sent.Add($Command)
    foreach ($line in $Command.Split("`n")) {
        if ($line -match '^G1 E([0-9.]+) F([0-9.]+)$') {
            $distance=[double]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)
            if ($distance -gt 10) { throw 'Firmware E limit exceeded' }
            $script:queue+=$distance
        } elseif ($line -match '^SET_HEATER_TEMPERATURE') {
            $script:phaseStart=$script:simTime
            $script:phaseInitial=$script:lastTemperature
        } elseif ($line -eq 'M400') { $script:queue=0 }
        else { throw "Unexpected command in response test: $line" }
    }
}
function Get-Sample($Stage) {
    $script:simTime+=.5
    $script:queue=[Math]::Max(0,$script:queue-5)
    $temperature=if ($script:responsePhase -eq 'baseline') { 230+[Math]::Max(0,45-$script:simTime)*.05 }
        else { $script:expectedTarget+($script:phaseInitial-$script:expectedTarget)*[Math]::Exp(-[Math]::Max(0,$script:simTime-$script:phaseStart-2)/4) }
    if ($script:neverStable) { $temperature=230+2*[Math]::Sin($script:simTime*[Math]::PI/2) }
    $temperature+=.6*[Math]::Sin($script:simTime*[Math]::PI/2)
    $script:lastTemperature=$temperature
    $row=[pscustomobject]@{stage=$Stage;time_s=$script:simTime;temperature_c=$temperature;target_c=$script:expectedTarget;
        power_fraction=.5;fan_fraction=0;
        flow_mm3_s=if ($script:queue -gt 0) { 10*[Math]::PI*[Math]::Pow(1.75/2,2) } else { 0 };
        # Emulate position telemetry that updates at segment boundaries rather
        # than providing a smooth interpolation of the remaining E distance.
        queued_extrusion_mm=if ($script:queue -gt 0) { [Math]::Ceiling($script:queue/10)*10-0.0145513 } else { 0 }}
    $script:simRows.Add($row)
    return $row
}
function Reset-Simulation {
    $script:simTime=0;$script:queue=0;$script:lastTemperature=230;$script:expectedTarget=230
    $script:lastProgress=[DateTime]::UtcNow;$script:neverStable=$false;$script:stopSimulation=$false
    $script:events.Clear();$script:sent.Clear();$script:simRows.Clear()
}
Reset-Simulation
Invoke-LoadedTemperatureSequence 600 (10*[Math]::PI*[Math]::Pow(1.75/2,2)) .5
$settled=@($script:events | Where-Object event -eq 'loaded_phase_settled')
if ($settled.Count -ne 3 -or $settled[0].data.elapsed_s -le 30) { throw 'Must extend loaded baseline while drifting and settle all three phases' }
if ($script:sent[-1] -ne 'M400') { throw 'End of response must drain the queue' }
if (@($script:simRows | Select-Object -Skip 3 | Where-Object { $_.flow_mm3_s -eq 0 }).Count) { throw 'Response flow drained to zero after startup' }
Write-Output ('SIMULATION:'+(ConvertTo-Json -InputObject @($script:simRows) -Depth 5 -Compress))
Reset-Simulation;$script:neverStable=$true
$rejected=$false
try { Invoke-LoadedTemperatureSequence 600 (10*[Math]::PI*[Math]::Pow(1.75/2,2)) .5 } catch { $rejected=$_.Exception.Message -match '120 seconds' }
if (-not $rejected -or $script:simTime -gt 121) { throw 'Unstable baseline must time out with no temperature steps' }
if (@($script:sent | Where-Object { $_ -match 'SET_HEATER' }).Count) { throw 'Unstable baseline advanced a temperature step' }
Reset-Simulation;$script:stopSimulation=$true
$rejected=$false
try { Invoke-LoadedTemperatureSequence 600 (10*[Math]::PI*[Math]::Pow(1.75/2,2)) .5 } catch { $rejected=$_.Exception.Message -match 'STOP' }
if (-not $rejected) { throw 'Stop request must stop submitting extrusion' }
Write-Output 'Adaptive loaded response stability, phase extension, queue bounds, firmware limits, timeout and stop: passed'
