param([Parameter(Mandatory = $true)][ValidatePattern('^tunnel_[A-Za-z0-9_-]+$')][string]$TunnelId)

$ErrorActionPreference = 'Stop'
$tunnelClient = Join-Path $PSScriptRoot 'tools\tunnel-client\tunnel-client.exe'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'mcp_server.py'
$profileDir = Join-Path $PSScriptRoot '.tunnel-profiles'
if (-not (Test-Path -LiteralPath $tunnelClient)) { throw 'Install the official tunnel-client in tools\tunnel-client first.' }
if (-not $env:CONTROL_PLANE_API_KEY) { throw 'Set CONTROL_PLANE_API_KEY in this PowerShell session first. Do not paste the key into chat.' }
if (Test-Path -LiteralPath $profileDir) { throw 'Existing profile directory found. Reuse it with tunnel-client run --profile-dir .tunnel-profiles --profile wechat-assistant.' }

# tunnel-client parses a shell-style command; forward slashes preserve Windows paths.
$mcpCommand = '"' + $pythonPath.Replace('\', '/') + '" "' + $serverPath.Replace('\', '/') + '"'
& $tunnelClient init --sample sample_mcp_stdio_local --profile-dir $profileDir --profile wechat-assistant --tunnel-id $TunnelId --mcp-command $mcpCommand --health-listen-addr '127.0.0.1:18766'
if ($LASTEXITCODE -ne 0) { throw 'Tunnel initialization failed.' }
& $tunnelClient doctor --profile-dir $profileDir --profile wechat-assistant --explain
if ($LASTEXITCODE -ne 0) { throw 'Tunnel diagnosis failed. Check the runtime key, tunnel/workspace association and permissions.' }
& $tunnelClient run --profile-dir $profileDir --profile wechat-assistant
if ($LASTEXITCODE -ne 0) { throw 'Tunnel stopped with an error.' }
