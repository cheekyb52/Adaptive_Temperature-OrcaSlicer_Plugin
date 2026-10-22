param(
    [Parameter(Mandatory=$true)][string]$PrinterUrl,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$Heater = 'extruder2',
    [string]$Fan = 'fan_generic e2_fan',
    [ValidateRange(0.1,10)][double]$FilamentDiameterMm = 1.75,
    [ValidateRange(1,1800)][int]$Seconds = 10,
    [ValidateRange(100,10000)][int]$IntervalMs = 500
)
# Read-only Moonraker recorder. No control, file-write or G-code API requests.
$ErrorActionPreference = 'Stop'
$parsedUrl = [Uri]$PrinterUrl
if ($parsedUrl.Scheme -notin @('http','https') -or $parsedUrl.UserInfo -or $parsedUrl.Query -or $parsedUrl.Fragment) {
    throw 'Expected HTTP(S) printer URL without credentials/query/fragment'
}
$printerBase = $PrinterUrl.TrimEnd('/')
$heaterName = [Uri]::EscapeDataString($Heater)
$fanName = [Uri]::EscapeDataString($Fan)
$clock = [Diagnostics.Stopwatch]::StartNew()
$stream = [IO.File]::Open($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath), [IO.FileMode]::CreateNew)
$writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
try {
    while ($clock.Elapsed.TotalSeconds -lt $Seconds) {
        $state = Invoke-RestMethod -Uri "$printerBase/printer/objects/query?print_stats&virtual_sdcard&pause_resume" -TimeoutSec 5
        $s = $state.result.status
        if (-not $s.print_stats.state -or $null -eq $s.virtual_sdcard.is_active -or $null -eq $s.pause_resume.is_paused) {
            throw 'Cannot verify live print state; stopping dependent reads'
        }
        $response = Invoke-RestMethod -Uri "$printerBase/printer/objects/query?$heaterName&toolhead&$fanName&motion_report" -TimeoutSec 5
        $status = $response.result.status
        if ($null -eq $status.$Heater.temperature -or $null -eq $status.$Heater.target -or $null -eq $status.$Heater.power) {
            throw 'Incomplete heater telemetry'
        }
        $row = [ordered]@{
            time_s = $response.result.eventtime
            utc = [DateTime]::UtcNow.ToString('o')
            temperature_c = $status.$Heater.temperature
            target_c = $status.$Heater.target
            power_fraction = $status.$Heater.power
            fan_fraction = $status.$Fan.speed
            physical_heater = $Heater
            hotend_state = $status.$Heater.state
            active_extruder = $status.toolhead.extruder
            print_state = $s.print_stats.state
            active_job_or_pause = [bool]($s.virtual_sdcard.is_active -or $s.pause_resume.is_paused -or $s.print_stats.state -in @('printing','paused'))
            live_extruder_velocity_mm_s = $status.motion_report.live_extruder_velocity
            live_xyz_velocity_mm_s = $status.motion_report.live_velocity
            flow_mm3_s = if ($status.toolhead.extruder -eq $Heater -and $null -ne $status.motion_report.live_extruder_velocity) { [Math]::Max(0,[double]$status.motion_report.live_extruder_velocity)*[Math]::PI*[Math]::Pow($FilamentDiameterMm/2,2) } else { $null }
            flow_status = 'Motion-planner estimate from executing E velocity and configured filament diameter; not a delivered-filament sensor'
            file_position = $s.virtual_sdcard.file_position
            state_check_eventtime = $state.result.eventtime
            client_elapsed_s = $clock.Elapsed.TotalSeconds
        }
        $writer.WriteLine((ConvertTo-Json -InputObject $row -Compress -Depth 5))
        $writer.Flush()
        if ($clock.Elapsed.TotalSeconds -lt $Seconds) { Start-Sleep -Milliseconds $IntervalMs }
    }
} finally {
    $writer.Dispose()
    $clock.Stop()
}
