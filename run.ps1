# 살충제 판매제한 조회 앱 실행 스크립트
# 사용법:  .\run.ps1
#   휴대폰에서 접속하려면: .\run.ps1 -Network

param([switch]$Network)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if ($Network) {
    # 같은 Wi-Fi 안의 휴대폰 브라우저에서 접속 가능
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp,Manual |
           Where-Object { $_.IPAddress -notlike "169.*" -and $_.IPAddress -ne "127.0.0.1" } |
           Select-Object -First 1).IPAddress
    Write-Host "휴대폰 브라우저에서 접속:  http://${ip}:8501" -ForegroundColor Green
    python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
} else {
    python -m streamlit run app.py
}
