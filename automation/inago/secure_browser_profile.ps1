[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable.' }
$profilePath = Join-Path $env:LOCALAPPDATA 'InagoPlaywrightProfile'
if (-not (Test-Path -LiteralPath $profilePath -PathType Container)) {
    throw 'The dedicated Playwright profile is unavailable.'
}

$normalizedProfile = [IO.Path]::GetFullPath($profilePath).TrimEnd('\')
$active = @(
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction Stop |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($normalizedProfile, [StringComparison]::OrdinalIgnoreCase) -ge 0
        }
)
if ($active.Count -gt 0) {
    throw 'Close the dedicated Inago Playwright Chrome before protecting its profile.'
}

$ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$allowedSids = @($ownerSid.Value, 'S-1-5-18', 'S-1-5-32-544')
$entries = @((Get-Item -LiteralPath $normalizedProfile -Force))
$entries += @(Get-ChildItem -LiteralPath $normalizedProfile -Force -Recurse -ErrorAction Stop)
foreach ($entry in $entries) {
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'The browser profile contains a reparse point; no ACL changes were made.'
    }
}

foreach ($entry in $entries) {
    if ($entry.PSIsContainer) {
        $security = New-Object System.Security.AccessControl.DirectorySecurity
    } else {
        $security = New-Object System.Security.AccessControl.FileSecurity
    }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($ownerSid)
    foreach ($sidValue in $allowedSids) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier($sidValue)
        if ($entry.PSIsContainer) {
            $inheritance = [Security.AccessControl.InheritanceFlags]([int][Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [int][Security.AccessControl.InheritanceFlags]::ObjectInherit)
        } else {
            $inheritance = [Security.AccessControl.InheritanceFlags]::None
        }
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $security.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $entry.FullName -AclObject $security
}

$count = 0
foreach ($entry in $entries) {
    $acl = Get-Acl -LiteralPath $entry.FullName
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($allowedSids -notcontains $owner) { throw 'A browser profile entry has an unapproved owner.' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $allowedSids -notcontains $rule.IdentityReference.Value) {
            throw 'A browser profile entry still grants access to an unapproved principal.'
        }
    }
    $count++
}

[pscustomobject]@{
    ProfileProtected = $true
    EntryCount = $count
    ActiveDedicatedChrome = 0
} | ConvertTo-Json -Compress
