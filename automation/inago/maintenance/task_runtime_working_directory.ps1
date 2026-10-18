[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Read', 'Set', 'Release')]
    [string]$Mode,

    [Parameter(Mandatory = $false)]
    [string]$TargetWorkingDirectory,

    [Parameter(Mandatory = $false)]
    [string]$OriginalEnabledJson
)

$ErrorActionPreference = 'Stop'
$taskNames = @(
    'RakutenRoomInagoDaily',
    'RakutenRoomInagoWorker',
    'RakutenRoomInagoWatchdog',
    'RakutenRoomInagoHumanApi'
)
$switchTaskNames = @(
    'RakutenRoomInagoDaily',
    'RakutenRoomInagoWorker',
    'RakutenRoomInagoWatchdog'
)

function Get-TaskSnapshot {
    param([string[]]$Names)

    $records = @()
    foreach ($name in $Names) {
        $task = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction Stop
        $actions = @($task.Actions)
        if ($actions.Count -ne 1) {
            throw 'task_action_count_invalid'
        }
        $action = $actions[0]
        $records += [pscustomobject]@{
            TaskName = $name
            State = $task.State.ToString()
            Enabled = [bool]$task.Settings.Enabled
            Execute = [string]$action.Execute
            Arguments = [string]$action.Arguments
            WorkingDirectory = [string]$action.WorkingDirectory
        }
    }
    return ,$records
}

