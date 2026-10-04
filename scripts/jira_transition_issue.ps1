# Transition a Jira Cloud issue (uses same creds as jira-mcp-launch.ps1 + jira-token.txt).
param(
    [Parameter(Mandatory = $true)][string]$IssueKey,
    [Parameter(Mandatory = $true)][string]$ToStatusName
)
$ErrorActionPreference = "Stop"
$launch = Join-Path $env:USERPROFILE "Documents\jira-mcp-launch.ps1"
$tokenPath = Join-Path $env:USERPROFILE "Documents\jira-token.txt"
$urlLine = Select-String -LiteralPath $launch -Pattern '^\$env:JIRA_URL = "([^"]+)"' | Select-Object -First 1
$mailLine = Select-String -LiteralPath $launch -Pattern '^\$env:JIRA_API_MAIL = "([^"]+)"' | Select-Object -First 1
if (-not $urlLine -or -not $mailLine) { throw "Could not parse JIRA_URL / JIRA_API_MAIL from $launch" }
$base = ($urlLine.Matches[0].Groups[1].Value).TrimEnd("/")
$email = $mailLine.Matches[0].Groups[1].Value
$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
$pair = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${email}:${token}"))
$headers = @{ Authorization = "Basic $pair"; Accept = "application/json" }

$trans = Invoke-RestMethod -Uri "$base/rest/api/3/issue/$IssueKey/transitions" -Headers $headers
$match = $trans.transitions | Where-Object { $_.name -eq $ToStatusName } | Select-Object -First 1
if (-not $match) {
    $names = ($trans.transitions | ForEach-Object { $_.name }) -join ", "
    throw "No transition named '$ToStatusName'. Available: $names"
}
$body = (@{ transition = @{ id = $match.id } } | ConvertTo-Json -Compress)
Invoke-RestMethod -Uri "$base/rest/api/3/issue/$IssueKey/transitions" -Method Post -Headers $headers `
    -ContentType "application/json" -Body $body | Out-Null
Write-Output "OK: $IssueKey -> $ToStatusName (transition id $($match.id))"
