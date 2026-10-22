param(
    [Parameter(Mandatory=$true)][ValidateSet('Position','Thermal','StaticFlow','Loaded','LoadedThermal','HeaterOff')][string]$Action,
    [Parameter(Mandatory=$true)][string]$PrinterUrl,
    [string]$SessionDirectory,
    [string]$ProbePlan,
    [switch]$RequireFanOff,
    [ValidateRange(0,31)][int]$PhysicalTool = 0,
    [string]$HeaterObject = 'extruder',
    [string]$FanObject = 'fan',
    [ValidateSet('active_only','t_command','u1_macro')][string]$ToolChangeStyle='active_only',
    [ValidateSet('y_then_x','x_then_y')][string]$PositioningOrder='y_then_x',
    [ValidateRange(1,450)][double]$MinimumTemperature=210,
    [ValidateRange(1,450)][double]$MaximumTemperature=245,
    [ValidateRange(0.1,500)][double]$MaximumFlowMm3S=75,
    [ValidateRange(1,450)][double]$BaselineTemperature = 220,
    [ValidateRange(1,450)][double]$PeakTemperature = 230,
    [ValidateRange(0.05,1)][double]$StabilityTolerancePercent = 0.5,
    [ValidateRange(1,2)][int]$ThermalRepeats = 2,
    [ValidateRange(0.1,500)][double]$FlowMm3S = 3.75,
    [ValidateRange(1,60)][double]$FlowSeconds = 20
)
# Explicit, transient Moonraker/Klipper calibration controls. No printer files, settings,
# calibration data, firmware or macros are saved or changed persistently.
$ErrorActionPreference = 'Stop'
$printerBase = $PrinterUrl.TrimEnd('/')
$heaterName=$HeaterObject
if ($heaterName -notmatch '^extruder([1-9][0-9]*)?$') { throw 'Invalid extruder heater mapping' }
$fanName=$FanObject
if ($fanName -notmatch '^(fan|fan_generic [A-Za-z0-9_]+)$') { throw 'Invalid part-fan mapping' }
. (Join-Path $PSScriptRoot 'calibration_geometry.ps1')
. (Join-Path $PSScriptRoot 'calibration_response.ps1')
. (Join-Path $PSScriptRoot 'calibration_logging.ps1')
$script:extrusionVelocityLimit=0.0
$script:extrusionDistanceLimit=0.0
$fanQuery = [Uri]::EscapeDataString($fanName)
if (-not $SessionDirectory) {
    $stamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time').ToString('yyyyMMdd-HHmmss')
    $SessionDirectory = Join-Path $PSScriptRoot "Research\sessions\$stamp-$Action"
}
New-Item -ItemType Directory -Path $SessionDirectory -Force | Out-Null
$eventsPath = Join-Path $SessionDirectory 'events.jsonl'
$samplesPath = Join-Path $SessionDirectory 'telemetry.jsonl'
$script:ownsHeater = $false
$script:fanExpected = $null
$script:expectedTarget = $null
$script:extrusionStateSaved = $false
$script:lastProgress = [DateTime]::UtcNow.AddSeconds(-60)
$script:sampleStage = 'static_flow'
$script:allowedTargets = @()
$script:responseStreaming = $false
$script:responsePhase = ''
$script:eventWriter = $null
$script:sampleWriter = $null
$script:cleanupInProgress = $false
$script:cleanupFailure = $false
$script:cleanupLogFailure = $false

function Write-Event($Name, $Data) {
    $line=[ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); event=$Name; data=$Data } | ConvertTo-Json -Compress -Depth 10
    try { $script:eventWriter.WriteLine($line) }
    catch {
        # A broken diagnostic file must never prevent heater shutdown or
        # state restoration. Normal execution still aborts on lost logging.
        if (-not $script:cleanupInProgress) { throw }
        $script:cleanupLogFailure=$true
        Write-Warning "Cleanup event could not be recorded: $Name : $($_.Exception.Message)"
    }
}

function Get-VerifiedState {
    $r = Invoke-RestMethod -Uri "$printerBase/printer/objects/query?print_stats&virtual_sdcard&pause_resume" -TimeoutSec 5
    $s = $r.result.status
    if (-not $s.print_stats.state -or $null -eq $s.virtual_sdcard.is_active -or $null -eq $s.pause_resume.is_paused) { throw 'Unable to verify live printer state' }
    if ($s.print_stats.state -notin @('standby','complete','cancelled') -or $s.virtual_sdcard.is_active -or $s.pause_resume.is_paused) {
        throw "Printer is not idle: $($s.print_stats.state). No dependent controls allowed."
    }
    return $r.result
}

