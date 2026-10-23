# Stand-in for the Windows-only NetSecurity module, so the HaptiSync installer can finish under Wine.
# Wine has no Windows Firewall. These commands only log the call to C:\fwshim.log and report success.
# They are used only when PowerShell 7 can't find the real cmdlet, and only inside this Wine prefix.

function Write-FwShimLog {
    param($Text)
    try {
        Add-Content -Path 'C:\fwshim.log' -Value ("{0} {1}" -f (Get-Date -Format s), $Text)
    } catch { }
}

function Set-NetFirewallRule     { Write-FwShimLog "Set-NetFirewallRule $args" }
function New-NetFirewallRule     { Write-FwShimLog "New-NetFirewallRule $args" }
function Remove-NetFirewallRule  { Write-FwShimLog "Remove-NetFirewallRule $args" }
function Enable-NetFirewallRule  { Write-FwShimLog "Enable-NetFirewallRule $args" }
function Disable-NetFirewallRule { Write-FwShimLog "Disable-NetFirewallRule $args" }
function Get-NetFirewallRule     { Write-FwShimLog "Get-NetFirewallRule $args" }

Export-ModuleMember -Function Set-NetFirewallRule, New-NetFirewallRule, Remove-NetFirewallRule, Enable-NetFirewallRule, Disable-NetFirewallRule, Get-NetFirewallRule
