<#
.SYNOPSIS
    Prepara um cartao recem-gravado para o Pi se instalar sozinho como pendant.

.DESCRIPTION
    Rode DEPOIS de gravar o Raspberry Pi OS com o Raspberry Pi Imager (usando a
    engrenagem para hostname, usuario, SSH e Wi-Fi). O script faz duas coisas na
    particao de boot:

      1. copia a pasta pendant_deploy/ para dentro dela;
      2. acrescenta um runcmd ao user-data do cloud-init, para o Pi rodar o
         instalar.sh sozinho no primeiro boot e reiniciar no quiosque.

    Depois disso o processo inteiro e: gravar, rodar este script, ejetar, ligar.
    Sem scp, sem ssh, sem rodar nada no Pi.

    O primeiro boot fica longo (instala numpy, tkinter, chromium, labwc,
    xwayland) e o Pi reinicia uma vez no fim. Num Pi 4 com rede boa, cerca de 7
    minutos do ligar ate a tela pronta.

    Nesse meio tempo o Pi PARA NUMA TELA DE LOGIN. E normal: o autologin e o
    quiosque so passam a existir quando a instalacao termina. O log fica em
    /var/log/pendant-instalar.log.

.PARAMETER Numero
    Ultimo octeto do IP na rede do robo. Padrao 12 -> 10.26.10.12. Tem que ser
    BAIXO: o UR5 CB2 nao responde a enderecos altos como .200/.201.

.PARAMETER Unidade
    Letra da particao de boot. Se omitido, o script procura o volume rotulado
    "bootfs" e confirma que e mesmo um cartao de Pi.

.EXAMPLE
    .\preparar_cartao.ps1
    .\preparar_cartao.ps1 -Numero 13
    .\preparar_cartao.ps1 -Unidade E -Numero 14
#>
[CmdletBinding()]
param(
    [ValidateRange(2, 99)]
    [int]$Numero = 12,

    [ValidatePattern('^[A-Za-z]$')]
    [string]$Unidade
)

$ErrorActionPreference = 'Stop'

$MARCA_INI = '# >>> pendant: instalacao automatica no primeiro boot >>>'
$MARCA_FIM = '# <<< pendant <<<'

