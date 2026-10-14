[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [switch]$ExportOnly,
    [string]$OutputDir,
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $projectRoot 'rakuten-room-kai\orchestrator\automation-config.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.operation_start_date -ne '2026-09-25') {
    throw '開始日は2026-09-25である必要があります'
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot '.venv-kai\Scripts\python.exe'
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot 'rakuten-room-kai\implementation\task-xml'
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$commandXml = [System.Security.SecurityElement]::Escape($PythonPath)
$workDirXml = [System.Security.SecurityElement]::Escape($projectRoot)
$userXml = [System.Security.SecurityElement]::Escape($identity)

$tasks = @(
    @{ Name = 'RakutenRoomBakemonoKaiDaily'; Time = '13:00:00'; Entry = 'automation\run_daily.py --source scheduler' },
    @{ Name = 'RakutenRoomBakemonoKaiHealthCheck'; Time = '14:00:00'; Entry = 'automation\check_daily_health.py' }
)

foreach ($task in $tasks) {
    $start = "$($config.operation_start_date)T$($task.Time)"
    $argumentsXml = [System.Security.SecurityElement]::Escape($task.Entry)
    $xml = @"
<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Rakuten ROOM Bakemono Kai $($task.Name)</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$start</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$userXml</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>false</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><WakeToRun>true</WakeToRun><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>$commandXml</Command><Arguments>$argumentsXml</Arguments><WorkingDirectory>$workDirXml</WorkingDirectory></Exec></Actions>
</Task>
"@
    $file = Join-Path $OutputDir "$($task.Name).xml"
    if ($PSCmdlet.ShouldProcess($file, 'Export task XML')) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
        [System.IO.File]::WriteAllText($file, $xml, [System.Text.UTF8Encoding]::new($false))
        Write-Output "Exported=$file"
    }
    if (-not $ExportOnly) {
        if (-not $WhatIfPreference -and -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
            throw "固定Pythonが見つかりません: $PythonPath"
        }
        if ($PSCmdlet.ShouldProcess($task.Name, 'Register Windows scheduled task')) {
            # Task Scheduler parses PowerShell strings as UTF-16. A UTF-8 XML
            # declaration on that string is rejected even though the exported
            # file itself is correctly encoded as UTF-8.
            $registrationXml = [regex]::Replace($xml, '^\s*<\?xml[^>]*\?>\s*', '')
            Register-ScheduledTask -TaskName $task.Name -Xml $registrationXml -Force | Out-Null
            Write-Output "Registered=$($task.Name)"
        }
    }
}

if ([datetime]::Now.Date -gt [datetime]::Parse($config.operation_start_date)) {
    Write-Output 'InitialRunPolicy=StartWhenAvailable; 登録時刻が開始日後ならオンデマンド実行を別途確認してください'
} else {
    Write-Output 'InitialRunPolicy=StartBoundary 2026-09-25; StartWhenAvailable=true'
}
