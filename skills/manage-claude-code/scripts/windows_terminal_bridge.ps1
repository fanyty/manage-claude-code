param(
    [Parameter(Mandatory = $true)]
    [string]$ControlPath,
    [Parameter(Mandatory = $true)]
    [string]$StatusPath,
    [Parameter(Mandatory = $true)]
    [string]$BridgeToken
)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Codex managed Claude Code"

function Write-JsonAtomic {
    param([string]$Path, [hashtable]$Value)
    $temporary = "$Path.$PID.tmp"
    $Value | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$statusDirectory = Split-Path -Parent $StatusPath
New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
Write-JsonAtomic -Path $StatusPath -Value @{
    pid = $PID
    bridge_token = $BridgeToken
    state = "waiting"
    started_at = [DateTime]::UtcNow.ToString("o")
}

$lastAgentId = ""
Write-Host "Codex is managing this Claude Code window." -ForegroundColor Cyan
Write-Host "Keep this window open. Future task updates will reuse it." -ForegroundColor DarkGray

while ($true) {
    try {
        $control = Get-Content -LiteralPath $ControlPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $agentId = [string]$control.agent_id
        $claudeExecutable = [string]$control.claude_executable
        $title = [string]$control.title

        if ($agentId -and $claudeExecutable -and $agentId -ne $lastAgentId) {
            $lastAgentId = $agentId
            $Host.UI.RawUI.WindowTitle = "$title - Claude Code"
            Write-JsonAtomic -Path $StatusPath -Value @{
                pid = $PID
                bridge_token = $BridgeToken
                state = "attached"
                agent_id = $agentId
                updated_at = [DateTime]::UtcNow.ToString("o")
            }
            Write-Host ""
            Write-Host "Connecting to Claude Code task $agentId..." -ForegroundColor Cyan
            & $claudeExecutable attach $agentId
            Write-Host ""
            Write-Host "This Claude Code run has ended. Waiting for Codex to continue the task..." -ForegroundColor Yellow
            Write-JsonAtomic -Path $StatusPath -Value @{
                pid = $PID
                bridge_token = $BridgeToken
                state = "waiting"
                agent_id = $agentId
                updated_at = [DateTime]::UtcNow.ToString("o")
            }
        }
    }
    catch {
        Write-JsonAtomic -Path $StatusPath -Value @{
            pid = $PID
            bridge_token = $BridgeToken
            state = "warning"
            detail = $_.Exception.Message
            updated_at = [DateTime]::UtcNow.ToString("o")
        }
    }
    Start-Sleep -Milliseconds 500
}
