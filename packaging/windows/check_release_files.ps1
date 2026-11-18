# Checks Windows release files before they go anywhere. Used by
# .github/workflows/windows-release.yml; runs on any Windows PowerShell 5.1+.
#
#   .\check_release_files.ps1 -Version 0.9.1 <files...>            metadata only
#   .\check_release_files.ps1 -Version 0.9.1 -Signed <files...>    and the signature
#
# Metadata: product name "Photobox" and product version = VERSION in every file.
# A code signing service enforces both (docs/CODE-SIGNING.md); a build that
# lost its version resource is caught here, before a signing request.
# -Signed: a valid Authenticode signature whose signer is the SignPath
# Foundation certificate. Anything else stops the release.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$Signed,
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Files
)
$ErrorActionPreference = "Stop"
$ProductName = "Photobox"
$Signer = "SignPath Foundation"

foreach ($f in $Files) {
    if (-not (Test-Path -LiteralPath $f)) { throw "missing: $f" }
    $name = Split-Path -Leaf $f
    $info = (Get-Item -LiteralPath $f).VersionInfo
    if ($info.ProductName -ne $ProductName) {
        throw "${name}: product name is '$($info.ProductName)', expected '$ProductName'"
    }
    if ($info.ProductVersion -ne $Version) {
        throw "${name}: product version is '$($info.ProductVersion)', expected '$Version'"
    }
    if ($Signed) {
        $sig = Get-AuthenticodeSignature -LiteralPath $f
        if ($sig.Status -ne "Valid") {
            throw "${name}: signature status $($sig.Status) - $($sig.StatusMessage)"
        }
        $subject = $sig.SignerCertificate.Subject
        if ($subject -notmatch [regex]::Escape($Signer)) {
            throw "${name}: signed by '$subject', expected '$Signer'"
        }
        Write-Host "ok  $name  $($info.ProductName) $($info.ProductVersion)  signed by $subject"
    } else {
        Write-Host "ok  $name  $($info.ProductName) $($info.ProductVersion)  (signature not checked)"
    }
}