function Get-Sample($Stage) {
    $checked = Get-VerifiedState
    $r = Invoke-RestMethod -Uri "$printerBase/printer/objects/query?$heaterName&toolhead&gcode_move&$fanQuery&webhooks&motion_report" -TimeoutSec 5
    $s = $r.result.status
    if ($s.webhooks.state -ne 'ready') { throw "Klipper is not ready: $($s.webhooks.state_message)" }
    if ($null -eq $s.$heaterName.temperature -or $null -eq $s.$heaterName.target -or $null -eq $s.$heaterName.power) { throw 'Missing heater telemetry' }
    if ($null -eq $s.$fanName.speed) { throw 'Missing selected part-fan telemetry' }
    $row = [pscustomobject][ordered]@{
        time_s=$r.result.eventtime; utc=[DateTime]::UtcNow.ToString('o'); stage=$Stage
        temperature_c=[double]$s.$heaterName.temperature; target_c=[double]$s.$heaterName.target
        power_fraction=[double]$s.$heaterName.power; fan_fraction=[double]$s.$fanName.speed
        physical_tool=$PhysicalTool; physical_heater=$heaterName
        hotend_state=$s.$heaterName.state; active_extruder=$s.toolhead.extruder
        homed_axes=$s.toolhead.homed_axes; position=@($s.gcode_move.gcode_position)
        machine_position=@($s.toolhead.position); axis_minimum=@($s.toolhead.axis_minimum); axis_maximum=@($s.toolhead.axis_maximum)
        speed_factor=$s.gcode_move.speed_factor; extrude_factor=$s.gcode_move.extrude_factor
        print_state=$checked.status.print_stats.state; state_check_eventtime=$checked.eventtime
        live_extruder_velocity_mm_s=$s.motion_report.live_extruder_velocity
        live_xyz_velocity_mm_s=$s.motion_report.live_velocity
        response_phase=$script:responsePhase
        queued_extrusion_mm=if ($s.toolhead.position.Count -ge 4 -and $s.motion_report.live_position.Count -ge 4) { [double]$s.toolhead.position[3]-[double]$s.motion_report.live_position[3] } else { $null }
        flow_mm3_s=if ($s.toolhead.extruder -eq $heaterName -and $null -ne $s.motion_report.live_extruder_velocity) { [Math]::Max(0.0,[double]$s.motion_report.live_extruder_velocity)*[Math]::PI*[Math]::Pow(1.75/2,2) } else { $null }
        flow_status='Motion-planner estimate from executing E velocity, not measured delivered filament'
    }
    $script:sampleWriter.WriteLine(($row | ConvertTo-Json -Compress -Depth 6))
    return $row
}

