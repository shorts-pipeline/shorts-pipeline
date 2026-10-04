# Run JQL against Jira Cloud using enhanced search (POST /rest/api/3/search/jql).
# Legacy GET/POST /rest/api/3/search are removed on many sites — use this instead.
# Same auth as jira_transition_issue.ps1 (jira-mcp-launch.ps1 + jira-token.txt).
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Jql,
    [int]$MaxResults = 25,
    [string]$NextPageToken = "",
    [string[]]$Fields = @("summary", "status", "assignee", "priority", "issuetype", "created"),
    [switch]$RawJson
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

$bodyObj = [ordered]@{
    jql         = $Jql
    maxResults  = $MaxResults
    fields      = @($Fields)
}
if ($NextPageToken.Trim().Length -gt 0) {
    $bodyObj.nextPageToken = $NextPageToken.Trim()
}
$jsonBody = $bodyObj | ConvertTo-Json -Compress -Depth 6

$uri = "$base/rest/api/3/search/jql"
$resp = Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
    -ContentType "application/json; charset=utf-8" -Body $jsonBody

if ($RawJson) {
    $resp | ConvertTo-Json -Depth 20
    return
}

$issues = @($resp.issues)
if ($issues.Count -eq 0) {
    Write-Output "(no issues)"
    return
}
foreach ($issue in $issues) {
    $key = [string]$issue.key
    $f = $issue.fields
    $sum = if ($null -ne $f.summary) { [string]$f.summary } else { "" }
    $st = ""
    if ($null -ne $f.status) { $st = [string]$f.status.name }
    $as = "(unassigned)"
    if ($null -ne $f.assignee) {
        if ($null -ne $f.assignee.displayName -and [string]$f.assignee.displayName) {
            $as = [string]$f.assignee.displayName
        }
        elseif ($null -ne $f.assignee.name -and [string]$f.assignee.name) {
            $as = [string]$f.assignee.name
        }
    }
    Write-Output ("{0,-12} {1,-16} {2,-24} {3}" -f $key, $st, $as, $sum)
}
if ($resp.nextPageToken) {
    Write-Output ""
    Write-Output "More results: re-run with -NextPageToken $($resp.nextPageToken)"
}