function Set-TaskWorkingDirectory {
    param(
        [string]$Name,
        [string]$Execute,
        [string]$Arguments,
        [string]$WorkingDirectory
    )

    $action = New-ScheduledTaskAction `
        -Execute $Execute `
        -Argument $Arguments `
        -WorkingDirectory $WorkingDirectory
    Set-ScheduledTask `
        -TaskName $Name `
        -TaskPath '\' `
        -Action $action `
        -ErrorAction Stop | Out-Null
}

function Test-SameDirectory {
    param(
        [string]$Left,
        [string]$Right
    )

    if ([string]::IsNullOrWhiteSpace($Left) -or
        [string]::IsNullOrWhiteSpace($Right)) {
        return $false
    }
    try {
        $leftFull = [System.IO.Path]::GetFullPath($Left)
        $rightFull = [System.IO.Path]::GetFullPath($Right)
        $leftRoot = [System.IO.Path]::GetPathRoot($leftFull)
        $rightRoot = [System.IO.Path]::GetPathRoot($rightFull)
        if ($leftFull.Length -gt $leftRoot.Length) {
            $leftFull = $leftFull.TrimEnd([char[]]@('\', '/'))
        }
        if ($rightFull.Length -gt $rightRoot.Length) {
            $rightFull = $rightFull.TrimEnd([char[]]@('\', '/'))
        }
        return [string]::Equals(
            $leftFull,
            $rightFull,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    }
    catch {
        return $false
    }
}

function Set-TaskEnabledState {
    param(
        [string]$Name,
        [bool]$Enabled
    )

    if ($Enabled) {
        Enable-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop | Out-Null
    }
    else {
        Disable-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop | Out-Null
    }
}

function Read-OriginalEnabledStates {
    param([string]$Json)

    try {
        $document = ConvertFrom-Json -InputObject $Json -ErrorAction Stop
    }
    catch {
        throw 'runtime_original_task_state_invalid'
    }
    if ($null -eq $document -or $document -isnot [pscustomobject]) {
        throw 'runtime_original_task_state_invalid'
    }

    $states = @{}
    foreach ($name in $taskNames) {
        $property = $document.PSObject.Properties[$name]
        if ($null -eq $property -or $property.Value -isnot [bool]) {
            throw 'runtime_original_task_state_invalid'
        }
        $states[$name] = [bool]$property.Value
    }
    $propertyCount = @($document.PSObject.Properties).Count
    if ($propertyCount -ne $taskNames.Count) {
        throw 'runtime_original_task_state_invalid'
    }
    return $states
}

function Assert-TransitionFence {
    param(
        [object[]]$Snapshot,
        [hashtable]$OriginalEnabled
    )

    foreach ($task in $Snapshot) {
        if ($task.Enabled) {
            throw 'runtime_task_transition_fence_missing'
        }
        if ($task.TaskName -eq 'RakutenRoomInagoWorker' -and
            $task.State -eq 'Queued') {
            throw 'runtime_task_busy'
        }
        if ($task.TaskName -ne 'RakutenRoomInagoWorker' -and
            $task.State -in @('Running', 'Queued')) {
            throw 'runtime_task_busy'
        }
    }
    foreach ($name in $taskNames) {
        if (-not $OriginalEnabled.ContainsKey($name)) {
            throw 'runtime_original_task_state_invalid'
        }
    }
}

try {
    if ($Mode -eq 'Read') {
        $snapshot = Get-TaskSnapshot -Names $taskNames
        [pscustomobject]@{ status = 'ok'; tasks = $snapshot } |
            ConvertTo-Json -Depth 4 -Compress
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($TargetWorkingDirectory) -or
        -not [System.IO.Path]::IsPathRooted($TargetWorkingDirectory) -or
        -not (Test-Path -LiteralPath $TargetWorkingDirectory -PathType Container)) {
        throw 'target_working_directory_invalid'
    }

    $originalEnabled = Read-OriginalEnabledStates -Json $OriginalEnabledJson
    if ($Mode -eq 'Release') {
        $beforeRelease = Get-TaskSnapshot -Names $taskNames
        foreach ($name in $switchTaskNames) {
            $task = $beforeRelease | Where-Object { $_.TaskName -eq $name }
            if (-not (Test-SameDirectory $task.WorkingDirectory $TargetWorkingDirectory)) {
                throw 'runtime_task_release_root_mismatch'
            }
        }
        $alreadyReleased = $true
        foreach ($task in $beforeRelease) {
            if ($task.Enabled -and -not $originalEnabled[$task.TaskName]) {
                throw 'runtime_task_state_changed'
            }
            if (($task.TaskName -eq 'RakutenRoomInagoWorker' -and
                    $task.State -eq 'Queued') -or
                ($task.TaskName -ne 'RakutenRoomInagoWorker' -and
                    $task.State -in @('Running', 'Queued'))) {
                throw 'runtime_task_busy'
            }
            if ($task.Enabled -ne $originalEnabled[$task.TaskName]) {
                $alreadyReleased = $false
            }
        }
        if ($alreadyReleased) {
            [pscustomobject]@{ status = 'ok'; tasks = $beforeRelease } |
                ConvertTo-Json -Depth 4 -Compress
            exit 0
        }
        try {
            foreach ($task in $beforeRelease) {
                if ($task.Enabled) {
                    Set-TaskEnabledState -Name $task.TaskName -Enabled $false
                }
            }
            $beforeRelease = Get-TaskSnapshot -Names $taskNames
            Assert-TransitionFence -Snapshot $beforeRelease -OriginalEnabled $originalEnabled
            foreach ($name in $taskNames) {
                Set-TaskEnabledState -Name $name -Enabled $originalEnabled[$name]
            }
            $afterRelease = Get-TaskSnapshot -Names $taskNames
            foreach ($task in $afterRelease) {
                if ($task.Enabled -ne $originalEnabled[$task.TaskName]) {
                    throw 'runtime_task_release_readback_mismatch'
                }
            }
            foreach ($name in $switchTaskNames) {
                $task = $afterRelease | Where-Object { $_.TaskName -eq $name }
                if (-not (Test-SameDirectory $task.WorkingDirectory $TargetWorkingDirectory)) {
                    throw 'runtime_task_release_readback_mismatch'
                }
            }
        }
        catch {
            foreach ($name in $taskNames) {
                try {
                    Set-TaskEnabledState -Name $name -Enabled $false
                }
                catch {
                    # Keep attempting the remaining tasks in the transition fence.
                }
            }
            throw 'runtime_task_release_failed'
        }
        [pscustomobject]@{ status = 'ok'; tasks = $afterRelease } |
            ConvertTo-Json -Depth 4 -Compress
        exit 0
    }

    $before = Get-TaskSnapshot -Names $taskNames
    $alreadyFenced = @($before | Where-Object { $_.Enabled }).Count -eq 0
    foreach ($task in $before) {
        if ($task.Enabled -and -not $originalEnabled[$task.TaskName]) {
            throw 'runtime_task_state_changed'
        }
        if ($task.TaskName -eq 'RakutenRoomInagoWorker' -and
            $task.State -eq 'Queued') {
            throw 'runtime_task_busy'
        }
        if ($task.TaskName -ne 'RakutenRoomInagoWorker' -and
            $task.State -in @('Running', 'Queued')) {
            throw 'runtime_task_busy'
        }
    }

    $changed = @()
    try {
        if (-not $alreadyFenced) {
            foreach ($task in $before) {
                if ($task.Enabled) {
                    Set-TaskEnabledState -Name $task.TaskName -Enabled $false
                }
            }
        }
        $fenced = Get-TaskSnapshot -Names $taskNames
        Assert-TransitionFence -Snapshot $fenced -OriginalEnabled $originalEnabled
        $workerBefore = $before | Where-Object { $_.TaskName -eq 'RakutenRoomInagoWorker' }
        $workerFenced = $fenced | Where-Object { $_.TaskName -eq 'RakutenRoomInagoWorker' }
        if ($workerBefore.State -ne 'Running' -and $workerFenced.State -eq 'Running') {
            throw 'runtime_task_busy'
        }

        foreach ($name in $switchTaskNames) {
            $old = $fenced | Where-Object { $_.TaskName -eq $name }
            if (-not $old) {
                throw 'runtime_task_missing'
            }
            if ($old.WorkingDirectory -ne $TargetWorkingDirectory) {
                Set-TaskWorkingDirectory `
                    -Name $name `
                    -Execute $old.Execute `
                    -Arguments $old.Arguments `
                    -WorkingDirectory $TargetWorkingDirectory
                $changed += $old
            }
        }

        $after = Get-TaskSnapshot -Names $taskNames
        Assert-TransitionFence -Snapshot $after -OriginalEnabled $originalEnabled
        $workerAfter = $after | Where-Object { $_.TaskName -eq 'RakutenRoomInagoWorker' }
        if ($workerBefore.State -ne 'Running' -and $workerAfter.State -eq 'Running') {
            throw 'runtime_task_busy'
        }
        foreach ($old in $fenced) {
            $current = $after | Where-Object { $_.TaskName -eq $old.TaskName }
            $expectedDirectory = if ($old.TaskName -in $switchTaskNames) {
                $TargetWorkingDirectory
            } else {
                $old.WorkingDirectory
            }
            if (-not $current -or
                $current.Execute -ne $old.Execute -or
                $current.Arguments -ne $old.Arguments -or
                -not (Test-SameDirectory $current.WorkingDirectory $expectedDirectory)) {
                throw 'runtime_task_readback_mismatch'
            }
        }
    }
    catch {
        $rollbackFailed = $false
        foreach ($old in $changed) {
            try {
                Set-TaskWorkingDirectory `
                    -Name $old.TaskName `
                    -Execute $old.Execute `
                    -Arguments $old.Arguments `
                    -WorkingDirectory $old.WorkingDirectory
            }
            catch {
                $rollbackFailed = $true
            }
        }
        if ($rollbackFailed) {
            throw 'runtime_task_switch_rollback_failed'
        }
        if (-not $alreadyFenced) {
            foreach ($name in $taskNames) {
                try {
                    Set-TaskEnabledState -Name $name -Enabled $originalEnabled[$name]
                }
                catch {
                    $rollbackFailed = $true
                }
            }
            if ($rollbackFailed) {
                foreach ($name in $taskNames) {
                    try {
                        Set-TaskEnabledState -Name $name -Enabled $false
                    }
                    catch {
                        # Preserve the generic failure response without masking later attempts.
                    }
                }
                throw 'runtime_task_switch_rollback_failed'
            }
        }
        throw 'runtime_task_switch_failed'
    }

    [pscustomobject]@{ status = 'ok'; tasks = $after; transition_fenced = $true } |
        ConvertTo-Json -Depth 4 -Compress
    exit 0
}
catch {
    [Console]::Error.WriteLine('runtime_task_operation_failed')
    exit 3
}
