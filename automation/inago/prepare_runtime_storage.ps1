[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if (-not $env:USERPROFILE) { throw 'USERPROFILE is unavailable.' }
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
. (Join-Path $PSScriptRoot 'storage_security.ps1')

$ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$allowedSids = @($ownerSid.Value, 'S-1-5-18', 'S-1-5-32-544')

function Assert-NoReparsePoint([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($true) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Runtime storage cannot contain a reparse point.'
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-PrivateTree([string]$Path, [bool]$RequireProtectedRoot) {
    Assert-NoReparsePoint $Path
    $entries = @((Get-Item -LiteralPath $Path -Force))
    $entries += @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)
    foreach ($entry in $entries) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Runtime storage tree cannot contain reparse points.'
        }
        $isRoot = $entry.FullName.TrimEnd('\') -eq $Path.TrimEnd('\')
        Assert-InagoStorageAcl -Path $entry.FullName -AllowedSids $allowedSids `
            -Mode 'private' -RequireProtected ($RequireProtectedRoot -and $isRoot)
    }
}

function New-PrivateDirectorySecurity {
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($ownerSid)
    foreach ($sidValue in $allowedSids) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidValue)
        $inheritance = [System.Security.AccessControl.InheritanceFlags]([int][System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [int][System.Security.AccessControl.InheritanceFlags]::ObjectInherit)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $security.AddAccessRule($rule)
    }
    return $security
}

function Ensure-PrivateDirectory([string]$Path) {
    Assert-NoReparsePoint (Split-Path -Parent $Path)
    if (Test-Path -LiteralPath $Path) {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw 'A runtime storage path is occupied by a non-directory.'
        }
        Assert-NoReparsePoint $Path
        Assert-PrivateTree $Path $true
        return
    }
    $security = New-PrivateDirectorySecurity
    [void](New-Item -ItemType Directory -Path $Path)
    Set-Acl -LiteralPath $Path -AclObject $security
    Assert-NoReparsePoint $Path
    Assert-InagoStorageAcl -Path $Path -AllowedSids $allowedSids `
        -Mode 'private' -RequireProtected $true
}

$runtimeRoot = Join-Path $env:USERPROFILE 'InagoRuntime'
$backupRoot = Join-Path $env:USERPROFILE 'InagoRuntimeBackups'
Assert-InagoStorageAcl -Path $env:USERPROFILE -AllowedSids $allowedSids `
    -Mode 'trusted-source'
Ensure-PrivateDirectory $runtimeRoot
Ensure-PrivateDirectory $backupRoot

[pscustomobject]@{
    RuntimeProtected = (Get-Acl -LiteralPath $runtimeRoot).AreAccessRulesProtected
    BackupProtected = (Get-Acl -LiteralPath $backupRoot).AreAccessRulesProtected
    UnexpectedWriteAces = 0
    ReparsePoints = 0
} | ConvertTo-Json -Compress
