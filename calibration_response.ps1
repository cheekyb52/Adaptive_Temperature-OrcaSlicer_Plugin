# SPDX-License-Identifier: GPL-3.0-only
# Loaded-response helpers. Definitions only; sourcing this file never operates hardware.
function Get-ResponseStability($Samples, [double]$Target, [double]$Flow, [double]$TolerancePercent) {
    $rows=@($Samples)
    $tolerance=$Target*$TolerancePercent/100
    $result=@{stable=$false; tolerance_c=$tolerance; drift_c_s=$null; reason='Collecting 20 seconds of constant-flow telemetry'}
    if ($rows.Count -lt 20 -or $rows[-1].time_s-$rows[0].time_s -lt 20) { return $result }
    for ($i=0; $i -lt $rows.Count; $i++) {
        $row=$rows[$i]
        if ($null -eq $row.flow_mm3_s -or [Math]::Abs($row.flow_mm3_s-$Flow) -gt [Math]::Max(0.25,$Flow*0.03) -or [Math]::Abs($row.target_c-$Target) -gt 0.01) {
            $result.reason='Waiting for uninterrupted constant flow at the requested target'; return $result
        }
        if ($i -gt 0 -and ($row.time_s-$rows[$i-1].time_s -gt 2 -or $row.time_s -le $rows[$i-1].time_s)) {
            $result.reason='Waiting for uninterrupted telemetry'; return $result
        }
    }
    $meanT=($rows | Measure-Object time_s -Average).Average
    $meanY=($rows | Measure-Object temperature_c -Average).Average
    $numerator=0.0; $denominator=0.0; $maxError=0.0
    foreach ($row in $rows) {
        $dt=$row.time_s-$meanT
        $numerator+=$dt*($row.temperature_c-$meanY); $denominator+=$dt*$dt
        $maxError=[Math]::Max($maxError,[Math]::Abs($row.temperature_c-$Target))
    }
    $drift=if ($denominator -gt 0) { $numerator/$denominator } else { 999 }
    $result.drift_c_s=$drift; $result.max_error_c=$maxError
    $result.stable=$maxError -le $tolerance -and [Math]::Abs($drift) -le 0.03
    $result.reason=if ($result.stable) { 'Stable under extrusion load' } else { 'Waiting for temperature band and drift criteria' }
    return $result
}

function Get-ResponseQueueSeconds($Sample, [double]$Feed) {
    if ($null -eq $Sample.queued_extrusion_mm -or [double]::IsNaN([double]$Sample.queued_extrusion_mm) -or [double]::IsInfinity([double]$Sample.queued_extrusion_mm)) {
        throw 'Loaded response requires commanded and live extruder position telemetry'
    }
    $seconds=[double]$Sample.queued_extrusion_mm/($Feed/60)
    if ($seconds -lt -1 -or $seconds -gt 4) { throw 'Unexpected extrusion queue; stopping response calibration' }
    # Force the Double overload: Max(0, $seconds) chooses Int32 in Windows
    # PowerShell and rounds 0.999 s to 1 s, starving the refill loop.
    return [Math]::Max(0.0,[double]$seconds)
}

function Add-ResponseExtrusion([double]$Feed) {
    # Keep ahead of motion without filling Klipper's host-stalling motion buffer.
    # Add at most one second, split to obey firmware distance limits.
    if ([double]::IsNaN($Feed) -or [double]::IsInfinity($Feed) -or $Feed -le 0 -or
        [double]::IsNaN($script:extrusionDistanceLimit) -or [double]::IsInfinity($script:extrusionDistanceLimit) -or $script:extrusionDistanceLimit -le 0) {
        throw 'Invalid response feed or firmware extrusion distance limit'
    }
    # Quantize once to the five decimal places emitted in G-code. Splitting
    # floating-point distances repeatedly can leave a sub-unit remainder that
    # rounds to zero, even for a valid flow such as 16 mm3/s.
    $remainingUnits=[long][Math]::Floor(($Feed/60.0)*100000.0)
    $limitUnits=[long][Math]::Floor($script:extrusionDistanceLimit*100000.0)
    if ($remainingUnits -le 0 -or $limitUnits -le 0) { throw 'Response segment is below G-code distance resolution' }
    $commands=[Collections.Generic.List[string]]::new()
    while ($remainingUnits -gt 0) {
        $pieceUnits=[Math]::Min([long]$remainingUnits,[long]$limitUnits)
        $piece=[decimal]$pieceUnits/100000
        $commands.Add(('G1 E{0} F{1}' -f $piece.ToString('F5',[Globalization.CultureInfo]::InvariantCulture),$Feed.ToString('F3',[Globalization.CultureInfo]::InvariantCulture)))
        $remainingUnits-=$pieceUnits
    }
    Send-Command ($commands -join "`n")
}

