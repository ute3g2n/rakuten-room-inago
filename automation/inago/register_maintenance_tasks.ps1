[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [switch]$Register,
    [string]$ProjectRoot = (Join-Path $PSScriptRoot "..\..")
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$pythonExe = Join-Path $projectRoot '.venv-inago\Scripts\python.exe'
$runtimeRoot = Join-Path $projectRoot 'rakuten-room-inago\runtime'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw 'The repository Python runtime is unavailable.'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($null -eq $identity.User) { throw 'The current Windows user identity is unavailable.' }
$userId = $identity.User.Value
$startBoundary = [DateTimeOffset]::Now.ToOffset([TimeSpan]::FromHours(9)).Date.AddDays(1).ToString('yyyy-MM-dd') + 'T00:00:00+09:00'
$namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'

function New-MaintenanceTaskXml([string]$TemplateName, [string]$Arguments) {
    [xml]$document = Get-Content -LiteralPath (Join-Path $PSScriptRoot "task-xml\$TemplateName") -Raw -Encoding UTF8
    $manager = New-Object System.Xml.XmlNamespaceManager($document.NameTable)
    $manager.AddNamespace('t', $namespace)
    $principal = $document.SelectSingleNode('//t:Principal/t:UserId', $manager)
    $exec = $document.SelectSingleNode('//t:Actions/t:Exec', $manager)
    $enabled = $document.SelectSingleNode('//t:Settings/t:Enabled', $manager)
    if ($null -eq $principal -or $null -eq $exec -or $null -eq $enabled) { throw 'The maintenance task template is incomplete.' }
    $principal.InnerText = $userId
    $enabled.InnerText = 'false'
    $exec.SelectSingleNode('t:Command', $manager).InnerText = $pythonExe
    $exec.SelectSingleNode('t:Arguments', $manager).InnerText = $Arguments
    $exec.SelectSingleNode('t:WorkingDirectory', $manager).InnerText = $projectRoot
    $boundary = $document.SelectSingleNode('//t:CalendarTrigger/t:StartBoundary', $manager)
    if ($null -ne $boundary) { $boundary.InnerText = $startBoundary }
    $declaration = $document.FirstChild
    if ($declaration -is [System.Xml.XmlDeclaration]) { $document.RemoveChild($declaration) | Out-Null }
    return $document.OuterXml
}

$watchdogArgs = "-B -m automation.inago.maintenance.cli --data-root `"$runtimeRoot`" watchdog"
$workerArgs = "-B -m automation.inago.maintenance.cli --data-root `"$runtimeRoot`" worker --once"
$tasks = @(
    @{ Name = 'RakutenRoomInagoWatchdog'; Template = 'RakutenRoomInagoWatchdog.xml'; Arguments = $watchdogArgs },
    @{ Name = 'RakutenRoomInagoWorker'; Template = 'RakutenRoomInagoWorker.xml'; Arguments = $workerArgs }
)
$rendered = foreach ($definition in $tasks) {
    $xml = New-MaintenanceTaskXml $definition.Template $definition.Arguments
    [pscustomobject]@{ TaskName = $definition.Name; Xml = $xml; Arguments = $definition.Arguments }
}

if (-not $Register) {
    [pscustomobject]@{
        Registered = $false
        StartBoundary = $startBoundary
        Tasks = @($rendered | ForEach-Object { @{ TaskName = $_.TaskName; Xml = $_.Xml } })
    } | ConvertTo-Json -Depth 6 -Compress
    return
}

$registeredNames = [System.Collections.Generic.List[string]]::new()
$collisions = [System.Collections.Generic.List[string]]::new()
foreach ($definition in $rendered) {
    $existing = Get-ScheduledTask -TaskName $definition.TaskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $collisions.Add($definition.TaskName)
    }
}
if ($collisions.Count -gt 0) {
    throw "Existing maintenance task(s) must be reviewed before registration: $($collisions -join ', ')"
}

