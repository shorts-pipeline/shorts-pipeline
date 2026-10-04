# Add a plain-text comment to a Jira Cloud issue (ADF body). Same auth as jira_transition_issue.ps1.
param(
    [Parameter(Mandatory = $true)][string]$IssueKey,
    [Parameter(Mandatory = $true)][string]$Text
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

# Split on double newlines into paragraphs; single newlines become hardBreak within a paragraph (ADF).
$blocks = $Text -split "`r?`n`r?`n", 0, "RegexMatch"
$content = @()
foreach ($block in $blocks) {
    $b = $block.Trim()
    if (-not $b) { continue }
    $lines = $b -split "`r?`n"
    $inline = [System.Collections.ArrayList]@()
    for ($i = 0; $i -lt $lines.Length; $i++) {
        $line = $lines[$i]
        if ($i -gt 0) { [void]$inline.Add(@{ type = "hardBreak" }) }
        if ($line.Length -gt 0) { [void]$inline.Add(@{ type = "text"; text = $line }) }
    }
    if ($inline.Count -eq 0) { continue }
    $content += @{ type = "paragraph"; content = @($inline.ToArray()) }
}
if ($content.Count -eq 0) { throw "Comment text is empty" }

$payload = @{ body = @{ type = "doc"; version = 1; content = $content } }
$json = $payload | ConvertTo-Json -Depth 20 -Compress
Invoke-RestMethod -Uri "$base/rest/api/3/issue/$IssueKey/comment" -Method Post -Headers $headers `
    -ContentType "application/json" -Body $json | Out-Null
Write-Output "OK: comment added to $IssueKey"