function New-ResponseClock { return [Diagnostics.Stopwatch]::StartNew() }

function Invoke-LoadedTemperatureSequence([double]$Feed, [double]$Flow, [double]$TolerancePercent) {
    $phases=@(@{name='baseline';target_c=$BaselineTemperature;minimum_seconds=30},
              @{name='heating';target_c=$PeakTemperature;minimum_seconds=35},
              @{name='cooling';target_c=$BaselineTemperature;minimum_seconds=40})
    Write-Event 'loaded_temperature_sequence' @{protocol='adaptive_loaded_v2';phases=$phases;commanded_flow_mm3_s=$Flow;
        stability=@{tolerance_percent=$TolerancePercent;window_s=20;max_drift_c_s=0.03;maximum_phase_seconds=120};
        maximum_duration_s=360;note='Temperature steps occur during constant extrusion. Short motion queue is replenished using live extruder position.'}
    $script:responseStreaming=$true
    $script:sampleStage='loaded_temperature_flow'
    $script:allowedTargets=@($BaselineTemperature,$PeakTemperature)
    foreach ($phase in $phases) {
        $script:responsePhase=$phase.name
        $clock=New-ResponseClock
        $window=[Collections.Generic.List[object]]::new()
        if ($phase.name -ne 'baseline') {
            if (Test-Path -LiteralPath (Join-Path $SessionDirectory 'STOP')) { throw 'Local STOP requested' }
            $script:expectedTarget=$phase.target_c
            Send-Command "SET_HEATER_TEMPERATURE HEATER=$heaterName TARGET=$($phase.target_c)"
            Write-Event 'target_step' @{target_c=$phase.target_c;phase=$phase.name}
        }
        Write-Event 'loaded_phase_start' $phase
        while ($true) {
            if (Test-Path -LiteralPath (Join-Path $SessionDirectory 'STOP')) { throw 'Local STOP requested during response calibration' }
            if ($clock.Elapsed.TotalSeconds -ge 120) { throw "Loaded $($phase.name) did not stabilize within 120 seconds; lower flow or review PID/tolerance" }
            $sample=Get-Sample 'loaded_temperature_flow'
            Assert-ThermalState $sample
            if ($sample.temperature_c -lt $BaselineTemperature-5) { throw 'Temperature sag exceeded response-test tolerance' }
            $queued=Get-ResponseQueueSeconds $sample $Feed
            $window.Add($sample)
            while ($window.Count -gt 1 -and $sample.time_s-$window[0].time_s -gt 21) { $window.RemoveAt(0) }
            $stability=Get-ResponseStability $window $phase.target_c $Flow $TolerancePercent
            if ($clock.Elapsed.TotalSeconds -ge $phase.minimum_seconds -and $stability.stable) {
                Write-Event 'loaded_phase_settled' @{phase=$phase.name;target_c=$phase.target_c;time_s=$sample.time_s;
                    elapsed_s=$clock.Elapsed.TotalSeconds;tolerance_c=$stability.tolerance_c;drift_c_s=$stability.drift_c_s}
                # Refill before a target change to keep the extrusion load continuous.
                if ($phase.name -ne 'cooling' -and $queued -lt 1) { Add-ResponseExtrusion $Feed }
                break
            }
            if ($queued -lt 1) { Add-ResponseExtrusion $Feed }
            if (([DateTime]::UtcNow-$script:lastProgress).TotalSeconds -ge 10) {
                Write-Output "Loaded $($phase.name): $($sample.temperature_c) C / $($phase.target_c) C; $($stability.reason)"
                $script:lastProgress=[DateTime]::UtcNow
            }
            Start-Sleep -Milliseconds 100
        }
    }
    Send-Command 'M400'
    $script:responseStreaming=$false
    $script:responsePhase='complete'
}
