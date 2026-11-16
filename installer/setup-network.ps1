# Cria as regras de firewall do PhoneBridge e muda as redes conectadas "Públicas" para "Privadas".
$ErrorActionPreference = 'SilentlyContinue'
Get-NetFirewallRule -DisplayName 'PhoneBridge*' | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'PhoneBridge 5800' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5800 -RemoteAddress LocalSubnet -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'PhoneBridge descoberta' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5801 -RemoteAddress LocalSubnet -Profile Any | Out-Null
Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | ForEach-Object {
    Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
}
