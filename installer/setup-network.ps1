# Firewall do PhoneBridge (e, só se pedido com -Private, redes Públicas conectadas -> Privadas).
# Só mexe em regras com os NOMES EXATOS do PhoneBridge: "PhoneBridge <porta>" e "PhoneBridge descoberta".
param([int]$Port = 5800, [switch]$Private, [switch]$RemoveOnly)
$ErrorActionPreference = 'SilentlyContinue'
$pattern = '^PhoneBridge( \d+| descoberta)$'

if ($Private) {
    Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | ForEach-Object {
        Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
    }
    exit 0
}

# remove só as regras do próprio PhoneBridge (evita duplicatas ao reinstalar)
Get-NetFirewallRule | Where-Object { $_.DisplayName -match $pattern } | Remove-NetFirewallRule
if ($RemoveOnly) { exit 0 }

New-NetFirewallRule -DisplayName "PhoneBridge $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'PhoneBridge descoberta' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5801 -RemoteAddress LocalSubnet -Profile Any | Out-Null
