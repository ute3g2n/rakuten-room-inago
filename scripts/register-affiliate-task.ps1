$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$scriptPath = Join-Path $PSScriptRoot 'check-affiliate.mjs'
$nodePath = 'C:\Program Files\nodejs\node.exe'
$taskName = 'RakutenRoomBakemonoAffiliateDaily'

if (-not (Test-Path -LiteralPath $nodePath -PathType Leaf)) {
    throw "固定の Node.js 実行ファイルがありません: $nodePath"
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "日次確認スクリプトがありません: $scriptPath"
}

$arguments = '"{0}" check' -f $scriptPath
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $sameScript = $existing.Actions | Where-Object {
        $_.Execute -eq $nodePath -and $_.Arguments -eq $arguments
    }
    if (-not $sameScript) {
        throw "同名の別タスクがあるため変更しません: $taskName"
    }
}

$action = New-ScheduledTaskAction -Execute $nodePath -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '13:00'
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description '楽天アフィリエイトの日別成果を閲覧し、プロジェクトに保存する。ログオン中のみ実行。' -Force | Out-Null
Write-Output "$taskName を毎日13:00に登録しました（スリープ解除有効、ユーザーのログオン中のみ実行）。"
