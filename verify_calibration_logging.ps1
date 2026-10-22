# SPDX-License-Identifier: GPL-3.0-only
param([Parameter(Mandatory=$true)][string]$SessionDirectory,[int]$Count=1500)
# Offline only: write local telemetry while Python exercises the real UI reader.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'calibration_logging.ps1')
$sampleWriter=$null;$eventWriter=$null
try {
    $sampleWriter=New-CalibrationJsonlWriter (Join-Path $SessionDirectory 'telemetry.jsonl')
    $eventWriter=New-CalibrationJsonlWriter (Join-Path $SessionDirectory 'events.jsonl')
    $eventWriter.WriteLine('{"event":"session_start"}')
    for ($i=0;$i -lt $Count;$i++) {
        $row=@{time_s=$i*.5;stage='loaded_temperature_flow';response_phase='heating';temperature_c=220+$i%10;
            target_c=230;power_fraction=.5;flow_mm3_s=16;index=$i;label=('UTF8 calibration '+[char]0x00b0)}
        $sampleWriter.WriteLine(($row | ConvertTo-Json -Compress))
        if ($i%100 -eq 0) { $eventWriter.WriteLine((@{event='progress';index=$i} | ConvertTo-Json -Compress)) }
        if ($i%10 -eq 0) { Start-Sleep -Milliseconds 2 }
    }
    $eventWriter.WriteLine('{"event":"session_complete"}')
} finally {
    if ($null -ne $sampleWriter) { $sampleWriter.Dispose() }
    if ($null -ne $eventWriter) { $eventWriter.Dispose() }
}

# Extract real event/control functions, never the runner's executable body.
$errors=$null;$tokens=$null
$tree=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'run_moonraker_calibration.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($definition in $tree.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Write-Event','Get-VerifiedState','Send-Command')},$true)) {
    Invoke-Expression $definition.Extent.Text
}
$script:eventWriter=$eventWriter # deliberately disposed: simulate a broken log
$script:cleanupInProgress=$false
$printerBase='http://offline.invalid';$script:posts=0
function Invoke-RestMethod {
    param($Uri,$TimeoutSec,$Method,$ContentType,$Body)
    if ($Method -eq 'Post') { $script:posts++;return @{result='ok'} }
    return @{result=@{status=@{print_stats=@{state='standby'};virtual_sdcard=@{is_active=$false};pause_resume=@{is_paused=$false}}}}
}
$blocked=$false
try { Send-Command 'G1 E1 F100' | Out-Null } catch { $blocked=$true }
if (-not $blocked -or $script:posts -ne 0) { throw 'Lost event logging must block normal controls' }
$script:cleanupInProgress=$true
Send-Command 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0' | Out-Null
Send-Command 'RESTORE_GCODE_STATE NAME=ORCA_FLOW_CALIBRATION MOVE=0' | Out-Null
if ($script:posts -ne 2) { throw 'A logging failure must not prevent heater shutdown/state restoration' }
# Exercise the actual outer cleanup block with the broken event writer: all
# controls must be attempted, but the run must not be reported as successful.
$mainTry=@($tree.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] })
if ($mainTry.Count -ne 1) { throw 'Expected one top-level calibration try/finally' }
$script:ownsHeater=$true;$heaterName='extruder';$script:responseStreaming=$true;$script:extrusionStateSaved=$true
$script:sampleWriter=$null;$script:cleanupFailure=$false;$script:cleanupLogFailure=$false
$failed=$false
try { Invoke-Expression (($mainTry[0].Finally.Statements | ForEach-Object { $_.Extent.Text }) -join "`n") | Out-Null }
catch { $failed=$_.Exception.Message -match 'cleanup or cleanup recording failed' }
if (-not $failed -or $script:posts -ne 5 -or $null -ne $script:eventWriter) { throw 'Cleanup must finish controls, dispose logs and report lost evidence as a failure' }
Write-Output 'Shared telemetry writer and cleanup with broken event log: passed'
