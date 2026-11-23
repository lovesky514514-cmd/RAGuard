param(
    [string]$Model = "qwen2.5:7b",
    [int]$Repeats = 3,
    [string]$Endpoint = "http://127.0.0.1:11434/v1/chat/completions"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"

if ($Repeats -lt 1 -or $Repeats -gt 20) {
    throw "Repeats must be in range 1..20"
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found; install Python 3.12 and add it to PATH"
}
if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw "Go not found; install Go 1.23 and add it to PATH"
}

$tag = ($Model -replace '[^a-zA-Z0-9._-]', '_')
$output = Join-Path $root ("results/real_llm_" + $tag)
Write-Host ("Real-model recovery experiment: " + $Model)
Write-Host ("Isolated Go sandbox; output: " + $output)
python -m bench.agent_replan_probe --mode model --endpoint $Endpoint --model $Model --repeats $Repeats --output $output
if ($LASTEXITCODE -ne 0) {
    throw "Experiment failed; inspect the output above and retain the complete error"
}
Write-Host ("Completed. Review episodes.csv and protocol.json under " + $output)
