# SPDX-License-Identifier: GPL-3.0-only
# Definitions only. A writer is opened explicitly by a calibration session.
function New-CalibrationJsonlWriter([string]$Path) {
    # Keep one write-only append handle for the session. Add-Content repeatedly
    # opens/probes encoding and can fail while another process tails the file.
    # Explicit sharing allows the UI to read without reopening the writer.
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Append,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
    try {
        $writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false))
        $writer.AutoFlush=$true
        return $writer
    } catch {
        $stream.Dispose()
        throw
    }
}