Push-Location $projectRoot
try {
    $cliHelp = & $pythonExe -B -m automation.inago.maintenance.cli --help 2>&1
    $cliExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
$cliHelpText = $cliHelp | Out-String
if ($cliExitCode -ne 0 -or $cliHelpText -notmatch '(?m)^\s+worker\s') {
    throw 'The maintenance CLI does not expose the worker command; no tasks were registered.'
}

Push-Location $projectRoot
try {
    $workerHelp = & $pythonExe -B -m automation.inago.maintenance.cli `
        --data-root $runtimeRoot worker --help 2>&1
    $workerHelpExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
$workerHelpText = $workerHelp | Out-String
if ($workerHelpExitCode -ne 0 -or $workerHelpText -notmatch '(?m)^\s+--once(?:\s|,|$)') {
    throw 'The worker CLI does not support the scheduled --once option; no tasks were registered.'
}

$approvedDefinitions = [System.Collections.Generic.List[object]]::new()
foreach ($definition in $rendered) {
    if ($PSCmdlet.ShouldProcess($definition.TaskName, 'Register the reviewed maintenance task')) {
        $approvedDefinitions.Add($definition)
    }
}
if ($approvedDefinitions.Count -ne $rendered.Count) {
    [pscustomobject]@{
        Registered = $false
        TaskNames = @()
        PlannedTaskNames = @($tasks.Name)
        Reason = 'approval_not_granted_for_all_tasks'
    } | ConvertTo-Json -Compress
    return
}

try {
    foreach ($definition in $approvedDefinitions) {
        Register-ScheduledTask -TaskName $definition.TaskName -TaskPath '\' -Xml $definition.Xml -ErrorAction Stop | Out-Null
        $registeredNames.Add($definition.TaskName)
        $registered = Get-ScheduledTask -TaskName $definition.TaskName -TaskPath '\' -ErrorAction Stop
        if ($registered.Actions.Count -ne 1 -or
            [IO.Path]::GetFullPath($registered.Actions[0].Execute).ToLowerInvariant() -ne $pythonExe.ToLowerInvariant() -or
            $registered.Actions[0].Arguments -cne $definition.Arguments -or
            [IO.Path]::GetFullPath($registered.Actions[0].WorkingDirectory).ToLowerInvariant() -ne $projectRoot.ToLowerInvariant() -or
            $registered.Settings.MultipleInstances.ToString() -ne 'IgnoreNew' -or
            -not $registered.Settings.Hidden -or
            $registered.Principal.UserId -ne $userId -or
            $registered.Principal.LogonType.ToString() -ne 'Interactive' -or
            $registered.Principal.RunLevel.ToString() -ne 'Limited' -or
            $registered.Settings.Enabled -ne $false -or
            [string]$registered.Settings.ExecutionTimeLimit -notin @('PT0S', '00:00:00')) {
            throw "Task '$($definition.TaskName)' did not retain its reviewed action or safety settings."
        }
    }
}
catch {
    $registrationError = $_
    $rollbackFailures = [System.Collections.Generic.List[string]]::new()
    for ($index = $registeredNames.Count - 1; $index -ge 0; $index--) {
        $registeredName = $registeredNames[$index]
        try {
            Unregister-ScheduledTask -TaskName $registeredName -TaskPath '\' -Confirm:$false -ErrorAction Stop
        }
        catch {
            $rollbackFailures.Add($registeredName)
        }
    }
    if ($rollbackFailures.Count -gt 0) {
        throw "Task registration failed and rollback was incomplete for: $($rollbackFailures -join ', '). Original error: $registrationError"
    }
    throw $registrationError
}

[pscustomobject]@{
    Registered = $registeredNames.Count -eq $tasks.Count
    TaskNames = @($registeredNames)
    PlannedTaskNames = @($tasks.Name)
    Enabled = $false
} | ConvertTo-Json -Compress
