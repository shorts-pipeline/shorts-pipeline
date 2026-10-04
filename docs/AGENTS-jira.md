# Jira — agent workflow (LEW / backlog)

This project may use **Atlassian Jira Cloud** for bugs and features. Agents and humans should follow the same conventions so the board stays truthful and searchable.

## Credentials and tools (local)

- **API token** and site URL live outside the repo (for example `Documents\jira-token.txt` and `Documents\jira-mcp-launch.ps1` on your machine). Do not commit tokens or paste them into chat.
- **Cursor MCP**: a Jira MCP server (for example `@mcp-devtools/jira`) may appear as **`user-jira`** in the IDE. Tools vary by package; not every operation is exposed as an MCP tool.
- **REST API** (reliable for transitions and comments): repo scripts under `scripts/`:
  - `scripts/jira_transition_issue.ps1` — move an issue to a named status (for example **Done**).
  - `scripts/jira_add_comment.ps1` — add an issue **comment** (Atlassian Document Format), used for suggested handling and completion notes.
  - `scripts/jira_query_jql.ps1` — run **JQL** via Jira Cloud **enhanced search** (`POST /rest/api/3/search/jql`). Use this when the IDE Jira MCP still calls removed `/rest/api/3/search` endpoints.

Example (from repo root, PowerShell):

```powershell
.\scripts\jira_query_jql.ps1 -Jql 'project in (LEW, FOW) AND resolution = Unresolved ORDER BY rank ASC' -MaxResults 20

# Full JSON (e.g. for jq); next page when API returns nextPageToken:
.\scripts\jira_query_jql.ps1 -Jql 'project = LEW AND status != Done' -MaxResults 50 -RawJson
.\scripts\jira_query_jql.ps1 -Jql 'project = LEW AND status != Done' -MaxResults 50 -NextPageToken '<token-from-previous-raw-json>'
```

```powershell
.\scripts\jira_add_comment.ps1 -IssueKey "LEW-12" -Text @"
**Suggested handling:** Plan only — decide policy in docs before coding.

**Rationale:** Touches release cadence and operator workflow; needs owner sign-off.
"@

.\scripts\jira_transition_issue.ps1 -IssueKey "LEW-12" -ToStatusName "Done"
```

If `Done` is not the exact transition name on your board, run transitions once without posting to discover names (the transition script lists available names on error).

## Required conventions for agents

### 1. Suggested handling → **comment on the ticket**

When you triage a ticket (before or while implementing), add a **Jira comment** that records **suggested handling** so the history stays on the issue:

- One short block is enough, for example:
  - **Suggested handling:** Fix in repo / Plan only / Spike or experiment / Research then optional follow-up / Split into sub-tasks.
  - **Scope / files:** Optional bullets (which area of the pipeline, risk notes).
  - **Out of scope:** Optional one line.

This is for **historical purposes** (why we chose a path, what we deferred). Do not rely only on chat or local notes.

Use `scripts/jira_add_comment.ps1` or the Jira UI if the MCP comment tool is unavailable.

### 2. When the work is finished → **set status to Done**

When the ticket is actually **fixed or delivered** (code merged, or explicit “won’t do” with resolution recorded in a comment):

- Transition the issue to **Done** (or your board’s equivalent terminal column), for example:

```powershell
.\scripts\jira_transition_issue.ps1 -IssueKey "LEW-12" -ToStatusName "Done"
```

Do not leave implemented work in **To Do** / **In Progress** without a reason documented in a comment.

### 3. Push the implementation to **GitHub** with the **ticket id**

After (or as part of) closing the loop on a ticket:

1. **Commit** the real code/doc changes for that ticket with the Jira **issue key in the subject line**, for example:
   - `LEW-5: Show phase1-dialogue prompts in Pipeline UI preview`
   - `LEW-12: Fix talking-head silence merge for FAL segments`
2. **Push** to `origin` on the branch you use for work (often `master` or a feature branch). The issue key must appear in the **commit message** so history and GitHub search tie back to Jira.

If multiple tickets ship in one commit (rare), list keys: `LEW-4, LEW-9: …`. If there is genuinely no Jira key (repo-only chore), use `chore:` / `docs:` and explain in the body—do not invent a fake key.

### 4. If you cannot access Jira

- Note in the PR or internal summary that Jira was not updated, and ask the owner to transition/comment—or paste the exact comment text for them to add. Still **push** GitHub commits with the ticket id when the owner later assigns a key, or record “no Jira” in the commit body.

## MCP and JQL (enhanced search)

Atlassian has **removed** the legacy issue search resources (`GET`/`POST` `/rest/api/3/search` with `jql` in the old shape). Agents and tools must use **enhanced search**:

- **`POST /rest/api/3/search/jql`** — JSON body includes `jql`, `maxResults`, optional `fields`, optional **`nextPageToken`** for pagination (not `startAt`).
- **`GET /rest/api/3/search/jql`** — query parameters for the same search (see [Issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/) in the Jira Cloud REST v3 docs).

**In this repo:** use `scripts/jira_query_jql.ps1` (same credentials as the other Jira scripts).

**If you maintain a Jira MCP server:** update its `execute_jql` (or equivalent) implementation to call **`POST {JIRA_URL}/rest/api/3/search/jql`** with a JSON body; do not call deprecated `/rest/api/3/search`. The Cursor **`user-jira`** MCP in this workspace only exposes JSON tool descriptors — the runnable server lives outside the repo and must be patched in that package or fork.

## Link from repo index

See **[AGENTS.md](../AGENTS.md)** (“Where to read next”) for the pointer to this file.
