# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Custom Kit — Installeur Windows (PowerShell)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Installe uv s'il manque, puis grimoire-kit en version épinglée avec
# `uv tool install` : grimoire, grimoire-mcp et grimoire-hook se résolvent
# ensuite depuis n'importe quel shell, sans venv actif.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Version 3.71.0
#
# Le script publié en artefact de release porte sa version et une attestation
# de provenance : gh attestation verify install.ps1 --repo Guilhem-Bonnet/Grimoire-kit
# Sorti du dépôt, sans version estampillée, il exige -Version.
#
# Variables : GRIMOIRE_VERSION (= -Version), GRIMOIRE_SPEC (wheel ou source à
# installer à la place du paquet publié, pour la CI), UV_VERSION (version d'uv).
# ═══════════════════════════════════════════════════════════════════════════════

[CmdletBinding()]
param(
    [string]$Version = $env:GRIMOIRE_VERSION,
    [string]$Spec = $env:GRIMOIRE_SPEC
)

$ErrorActionPreference = 'Stop'

# Estampillé par la release (publish.yml) ; vide dans le dépôt.
$PinnedVersion = ""

function Write-Info($m) { Write-Host "i  $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "OK $m" -ForegroundColor Green }
function Fail($m)       { Write-Host "ERREUR $m" -ForegroundColor Red; exit 1 }

# Un exécutable natif qui échoue ne lève pas d'exception : on teste le code.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "$Exe $($Arguments -join ' ') a échoué (code $LASTEXITCODE)" }
}

if (-not $Version) { $Version = $PinnedVersion }
if (-not $Version) {
    Fail "aucune version épinglée : relancez avec -Version X.Y.Z (ou utilisez le script publié avec la release)"
}

# ─── uv ──────────────────────────────────────────────────────────────────────
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "uv déjà présent : $(uv --version)"
} else {
    Write-Info "uv absent — installation..."
    $url = "https://astral.sh/uv/install.ps1"
    if ($env:UV_VERSION) { $url = "https://astral.sh/uv/$($env:UV_VERSION)/install.ps1" }
    Invoke-RestMethod $url | Invoke-Expression
    # uv s'installe dans ~\.local\bin ; la session courante ne le sait pas encore.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv est installé mais introuvable dans le PATH"
    }
    Write-Ok "uv installé : $(uv --version)"
}

# ─── SDK : uv tool install ───────────────────────────────────────────────────
$target = if ($Spec) { $Spec } else { "grimoire-kit==$Version" }
Write-Info "Installation de grimoire-kit $Version (uv tool install)..."
Invoke-Native uv @('tool', 'install', '--force', $target)

# Le dossier des exécutables d'uv doit être dans le PATH, ici et aux shells futurs.
$bindir = (& uv tool dir --bin 2>$null)
if ($LASTEXITCODE -eq 0 -and $bindir) { $env:Path = "$bindir;$env:Path" }
& uv tool update-shell *> $null

foreach ($tool in 'grimoire', 'grimoire-mcp', 'grimoire-hook') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Fail "$tool ne se résout pas dans le PATH après l'installation"
    }
}
$got = (& grimoire --version | Out-String).Trim()
if ($got -ne "grimoire-kit $Version") {
    Fail "version installée inattendue : '$got' (attendu : grimoire-kit $Version)"
}
Write-Ok "$got — grimoire, grimoire-mcp et grimoire-hook sont installés"
Write-Info "Si grimoire n'est pas encore trouvé, ouvrez un nouveau terminal. Puis : grimoire up ."
