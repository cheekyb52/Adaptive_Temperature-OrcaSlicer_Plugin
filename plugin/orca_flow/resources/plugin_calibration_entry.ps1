param([Parameter(Mandatory=$true)][string]$RequestFile)
# Stable process command line lets Orca persist its process permission grant.
# Each individual run still has a unique, immutable evidence directory.
$ErrorActionPreference='Stop'
$request=Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
if ($request.action -notin @('Position','Loaded','LoadedThermal') -or $request.physical_tool -lt 0 -or $request.physical_tool -gt 31) {
    throw 'Unsupported packaged calibration request'
}
$sessionsRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\sessions')).TrimEnd('\')+'\'
$session=[IO.Path]::GetFullPath([string]$request.session)
if (-not $session.StartsWith($sessionsRoot,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Session must be inside the plugin sessions directory'
}
$lock=[Threading.Mutex]::new($false,'Local\OrcaFlowU1Calibration')
$owned=$false
try {
    try { $owned=$lock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned=$true }
    if (-not $owned) { throw 'Another temperature calibration runner is still active' }
    $runner=Join-Path $PSScriptRoot 'run_moonraker_calibration.ps1'
    $arguments=@{Action=[string]$request.action;PrinterUrl=[string]$request.printer_url;PhysicalTool=[int]$request.physical_tool;
        HeaterObject=[string]$request.heater_name;FanObject=[string]$request.fan_name;ToolChangeStyle=[string]$request.tool_change_style;
        PositioningOrder=[string]$request.positioning_order;
        MinimumTemperature=[double]$request.min_temperature_c;MaximumTemperature=[double]$request.max_temperature_c;
        MaximumFlowMm3S=[double]$request.max_flow_mm3_s;FlowMm3S=[double]$request.flow_mm3_s;
        BaselineTemperature=[double]$request.baseline_c;PeakTemperature=[double]$request.peak_c;
        StabilityTolerancePercent=[double]$request.stability_tolerance_percent;
        SessionDirectory=$session;RequireFanOff=$true}
    if ($request.action -eq 'Loaded') { $arguments.ProbePlan=Join-Path $session 'input-plan.json' }
    & $runner @arguments
} finally {
    if ($owned) { $lock.ReleaseMutex() }
    $lock.Dispose()
}