function Falhar($msg) { Write-Host "ERRO: $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)     { Write-Host "  OK   $msg" -ForegroundColor Green }
function Aviso($msg)  { Write-Host "  !!   $msg" -ForegroundColor Yellow }

# ---- 1. achar a particao de boot -------------------------------------------
if ($Unidade) {
    $letra = $Unidade.ToUpper()
} else {
    $vols = @(Get-Volume | Where-Object { $_.FileSystemLabel -eq 'bootfs' -and $_.DriveLetter })
    if ($vols.Count -eq 0) { Falhar "nenhum volume rotulado 'bootfs'. O cartao esta plugado? Use -Unidade." }
    if ($vols.Count -gt 1) { Falhar "achei mais de um 'bootfs' ($($vols.DriveLetter -join ', ')). Use -Unidade para escolher." }
    $letra = $vols[0].DriveLetter
}
$boot = "${letra}:"

# Conferir que e mesmo um cartao de Raspberry Pi, e nao o HD de backup.
foreach ($f in @('config.txt', 'cmdline.txt')) {
    if (-not (Test-Path "$boot\$f")) { Falhar "$boot nao parece a particao de boot de um Pi (falta $f). Abortei para nao escrever no disco errado." }
}
$vol = Get-Volume -DriveLetter $letra
Write-Host ""
Write-Host "Cartao: $boot  ($($vol.FileSystemLabel), $([math]::Round($vol.Size/1MB,0)) MB, $($vol.FileSystem))"
if (Test-Path "$boot\issue.txt") { Write-Host "Imagem: $((Get-Content "$boot\issue.txt" -TotalCount 1))" }
Write-Host "Destino na rede do robo: 10.26.10.$Numero"
Write-Host ""

# ---- 2. o user-data tem que existir (e o mecanismo desta imagem) ------------
$userData = "$boot\user-data"
if (-not (Test-Path $userData)) {
    Falhar @"
nao achei $userData.

Este cartao nao foi personalizado pelo Imager, ou a imagem usa o outro
mecanismo (custom.toml / firstboot). Grave de novo com o Raspberry Pi Imager
e preencha a engrenagem: hostname pendant$Numero, usuario raspberry, SSH com a
sua chave, e o Wi-Fi da LAN (obrigatorio, e por ele que voce alcanca o Pi).
"@
}

$conteudo = Get-Content $userData -Raw
if ($conteudo -notmatch '(?m)^#cloud-config') { Falhar "$userData nao comeca com '#cloud-config'. Nao vou mexer nele." }

# ---- 3. avisos uteis antes de gravar ---------------------------------------
if ($conteudo -match '(?m)^\s*hostname:\s*(\S+)') {
    $hn = $matches[1]
    Write-Host "Hostname do cartao: $hn"
    if ($hn -ne "pendant$Numero") { Aviso "hostname '$hn' nao casa com o numero $Numero (esperado 'pendant$Numero'). O IP e o nome vao divergir." }
}
if (Test-Path "$boot\network-config") {
    if ((Get-Content "$boot\network-config" -Raw) -match 'wifis:') { Ok "Wi-Fi configurado no cartao" }
    else { Aviso "o network-config NAO tem Wi-Fi. Sem ele o Pi sobe inalcancavel: so a rede do robo, que nao tem roteador." }
} else {
    Aviso "nao achei network-config. Confirme que o Wi-Fi foi preenchido na engrenagem."
}
if ($conteudo -match 'ssh_authorized_keys') { Ok "chave SSH autorizada no cartao" }
else { Aviso "nenhuma chave SSH no user-data. Voce so entrara por senha, se tiver habilitado." }

# ---- 4. copiar o pacote para a particao de boot ----------------------------
$origem = Split-Path -Parent $PSScriptRoot    # .../pendant_deploy
if (-not (Test-Path "$origem\instalar.sh")) { Falhar "nao achei instalar.sh em $origem. Rode este script de dentro de pendant_deploy\cartao\." }

$destino = "$boot\pendant_deploy"
if (Test-Path $destino) { Remove-Item $destino -Recurse -Force }
# -Exclude nao e recursivo no Copy-Item; copiar tudo e limpar depois.
Copy-Item $origem $destino -Recurse -Force
Get-ChildItem $destino -Recurse -Force -Directory -Filter '.git' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

$nOrig = (Get-ChildItem $origem  -Recurse -File | Where-Object { $_.FullName -notmatch '\\\.git\\' }).Count
$nDest = (Get-ChildItem $destino -Recurse -File).Count
if ($nDest -lt $nOrig) { Falhar "copia incompleta: $nDest de $nOrig arquivos. Cartao cheio ou com erro?" }
Ok "pendant_deploy copiado ($nDest arquivos)"

# ---- 5. injetar o runcmd no user-data --------------------------------------
# Idempotente: tira o bloco antigo antes de por o novo, para poder rodar de novo.
if ($conteudo -match [regex]::Escape($MARCA_INI)) {
    $conteudo = [regex]::Replace($conteudo,
        "(?s)\r?\n?" + [regex]::Escape($MARCA_INI) + ".*?" + [regex]::Escape($MARCA_FIM), '')
    Ok "bloco anterior removido (reaplicando)"
}

# Um runcmd do Imager seria raro, mas se existir a fusao e manual: duas chaves
# runcmd no mesmo YAML fazem o cloud-init usar so a ultima, em silencio.
if ($conteudo -match '(?m)^runcmd:') {
    Falhar @"
o user-data ja tem um 'runcmd:' que nao e meu.

Duas chaves 'runcmd' no mesmo YAML fazem o cloud-init ignorar uma delas sem
avisar. Junte os comandos a mao, ou grave o cartao de novo.
"@
}

# O cloud-init roda isto como root, no primeiro boot, com a rede ja de pe.
$bloco = @"

$MARCA_INI
# Gerado por preparar_cartao.ps1. O Pi se instala como pendant e reinicia no
# quiosque. Acompanhe por: tail -f /var/log/pendant-instalar.log
runcmd:
  - [ bash, -lc, "cp -r /boot/firmware/pendant_deploy /root/pendant_deploy" ]
  - [ bash, -lc, "chmod +x /root/pendant_deploy/instalar.sh /root/pendant_deploy/arquivos/*.sh" ]
  - [ bash, -lc, "/root/pendant_deploy/instalar.sh $Numero > /var/log/pendant-instalar.log 2>&1" ]
  - [ bash, -lc, "systemctl reboot" ]
$MARCA_FIM
"@

$novo = $conteudo.TrimEnd("`r", "`n") + "`n" + $bloco + "`n"

Copy-Item $userData "$userData.bak" -Force
# Sem BOM: o cloud-init nao reconhece '#cloud-config' se vier BOM na frente.
[System.IO.File]::WriteAllText($userData, ($novo -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))
Ok "user-data preparado (backup em user-data.bak)"

# ---- 6. descarregar o cache -------------------------------------------------
try { Write-VolumeCache -DriveLetter $letra -ErrorAction Stop } catch { }

Write-Host ""
Write-Host "Pronto. Agora:" -ForegroundColor Cyan
Write-Host "  1. ejete o cartao com seguranca"
Write-Host "  2. ponha no Pi e ligue"
Write-Host "  3. espere (num Pi 4 com rede boa, ~7 min; reinicia sozinho no fim)"
Write-Host ""
Write-Host "O Pi vai PARAR NUMA TELA DE LOGIN no meio do caminho. E normal:" -ForegroundColor Yellow
Write-Host "e a instalacao rodando. Nao digite nada e nao desligue." -ForegroundColor Yellow
Write-Host ""
Write-Host "No fim ele reinicia e a tela abre sozinha no pendant, em tela cheia."
Write-Host "Do PC:  http://pendant$Numero.local:8080/pendant_dt"
Write-Host "Deu errado?  ssh raspberry@pendant$Numero.local 'tail -40 /var/log/pendant-instalar.log'"
Write-Host ""
