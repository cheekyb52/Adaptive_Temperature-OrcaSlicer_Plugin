# SPDX-License-Identifier: GPL-3.0-only
# Offline only: pure coordinate functions and syntax parsing, no printer I/O.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'calibration_geometry.ps1')
foreach ($name in @('calibration_geometry.ps1','run_moonraker_calibration.ps1','plugin\orca_flow\resources\plugin_calibration_entry.ps1')) {
    $parseErrors=$null; $parseTokens=$null
    $null=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$parseTokens,[ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
$sample=[pscustomobject]@{axis_minimum=@(-50,10,0);axis_maximum=@(250,310,250);machine_position=@(100,160,100);position=@(105,158,110)}
$target=Get-CalibrationTarget $sample
if ($target[0] -ne 100 -or $target[1] -ne 160 -or $target[2] -ne 100) { throw 'Travel midpoint failed' }
$converted=Convert-CalibrationCoordinate $sample $target
if ($converted[0] -ne '105.000' -or $converted[1] -ne '158.000' -or $converted[2] -ne '110.000') { throw 'G-code offset conversion failed' }
$sample.axis_maximum=@(250,310,90)
$refused=$false
try { $null=Get-CalibrationTarget $sample } catch { $refused=$true }
if (-not $refused) { throw 'Z100 outside travel must be refused' }
$sample.axis_maximum=@(-50,310,250)
$refused=$false
try { $null=Get-CalibrationTarget $sample } catch { $refused=$true }
if (-not $refused) { throw 'Degenerate travel must be refused' }
# Load only two function definitions, never the runner's executable body.
$parseErrors=$null;$parseTokens=$null
$tree=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'run_moonraker_calibration.ps1'),[ref]$parseTokens,[ref]$parseErrors)
foreach ($definition in $tree.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Get-VerifiedState','Send-Command')},$true)) {
    Invoke-Expression $definition.Extent.Text
}
$printerBase='http://offline.invalid'
$script:postCount=0
function Write-Event($Name,$Data) {}
function Invoke-RestMethod {
    param($Uri,$TimeoutSec,$Method,$ContentType,$Body)
    if ($Method -eq 'Post') { $script:postCount++; return @{result='ok'} }
    return @{result=@{status=@{print_stats=@{state=$script:fakeState};virtual_sdcard=@{is_active=$script:fakeSd};pause_resume=@{is_paused=$script:fakePause}}}}
}
foreach ($case in @(@('printing',$true,$false),@('paused',$false,$true),@('standby',$true,$false))) {
    $script:fakeState=$case[0];$script:fakeSd=$case[1];$script:fakePause=$case[2]
    $blocked=$false
    try { Send-Command 'G1 X1' } catch { $blocked=$true }
    if (-not $blocked -or $script:postCount -ne 0) { throw 'An active/paused/SD job allowed a control POST' }
}
Write-Output 'Calibration geometry, runner syntax and idle-state control guards: passed'

# Exercise the actual positioning routine against a fake machine. No real
# runner body is executed and all command/network functions are mocked.
$heaterName='extruder';$PhysicalTool=0
$script:routeCommands=[Collections.Generic.List[string]]::new()
function Reset-PositioningSimulation([double]$Height=80.25) {
    $script:routeCommands.Clear()
    $script:fakeMachine=@(35.0,300.0,$Height)
    $script:fakeOffset=@(5.0,-2.0,10.0)
    $script:failedAxis='';$script:wrongTool=$false
}
function Get-Sample($Stage) {
    $sample=[pscustomobject]@{axis_minimum=@(-50,10,0);axis_maximum=@(250,310,250);
        machine_position=@($script:fakeMachine);position=@(0.0,0.0,0.0);
        active_extruder='extruder';hotend_state='ACTIVATE';homed_axes='xyz'}
    for ($axis=0;$axis -lt 3;$axis++) { $sample.position[$axis]=$script:fakeMachine[$axis]+$script:fakeOffset[$axis] }
    if ($script:failedAxis -and $Stage -eq "positioning_$($script:failedAxis)_center_verified") { $sample.machine_position[2]+=2 }
    if ($script:wrongTool -and $Stage -eq 'positioning_Y_center_verified') { $sample.active_extruder='extruder1' }
    return $sample
}
function Send-Command([string]$Command) {
    if (-not $Command.EndsWith("`nM400")) { throw 'Every positioning leg must complete before another begins' }
    $lines=@($Command.Split("`n") | Where-Object { $_ -match '^G1 ' })
    if ($lines.Count -ne 1 -or $lines[0] -notmatch '^G1 ([XYZ])(-?[0-9]+\.[0-9]+) F(600|3000)$') { throw 'Positioning must move exactly one axis at a time' }
    $letter=$Matches[1];$coordinate=[double]::Parse($Matches[2],[Globalization.CultureInfo]::InvariantCulture)
    $index=@{X=0;Y=1;Z=2}[$letter]
    $script:fakeMachine[$index]=$coordinate-$script:fakeOffset[$index]
    $script:routeCommands.Add($letter)
}
function Assert-TestPosition($Sample) {
    $target=Get-CalibrationTarget $Sample
    Assert-CalibrationApproachPosition $Sample $target
}
foreach ($order in @('y_then_x','x_then_y')) {
    Reset-PositioningSimulation
    Move-CalibrationApproach (Get-Sample 'selected') $order | Out-Null
    $expected=if ($order -eq 'y_then_x') { 'ZYXZ' } else { 'ZXYZ' }
    if (($script:routeCommands -join '') -ne $expected) { throw "Incorrect positioning order: $order" }
    if ($script:fakeMachine[0] -ne 100 -or $script:fakeMachine[1] -ne 160 -or $script:fakeMachine[2] -ne 100) { throw 'Positioning did not account for G-code offsets' }
    Reset-PositioningSimulation
    $script:failedAxis=if ($order -eq 'y_then_x') { 'Y' } else { 'X' }
    $blocked=$false
    try { Move-CalibrationApproach (Get-Sample 'selected') $order | Out-Null } catch { $blocked=$_.Exception.Message -match 'next leg blocked' }
    if (-not $blocked -or $script:routeCommands.Count -ne 2) { throw 'A failed first-axis move must block the second axis' }
}
Reset-PositioningSimulation 145.25
$clearance=Get-CalibrationClearanceTarget (Get-Sample 'selected')
if ($clearance[2] -ne 145.25) { throw 'Clearance must preserve existing higher Z and fractional coordinates' }
Reset-PositioningSimulation
$script:wrongTool=$true
$blocked=$false
try { Move-CalibrationApproach (Get-Sample 'selected') 'y_then_x' | Out-Null } catch { $blocked=$_.Exception.Message -match 'remain active' }
if (-not $blocked -or $script:routeCommands.Count -ne 2) { throw 'Unexpected tool change must block remaining movement' }
Write-Output 'Selectable positioning priority, single-axis motion barriers, offsets and failed-leg control guards: passed'
