function Assert-InagoStorageAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string[]]$AllowedSids,
        [bool]$RequireProtected = $false,
        [ValidateSet('private', 'trusted-source')]
        [string]$Mode = 'private',
        [bool]$RequireAllowedOwner = $true
    )

    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($RequireAllowedOwner -and $AllowedSids -notcontains $owner) {
        throw 'Storage has an unapproved owner.'
    }
    if ($RequireProtected -and -not $acl.AreAccessRulesProtected) {
        throw 'Private storage root must have inheritance disabled.'
    }

    $rights = [System.Security.AccessControl.FileSystemRights]
    $writeMask = [int]($rights::WriteData -bor $rights::AppendData -bor
        $rights::CreateFiles -bor $rights::CreateDirectories -bor
        $rights::WriteExtendedAttributes -bor $rights::WriteAttributes -bor
        $rights::DeleteSubdirectoriesAndFiles -bor $rights::Delete -bor
        $rights::ChangePermissions -bor $rights::TakeOwnership)

    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        if ($AllowedSids -contains $rule.IdentityReference.Value) {
            continue
        }
        if ($Mode -eq 'private') {
            throw 'Private storage grants access to an unapproved principal.'
        }
        if (([int]$rule.FileSystemRights -band $writeMask) -ne 0) {
            throw 'Trusted source is writable by an unapproved principal.'
        }
    }
}

function Assert-InagoTrustedSourceTree {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string[]]$AllowedSids
    )

    $trustedOwners = @($AllowedSids)
    try {
        $trustedInstaller = [Security.Principal.NTAccount]::new('NT SERVICE\TrustedInstaller').Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
        $trustedOwners += $trustedInstaller
    } catch {
        # The service account is optional on Windows editions without it.
    }

    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $cursor = $rootPath
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Trusted source path contains a reparse point.'
            }
            Assert-InagoStorageAcl -Path $cursor -AllowedSids $trustedOwners `
                -Mode 'trusted-source'
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }

    Assert-InagoStorageAcl -Path $rootPath -AllowedSids $trustedOwners `
        -Mode 'trusted-source'
    $rootItem = Get-Item -LiteralPath $rootPath -Force -ErrorAction Stop
    if (-not $rootItem.PSIsContainer) { return }

    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($rootPath)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($entry in Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Trusted source tree contains a reparse point.'
            }
            Assert-InagoStorageAcl -Path $entry.FullName -AllowedSids $trustedOwners `
                -Mode 'trusted-source'
            if ($entry.PSIsContainer) {
                $pending.Push($entry.FullName)
            }
        }
    }
}
