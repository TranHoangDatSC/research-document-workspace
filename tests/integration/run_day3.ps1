# Run from PowerShell. Deletes only Day 3 test documents, not your documents.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $projectRoot
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program $($Arguments -join ' ')" }
}
Invoke-Checked 'docker' @('compose', 'up', '-d', '--wait', '--wait-timeout', '180')
Invoke-Checked 'python' @('tests/integration/day2_test.py', 'before')
Invoke-Checked 'python' @('tests/integration/day3_test.py', 'before')
try {
    Invoke-Checked 'docker' @('compose', 'stop', 'mongo')
    Invoke-Checked 'python' @('tests/integration/day3_test.py', 'failure')
} finally {
    Invoke-Checked 'docker' @('compose', 'start', 'mongo')
    Invoke-Checked 'docker' @('compose', 'up', '-d', '--wait', '--wait-timeout', '180')
}
Invoke-Checked 'python' @('tests/integration/day3_test.py', 'recovery')
# Keep named volumes. Never add -v to this command.
Invoke-Checked 'docker' @('compose', 'down')
Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', '--wait', '--wait-timeout', '180')
Invoke-Checked 'python' @('tests/integration/day2_test.py', 'after')
Invoke-Checked 'python' @('tests/integration/day3_test.py', 'after')
$evidence = 'docs/evidence/day-03'
New-Item -ItemType Directory -Force $evidence | Out-Null
Copy-Item 'artifacts/day-03/*-result.txt' $evidence
Copy-Item 'artifacts/day-02/day-02-before-result.txt' $evidence
Copy-Item 'artifacts/day-02/day-02-after-result.txt' $evidence
$composeOutput = & docker compose ps
if ($LASTEXITCODE -ne 0) { throw 'Cannot collect Compose status.' }
$composeOutput | Out-File -Encoding utf8 "$evidence/compose-ps.txt"
$tracked = & git ls-files
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git tracked files.' }
$unwanted = $tracked | Select-String -Pattern '(^|/)\.env$|(^|/)\.venv/|(^|/)__pycache__/|\.pyc$'
if ($unwanted) { $unwanted; throw 'Environment/cache files are tracked by Git.' }
Write-Host 'DAY 3 AUTOMATED CHECKS: PASS'
Write-Host 'Open http://127.0.0.1:8001/ and complete the manual checklist in docs/day-03.md.'
