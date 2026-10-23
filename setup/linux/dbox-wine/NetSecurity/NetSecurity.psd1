@{
    RootModule        = 'NetSecurity.psm1'
    ModuleVersion     = '1.0.0.0'
    GUID              = '5b0f0f3e-6f0e-4f5b-9a57-6d0b5c1d7e21'
    Description       = 'Wine stand-in for the Windows NetSecurity firewall cmdlets (logs calls, does nothing).'
    FunctionsToExport = @(
        'Set-NetFirewallRule',
        'New-NetFirewallRule',
        'Remove-NetFirewallRule',
        'Enable-NetFirewallRule',
        'Disable-NetFirewallRule',
        'Get-NetFirewallRule'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
}