function Send-Command([string]$Command) {
    $null = Get-VerifiedState
    Write-Event 'command_requested' $Command
    $body = @{script=$Command} | ConvertTo-Json -Compress
    $r = Invoke-RestMethod -Method Post -Uri "$printerBase/printer/gcode/script" -ContentType 'application/json' -Body $body -TimeoutSec 180
    if ($r.error -or $r.result -ne 'ok') { throw 'Printer command did not return success' }
    Write-Event 'command_completed' $Command
    Write-Output "Completed: $($Command.Replace("`n", ' | '))"
}

function Assert-TestPosition($Sample) {
    if ($Sample.active_extruder -ne $heaterName -or ($Sample.hotend_state -and $Sample.hotend_state -ne 'ACTIVATE') -or $Sample.homed_axes -notmatch 'x' -or $Sample.homed_axes -notmatch 'y' -or $Sample.homed_axes -notmatch 'z') {
        throw "Physical T$PhysicalTool is not active and homed"
    }
    $target=Get-CalibrationTarget $Sample
    for ($axis=0;$axis -lt 3;$axis++) {
        if ([Math]::Abs([double]$Sample.machine_position[$axis]-[double]$target[$axis]) -gt 1) { throw 'Tool moved away from XY travel center / Z100 mm' }
    }
}

function Assert-ThermalState($Sample) {
    Assert-TestPosition $Sample
    if ($Sample.temperature_c -ge $MaximumTemperature+5) { throw 'Measured temperature exceeded the configured ceiling plus 5 C stop margin' }
    if ($null -ne $script:fanExpected -and [Math]::Abs($Sample.fan_fraction-$script:fanExpected) -gt 0.01) { throw 'Part fan changed during the experiment' }
    if ($null -ne $script:expectedTarget -and [Math]::Abs($Sample.target_c-$script:expectedTarget) -gt 0.01) { throw 'Unexpected heater target change' }
    if ($script:allowedTargets.Count -gt 0 -and $Sample.target_c -notin $script:allowedTargets) { throw 'Target outside the reviewed loaded-temperature sequence' }
    if ([Math]::Abs($Sample.speed_factor-1) -gt 0.001 -or [Math]::Abs($Sample.extrude_factor-1) -gt 0.001) { throw 'Unexpected speed or extrusion scaling' }
}

function Set-TestTemperature([double]$Temperature) {
    if ($Temperature -lt $MinimumTemperature -or $Temperature -gt $MaximumTemperature) { throw 'Temperature target exceeds configured calibration bounds' }
    $sample = Get-Sample 'before_target_step'
    Assert-TestPosition $sample
    $script:ownsHeater = $true
    $script:expectedTarget = $Temperature
    Send-Command "SET_HEATER_TEMPERATURE HEATER=$heaterName TARGET=$Temperature"
    Write-Event 'target_step' @{target_c=$Temperature; before_time_s=$sample.time_s; before_temperature_c=$sample.temperature_c}
}

function Wait-Settled([string]$Stage, [double]$Target, [int]$MaximumSeconds, [double]$ToleranceC=0.5) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $window = [Collections.Generic.List[object]]::new()
    while ($clock.Elapsed.TotalSeconds -lt $MaximumSeconds) {
        if (Test-Path -LiteralPath (Join-Path $SessionDirectory 'STOP')) { throw 'Local STOP requested' }
        $sample = Get-Sample $Stage
        Assert-ThermalState $sample
        $window.Add($sample)
        while ($window.Count -gt 1 -and $sample.time_s-$window[0].time_s -gt 21) { $window.RemoveAt(0) }
        if (([DateTime]::UtcNow-$script:lastProgress).TotalSeconds -ge 15) {
            Write-Output "$Stage : $($sample.temperature_c) C, target $Target C, heater power $($sample.power_fraction)"
            $script:lastProgress = [DateTime]::UtcNow
        }
        if ($window.Count -ge 20 -and $sample.time_s-$window[0].time_s -ge 20) {
            $temps = @($window | ForEach-Object { $_.temperature_c })
            $maxError = ($temps | ForEach-Object { [Math]::Abs($_-$Target) } | Measure-Object -Maximum).Maximum
            $meanT = ($window | Measure-Object time_s -Average).Average
            $meanY = ($window | Measure-Object temperature_c -Average).Average
            $numerator = 0.0; $denominator = 0.0
            foreach ($point in $window) {
                $dt = $point.time_s-$meanT
                $numerator += $dt*($point.temperature_c-$meanY)
                $denominator += $dt*$dt
            }
            $drift = if ($denominator -gt 0) { $numerator/$denominator } else { 999 }
            if ($maxError -le $ToleranceC -and [Math]::Abs($drift) -le 0.03) {
                Write-Event 'settled' @{stage=$Stage; target_c=$Target; time_s=$sample.time_s; drift_c_s=$drift; max_error_c=$maxError}
                Write-Output "Settled: $Stage at $Target C"
                return
            }
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Stage did not settle within $MaximumSeconds seconds: $Stage"
}

function Send-MonitoredExtrusion([string]$Command) {
    # Submit a bounded continuous E-only batch, then monitor via read-only
    # queries while the response waits for its final M400. No per-chunk HTTP gaps.
    $null = Get-VerifiedState
    Write-Event 'command_requested' $Command
    $body = @{script=$Command} | ConvertTo-Json -Compress
    # A separate PowerShell job is necessary: on this host the .NET async
    # response call blocked its calling thread and prevented telemetry reads.
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $job = Start-Job -ArgumentList $printerBase,$body -ScriptBlock {
        param($base,$jsonBody)
        $ErrorActionPreference='Stop'
        # Check again INSIDE the worker, immediately before its one write.
        $state=Invoke-RestMethod -Uri "$base/printer/objects/query?print_stats&virtual_sdcard&pause_resume" -TimeoutSec 5
        $s=$state.result.status
        if ($s.print_stats.state -notin @('standby','complete','cancelled') -or $null -eq $s.virtual_sdcard.is_active -or $s.virtual_sdcard.is_active -or $null -eq $s.pause_resume.is_paused -or $s.pause_resume.is_paused) {
            throw 'Worker could not verify idle state; extrusion not submitted'
        }
        $response=Invoke-RestMethod -Method Post -Uri "$base/printer/gcode/script" -ContentType 'application/json' -Body $jsonBody -TimeoutSec 150
        if ($response.error -or $response.result -ne 'ok') { throw 'Extrusion batch did not return success' }
        return 'extrusion_completed'
    }
    try {
        while ($job.State -in @('NotStarted','Running')) {
            if ($clock.Elapsed.TotalSeconds -gt 155) { throw 'Extrusion response exceeded the bounded wait' }
            if (Test-Path -LiteralPath (Join-Path $SessionDirectory 'STOP')) { throw 'Local STOP requested during extrusion' }
            $sample = Get-Sample $script:sampleStage
            Assert-ThermalState $sample
            $minimumTemperature = if ($null -ne $script:expectedTarget) { $script:expectedTarget-5 } else { ($script:allowedTargets | Measure-Object -Minimum).Minimum-5 }
            if ($sample.temperature_c -lt $minimumTemperature) { throw 'Temperature sag exceeded load-test tolerance (5 C below the lowest authorized target)' }
            Start-Sleep -Milliseconds 100
        }
        $result = Receive-Job -Job $job -Wait -ErrorAction Stop
        if ($result -ne 'extrusion_completed') { throw 'No successful extrusion result from worker' }
        Write-Event 'command_completed' $Command
        Write-Output "Completed monitored extrusion batch in $([Math]::Round($clock.Elapsed.TotalSeconds,2)) seconds"
    } finally {
        # Stopping a local HTTP worker is NOT represented as canceling motion
        # already queued on the printer. Load-step batches are <=60 seconds;
        # Loaded-temperature response uses a separate short-queue protocol.
        if ($job.State -in @('NotStarted','Running')) { Stop-Job -Job $job; Write-Event 'worker_stopped' 'An already-submitted bounded batch may still be executing.' }
        Remove-Job -Job $job -Force
    }
}

try {
    $script:eventWriter=New-CalibrationJsonlWriter $eventsPath
    $script:sampleWriter=New-CalibrationJsonlWriter $samplesPath
    Write-Event 'session_start' @{action=$Action; physical_tool=$PhysicalTool; physical_heater=$heaterName; flow_mm3_s=$FlowMm3S; flow_seconds=$FlowSeconds; max_target_c=$MaximumTemperature; max_flow_mm3_s=$MaximumFlowMm3S; baseline_temperature_c=$BaselineTemperature; peak_temperature_c=$PeakTemperature; thermal_repeats=$ThermalRepeats}
    $null=Get-VerifiedState
    $configuration=Invoke-RestMethod -Uri "$printerBase/printer/objects/query?configfile" -TimeoutSec 5
    $firmware=$configuration.result.status.configfile.settings.$heaterName
    if ($null -eq $firmware -or $null -eq $firmware.max_temp) { throw 'Missing firmware heater limits for selected tool' }
    if ($MaximumTemperature -gt [double]$firmware.max_temp-5 -or $MinimumTemperature -ge $MaximumTemperature) { throw 'Configured temperatures conflict with firmware heater limits' }
    if ($Action -notin @('Position','HeaterOff')) {
        if ($null -eq $firmware.filament_diameter -or [Math]::Abs([double]$firmware.filament_diameter-1.75) -gt 0.001) { throw 'Live calibration requires a verified 1.75 mm filament configuration' }
        if ($null -eq $firmware.max_extrude_only_velocity -or $null -eq $firmware.max_extrude_only_distance) { throw 'Missing firmware extrude-only limits' }
        $script:extrusionVelocityLimit=[double]$firmware.max_extrude_only_velocity
        $script:extrusionDistanceLimit=[Math]::Min(100.0,[double]$firmware.max_extrude_only_distance)
        if ($script:extrusionVelocityLimit -le 0 -or $script:extrusionDistanceLimit -le 0) { throw 'Invalid firmware extrusion limits' }
        if ($MinimumTemperature -lt [double]$firmware.min_extrude_temp) { throw 'Minimum calibration temperature is below firmware extrusion temperature' }
    }
    $initial = Get-Sample 'initial'
    if (Test-Path -LiteralPath (Join-Path $SessionDirectory 'STOP')) { throw 'Local STOP requested before starting' }
    if ($RequireFanOff -and [Math]::Abs($initial.fan_fraction) -gt 0.001) { throw 'This packaged calibration requires the selected tool part fan to be off' }
    if ($Action -eq 'Position') {
        if ($initial.target_c -gt 0) { throw 'Selected tool already has an active heater target; investigate before moving it' }
        if ($initial.homed_axes -notmatch 'x' -or $initial.homed_axes -notmatch 'y' -or $initial.homed_axes -notmatch 'z') { Send-Command 'G28' }
        $position=Get-Sample 'before_positioning'
        Assert-CalibrationMachinePosition $position $position.machine_position
        $lift=Get-CalibrationClearanceTarget $position
        $coordinate=Convert-CalibrationCoordinate $position $lift
        Send-Command "G90`nG1 Z$($coordinate[2]) F600`nM400"
        $position=Get-Sample 'before_tool_selection_clearance_verified'
        # This check intentionally allows the previously active tool; the
        # selected tool is verified after the printer's selection sequence.
        Assert-CalibrationMachinePosition $position $lift
        if ($ToolChangeStyle -eq 'u1_macro') { Send-Command "T$PhysicalTool A0" }
        elseif ($ToolChangeStyle -eq 't_command') { Send-Command "T$PhysicalTool" }
        Send-Command 'M400'
        $position=Get-Sample 'after_tool_selection'
        if ($position.active_extruder -ne $heaterName) { throw 'Selected tool command did not activate the configured heater/extruder' }
        Move-CalibrationApproach $position $PositioningOrder
    } elseif ($Action -eq 'HeaterOff') {
        Send-Command "SET_HEATER_TEMPERATURE HEATER=$heaterName TARGET=0"
    } else {
        Assert-TestPosition $initial
        if ($initial.target_c -gt 0) { throw 'Existing heater target indicates a conflicting operation' }
        if ([Math]::Abs($initial.speed_factor-1) -gt 0.001 -or [Math]::Abs($initial.extrude_factor-1) -gt 0.001) { throw 'Expected 100 percent speed and extrusion scaling' }
        $script:fanExpected = $initial.fan_fraction
        if ($Action -eq 'Thermal') {
            if ($PeakTemperature-$BaselineTemperature -lt 2) { throw 'Temperature step must be at least 2 C' }
            Set-TestTemperature $BaselineTemperature
            Wait-Settled "baseline_$BaselineTemperature" $BaselineTemperature 180
            for ($repeat=1; $repeat -le $ThermalRepeats; $repeat++) {
                Set-TestTemperature $PeakTemperature
                Wait-Settled "heating_${repeat}_$PeakTemperature" $PeakTemperature 120
                Set-TestTemperature $BaselineTemperature
                Wait-Settled "cooling_${repeat}_$BaselineTemperature" $BaselineTemperature 180
            }
        } elseif ($Action -eq 'LoadedThermal') {
            if ($FlowMm3S -gt $MaximumFlowMm3S -or $BaselineTemperature -lt $MinimumTemperature -or $PeakTemperature -gt $MaximumTemperature -or $PeakTemperature-$BaselineTemperature -lt 2) { throw 'Response test exceeds selected tool bounds' }
            $area=[Math]::PI*[Math]::Pow(1.75/2,2)
            $feed=[Math]::Floor(($FlowMm3S/$area)*60*1000)/1000
            if ($feed -le 0 -or $feed/60 -gt $script:extrusionVelocityLimit) { throw 'Invalid loaded temperature-step feed' }
            if ($PeakTemperature-$BaselineTemperature -lt 4*$PeakTemperature*$StabilityTolerancePercent/100) { throw 'Temperature step must be at least four times the stability tolerance band' }
            Set-TestTemperature $BaselineTemperature
            Wait-Settled 'loaded_temperature_no_flow_baseline' $BaselineTemperature 180 ($BaselineTemperature*$StabilityTolerancePercent/100)
            Send-Command 'SAVE_GCODE_STATE NAME=ORCA_FLOW_CALIBRATION'
            $script:extrusionStateSaved=$true
            Send-Command "M83`nM400"
            Invoke-LoadedTemperatureSequence $feed ($feed/60*$area) $StabilityTolerancePercent
            $script:expectedTarget=$BaselineTemperature
            Wait-Settled 'loaded_temperature_post_flow' $BaselineTemperature 120 ($BaselineTemperature*$StabilityTolerancePercent/100)
        } elseif ($Action -eq 'Loaded') {
            if (-not $ProbePlan) { throw 'Loaded calibration requires a reviewed local probe plan' }
            $plan=Get-Content -LiteralPath $ProbePlan -Raw | ConvertFrom-Json
            $limit=[double]$plan.maximum_requested_flow_mm3_s
            $temperature=[double]$plan.temperature_target_c
            if ($plan.type -ne 'loaded_flow_response' -or $plan.physical_tool -ne $PhysicalTool -or $plan.temperature_target_c -ne $temperature -or $temperature -lt $MinimumTemperature -or $temperature -gt $MaximumTemperature) { throw 'Unsupported loaded probe plan/physical head/temperature' }
            if ($limit -le 0 -or $limit -gt $MaximumFlowMm3S -or $plan.filament_diameter_mm -ne 1.75) { throw 'Probe does not match user-supplied flow point or verified filament diameter' }
            $area=[Math]::PI*[Math]::Pow(1.75/2,2)
            $commands=[Collections.Generic.List[string]]::new()
            $duration=0.0
            foreach ($command in $plan.commands) {
                if ($command -notmatch '^G1 E([0-9]+\.[0-9]+) F([0-9]+\.[0-9]+)$') { throw 'Probe contains a command other than bounded E-only extrusion' }
                $e=[decimal]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)
                $feed=[double]::Parse($Matches[2],[Globalization.CultureInfo]::InvariantCulture)
                if ($e -le 0 -or $feed -le 0 -or $feed/60 -gt $script:extrusionVelocityLimit -or $feed/60*$area -gt $limit+0.000001) { throw 'Extrusion command exceeds user or firmware limits' }
                $duration += $e/($feed/60)
                # Preserve the source command's exact five-decimal distance
                # while splitting at fractional firmware limits.
                $remainingUnits=[long]($e*100000)
                $limitUnits=[long][Math]::Floor($script:extrusionDistanceLimit*0.9*100000.0)
                if ($limitUnits -le 0) { throw 'Firmware extrusion distance limit is below G-code resolution' }
                while ($remainingUnits -gt 0) {
                    $pieceUnits=[Math]::Min([long]$remainingUnits,[long]$limitUnits)
                    $piece=[decimal]$pieceUnits/100000
                    $commands.Add(('G1 E{0} F{1}' -f $piece.ToString('F5',[Globalization.CultureInfo]::InvariantCulture),$feed.ToString('F3',[Globalization.CultureInfo]::InvariantCulture)))
                    $remainingUnits-=$pieceUnits
                }
            }
            if ($duration -lt 1 -or $duration -gt 60 -or [Math]::Abs($duration-55) -gt 0.01) { throw 'Unexpected loaded probe duration' }
            Copy-Item -LiteralPath $ProbePlan -Destination (Join-Path $SessionDirectory 'probe-plan.json')
            Write-Event 'loaded_probe_plan' @{temperature_c=$temperature; max_flow_mm3_s=$limit; phases=$plan.phases; nominal_duration_s=$duration}
            Set-TestTemperature $temperature
            Wait-Settled "loaded_baseline_$temperature" $temperature 180
            Send-Command 'SAVE_GCODE_STATE NAME=ORCA_FLOW_CALIBRATION'
            $script:extrusionStateSaved=$true
            Send-Command 'M83'
            $sample=Get-Sample 'before_loaded_flow'
            Assert-ThermalState $sample
            if ($sample.temperature_c -lt $temperature-0.5) { throw 'Loaded probe starting temperature is not settled' }
            $script:sampleStage='loaded_flow'
            Write-Event 'loaded_flow_start' @{time_s=$sample.time_s; max_flow_mm3_s=$limit; target_c=$temperature}
            $commands.Add('M400')
            Send-MonitoredExtrusion ($commands -join "`n")
            Write-Event 'loaded_flow_end' @{}
            Wait-Settled "loaded_recovery_$temperature" $temperature 120
        } else {
            Set-TestTemperature 240
            Wait-Settled 'static_baseline_240' 240 180
            Send-Command 'SAVE_GCODE_STATE NAME=ORCA_FLOW_CALIBRATION'
            $script:extrusionStateSaved = $true
            Send-Command 'M83'
            $area = [Math]::PI*[Math]::Pow(1.75/2,2)
            $feed = [Math]::Floor(($FlowMm3S/$area)*60*1000)/1000
            $filamentVelocity = $feed/60
            if ($filamentVelocity -gt $script:extrusionVelocityLimit) { throw 'Exceeds configured extrude-only velocity' }
            $elapsed = 0.0
            $commands = [Collections.Generic.List[string]]::new()
            Write-Event 'static_flow_start' @{flow_mm3_s=$filamentVelocity*$area; feed_mm_min=$feed; requested_seconds=$FlowSeconds}
            while ($elapsed -lt $FlowSeconds-0.001) {
                $chunkSeconds = [Math]::Min(2.0,$FlowSeconds-$elapsed)
                $e = [Math]::Floor($filamentVelocity*$chunkSeconds*100000)/100000
                if ($e -le 0 -or $e -gt $script:extrusionDistanceLimit) { throw 'Extrusion chunk outside bounded limits' }
                $command = 'G1 E{0} F{1}' -f $e.ToString('F5',[Globalization.CultureInfo]::InvariantCulture),$feed.ToString('F3',[Globalization.CultureInfo]::InvariantCulture)
                $commands.Add($command)
                $elapsed += $chunkSeconds
            }
            $sample = Get-Sample 'before_static_flow'
            Assert-ThermalState $sample
            if ($sample.temperature_c -lt 235) { throw 'Insufficient starting temperature for load checkout' }
            $commands.Add('M400')
            Send-MonitoredExtrusion ($commands -join "`n")
            Write-Event 'static_flow_end' @{nominal_extrusion_seconds=$elapsed; note='Continuous queued E-only segments; extrusion acceleration and firmware junction behavior still affect instantaneous flow'}
            Wait-Settled 'static_recovery_240' 240 90
        }
    }
    Write-Event 'session_complete' @{action=$Action}
} catch {
    $failure=$_
    try { Write-Event 'session_error' $failure.Exception.Message }
    catch { Write-Warning "Session error could not be recorded: $($failure.Exception.Message)" }
    Write-Output "SESSION ERROR: $($failure.Exception.Message)"
    throw $failure
} finally {
    $script:cleanupInProgress=$true
    try {
    if ($script:ownsHeater) {
        try { Send-Command "SET_HEATER_TEMPERATURE HEATER=$heaterName TARGET=0"; Write-Event 'heater_off' @{} }
        catch { $script:cleanupFailure=$true; Write-Event 'heater_cleanup_blocked' $_.Exception.Message; Write-Warning "Heater cleanup could not be verified: $($_.Exception.Message)" }
    }
    if ($script:responseStreaming) {
        try { Send-Command 'M400' } catch { $script:cleanupFailure=$true; Write-Event 'state_restore_blocked' 'Could not drain response queue before state restoration'; $script:extrusionStateSaved=$false }
    }
    if ($script:extrusionStateSaved) {
        try { Send-Command 'RESTORE_GCODE_STATE NAME=ORCA_FLOW_CALIBRATION MOVE=0' }
        catch { $script:cleanupFailure=$true; Write-Event 'state_restore_blocked' $_.Exception.Message; Write-Warning 'G-code state restore was blocked; inspect printer state.' }
    }
    Write-Output "Session evidence: $SessionDirectory"
    } finally {
        foreach ($writer in @($script:sampleWriter,$script:eventWriter)) {
            if ($null -ne $writer) {
                try { $writer.Dispose() } catch { $script:cleanupFailure=$true; Write-Warning "Calibration log close failed: $($_.Exception.Message)" }
            }
        }
        $script:sampleWriter=$null;$script:eventWriter=$null
    }
    if ($script:cleanupFailure -or $script:cleanupLogFailure) { throw 'Calibration cleanup or cleanup recording failed; inspect runner.log and printer state' }
}
