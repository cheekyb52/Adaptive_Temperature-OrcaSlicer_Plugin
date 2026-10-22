# SPDX-License-Identifier: GPL-3.0-only
# Pure geometry functions; dot-sourcing this file never contacts a printer.
function Get-CalibrationTarget($Sample) {
    $minimum=@($Sample.axis_minimum); $maximum=@($Sample.axis_maximum)
    if ($minimum.Count -lt 3 -or $maximum.Count -lt 3) { throw 'Missing reported travel limits' }
    for ($axis=0; $axis -lt 3; $axis++) {
        $lo=[double]$minimum[$axis]; $hi=[double]$maximum[$axis]
        if ([double]::IsNaN($lo) -or [double]::IsInfinity($lo) -or [double]::IsNaN($hi) -or [double]::IsInfinity($hi) -or $hi -le $lo) { throw 'Invalid reported travel limits' }
    }
    if (100 -lt $minimum[2] -or 100 -gt $maximum[2]) { throw 'Z100 mm is outside reported printer travel' }
    $centerX=([double]$minimum[0]+[double]$maximum[0])/2
    $centerY=([double]$minimum[1]+[double]$maximum[1])/2
    return @($centerX,$centerY,100.0)
}

function Convert-CalibrationCoordinate($Sample,$MachineTarget) {
    $machine=@($Sample.machine_position); $gcode=@($Sample.position)
    if ($machine.Count -lt 3 -or $gcode.Count -lt 3) { throw 'Missing coordinates for offset conversion' }
    $result=@()
    for ($axis=0;$axis -lt 3;$axis++) {
        $value=[double]$MachineTarget[$axis]+[double]$gcode[$axis]-[double]$machine[$axis]
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { throw 'Invalid coordinate conversion' }
        $result += $value.ToString('F3',[Globalization.CultureInfo]::InvariantCulture)
    }
    return $result
}

function Get-CalibrationClearanceTarget($Sample) {
    $null=Get-CalibrationTarget $Sample
    $machine=@($Sample.machine_position)
    if ($machine.Count -lt 3) { throw 'Missing machine position for Z clearance' }
    for ($axis=0;$axis -lt 3;$axis++) {
        $value=[double]$machine[$axis]
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { throw 'Invalid machine position for Z clearance' }
    }
    $height=[Math]::Max(100.0,[double]$machine[2])
    if ($height -gt [double]$Sample.axis_maximum[2]) { throw 'Z clearance is outside reported travel' }
    return @([double]$machine[0],[double]$machine[1],$height)
}

function Assert-CalibrationMachinePosition($Sample,$Expected) {
    if (@($Sample.machine_position).Count -lt 3 -or @($Expected).Count -lt 3 -or
        $Sample.homed_axes -notmatch 'x' -or $Sample.homed_axes -notmatch 'y' -or $Sample.homed_axes -notmatch 'z') {
        throw 'Homed axes and valid coordinates are required for calibration positioning'
    }
    for ($axis=0;$axis -lt 3;$axis++) {
        $actual=[double]$Sample.machine_position[$axis]
        if ([double]::IsNaN($actual) -or [double]::IsInfinity($actual) -or [Math]::Abs($actual-[double]$Expected[$axis]) -gt 0.5) {
            throw 'Calibration positioning leg did not reach its expected position; next leg blocked'
        }
    }
}

function Assert-CalibrationApproachPosition($Sample,$Expected) {
    if ($Sample.active_extruder -ne $heaterName -or ($Sample.hotend_state -and $Sample.hotend_state -ne 'ACTIVATE')) {
        throw 'Selected tool must remain active during calibration positioning'
    }
    Assert-CalibrationMachinePosition $Sample $Expected
}

function Move-CalibrationApproach($Sample,[ValidateSet('y_then_x','x_then_y')][string]$Order) {
    # Clear the load/dock area along the user-selected axis first. Each leg is a
    # separate command with M400 so firmware cannot blend an XY diagonal.
    if ($Order -notin @('y_then_x','x_then_y')) { throw 'Choose a positioning axis order before moving' }
    $target=Get-CalibrationTarget $Sample
    $clearance=Get-CalibrationClearanceTarget $Sample
    Assert-CalibrationApproachPosition $Sample $Sample.machine_position
    $coordinate=Convert-CalibrationCoordinate $Sample $clearance
    Send-Command "G90`nG1 Z$($coordinate[2]) F600`nM400"
    $current=Get-Sample 'positioning_z_clearance_verified'
    Assert-CalibrationApproachPosition $current $clearance

    $axes=if ($Order -eq 'y_then_x') { @(1,0) } else { @(0,1) }
    $legTarget=@($clearance)
    foreach ($axis in $axes) {
        $legTarget[$axis]=$target[$axis]
        $coordinate=Convert-CalibrationCoordinate $current $legTarget
        $letter=if ($axis -eq 0) { 'X' } else { 'Y' }
        Send-Command "G90`nG1 $letter$($coordinate[$axis]) F3000`nM400"
        $current=Get-Sample "positioning_${letter}_center_verified"
        Assert-CalibrationApproachPosition $current $legTarget
    }

    $coordinate=Convert-CalibrationCoordinate $current $target
    Send-Command "G90`nG1 Z$($coordinate[2]) F600`nM400"
    $ready=Get-Sample 'position_verified'
    Assert-CalibrationApproachPosition $ready $target
    Assert-TestPosition $ready
    Write-Event 'positioning_complete' @{route=$Order;machine_target=$target}
    Write-Output ($ready | ConvertTo-Json -Depth 6)
}
