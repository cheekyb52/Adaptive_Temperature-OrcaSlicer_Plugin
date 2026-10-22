param(
    [string]$Python = 'python',
    [string]$Chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
)
# Offline documentation rendering only. No plugin installation or printer access.
$guideRoot = Split-Path -Parent $PSScriptRoot
$guideReview = Join-Path $guideRoot '.build\guide'
Push-Location -LiteralPath $guideRoot
try {
    & $Python 'docs/build_user_guide.py'
    if ($LASTEXITCODE -ne 0) { throw 'Guide preparation failed' }
    $captureList = Get-Content -LiteralPath (Join-Path $guideReview 'captures.json') -Raw | ConvertFrom-Json
    foreach ($captureItem in $captureList) {
        $captureProfile = Join-Path $guideReview ('chrome-' + $captureItem.name)
        $captureLog = Join-Path $guideReview ($captureItem.name + '.log')
        $captureDom = Join-Path $guideReview ($captureItem.name + '.dom.html')
        & $Chrome --headless --no-sandbox --disable-gpu --disable-background-networking --no-first-run --no-default-browser-check --force-dark-mode "--user-data-dir=$captureProfile" "--window-size=$($captureItem.width),$($captureItem.height)" --hide-scrollbars --virtual-time-budget=1000 "--screenshot=$($captureItem.image)" --dump-dom $captureItem.url 2> $captureLog | Set-Content -LiteralPath $captureDom -Encoding UTF8
        $rendered = Get-Content -LiteralPath $captureDom -Raw
        if ($rendered -notmatch 'data-capture-ready="true"' -or $rendered -match 'data-capture-error=') {
            throw "UI capture failed: $($captureItem.name)"
        }
        $contentHeight = [int]([regex]::Match($rendered, 'data-content-height="(\d+)"').Groups[1].Value)
        if ($contentHeight -gt $captureItem.height) { throw "Capture is clipped: $($captureItem.name)" }
        Write-Output "Captured $($captureItem.name)"
    }
    & $Python 'docs/build_user_guide.py' --print-source
    if ($LASTEXITCODE -ne 0) { throw 'Print source preparation failed' }
    $guidePdf = Join-Path $guideRoot 'docs\Adaptive Temperature Guide\Adaptive Temperature User Guide.pdf'
    $guideProfile = Join-Path $guideReview 'chrome-pdf'
    $printUri = ([Uri](Join-Path $guideReview 'print.html')).AbsoluteUri
    & $Chrome --headless --no-sandbox --disable-gpu --disable-background-networking --no-first-run --no-default-browser-check "--user-data-dir=$guideProfile" --no-pdf-header-footer --virtual-time-budget=1000 "--print-to-pdf=$guidePdf" $printUri 2> (Join-Path $guideReview 'pdf.log') | Out-Null
    & $Python 'docs/build_user_guide.py' --package
    if ($LASTEXITCODE -ne 0) { throw 'Guide packaging failed' }
}
finally { Pop-Location }
