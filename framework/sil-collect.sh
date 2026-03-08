#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Custom Kit — Self-Improvement Loop : collecteur de signaux d'échec
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage :
#   bash sil-collect.sh                    # depuis la racine du projet cible
#   bash sil-collect.sh --since 30         # limiter aux 30 derniers jours
#   bash sil-collect.sh --out /chemin      # répertoire de sortie custom
#
# Produit : _grimoire-output/sil-report-latest.md
#   Snapshot brut agrégé des signaux d'échec.
#   À donner à Sentinel ([FA] Self-Improvement Loop) pour analyse.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_ROOT="$(pwd)"
SINCE_DAYS=90
OUTPUT_DIR="$PROJECT_ROOT/_grimoire-output"
OUTPUT_FILE="$OUTPUT_DIR/sil-report-latest.md"
DATE_NOW="$(date '+%Y-%m-%d %H:%M')"

# ─── Couleurs ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }

# ─── Arguments ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE_DAYS="$2"; shift 2 ;;
        --out)   OUTPUT_DIR="$2"; OUTPUT_FILE="$OUTPUT_DIR/sil-report-latest.md"; shift 2 ;;
        --help)
            echo "Usage: sil-collect.sh [--since DAYS] [--out DIR]"
            echo "  --since N  : limiter aux N derniers jours (défaut: 90)"
            echo "  --out DIR  : répertoire de sortie (défaut: _grimoire-output/)"
            exit 0
            ;;
        *) echo "Option inconnue: $1" >&2; exit 1 ;;
    esac
done

# ─── Chemins mémoire ──────────────────────────────────────────────────────────
MEMORY_DIR="$PROJECT_ROOT/_grimoire/_memory"
DECISIONS_LOG="$MEMORY_DIR/decisions-log.md"
CONTRADICTION_LOG="$MEMORY_DIR/contradiction-log.md"
HANDOFF_LOG="$MEMORY_DIR/handoff-log.md"
LEARNINGS_DIR="$MEMORY_DIR/agent-learnings"
ACTIVITY_LOG="$MEMORY_DIR/activity.jsonl"

mkdir -p "$OUTPUT_DIR"

# ─── Fonctions d'analyse ──────────────────────────────────────────────────────

# Compte les lignes contenant un pattern dans un fichier (0 si absent)
count_pattern() {
    local file="$1" pattern="$2" n
    if [[ ! -f "$file" ]]; then echo 0; return; fi
    n=$(grep -ci "$pattern" "$file" 2>/dev/null || true)
    echo "${n:-0}"
}

# Extrait les lignes contenant un pattern, avec contexte
extract_signals() {
    local file="$1" pattern="$2"
    if [[ ! -f "$file" ]] || [[ ! -s "$file" ]]; then
        echo "_Fichier absent ou vide_"
        return
    fi
    local matches
    matches=$(grep -in "$pattern" "$file" 2>/dev/null | head -20 || true)
    if [[ -z "$matches" ]]; then
        echo "_Aucun signal trouvé_"
    else
        echo "$matches" | while IFS= read -r line; do
            echo "- L.$(echo "$line" | cut -d: -f1) : $(echo "$line" | cut -d: -f2-)"
        done
    fi
}

# Résumé d'un fichier learnings
summarize_learnings() {
    local file="$1"
    [[ ! -f "$file" ]] && return
    local agent_name warnings fails
    agent_name=$(basename "$file" .md)
    warnings=$(grep -c "⚠️\|ATTENTION\|WARNING\|correction\|erreur" "$file" 2>/dev/null || true)
    fails=$(grep -c "FAIL\|échoué\|raté\|cassé\|non testé\|manquant" "$file" 2>/dev/null || true)
    if [[ "${warnings:-0}" -gt 0 ]] || [[ "${fails:-0}" -gt 0 ]]; then
        echo "| $agent_name | ${warnings:-0} | ${fails:-0} |"
    fi
}

# ─── Collecte activité récente ────────────────────────────────────────────────
collect_activity_failures() {
    if [[ ! -f "$ACTIVITY_LOG" ]]; then
        echo "_activity.jsonl absent_"
        return
    fi
    local cutoff
    cutoff=$(date -d "$SINCE_DAYS days ago" '+%Y-%m-%d' 2>/dev/null || \
             date -v-"${SINCE_DAYS}d" '+%Y-%m-%d' 2>/dev/null || echo "2000-01-01")
    local count=0
    while IFS= read -r line; do
        local ts event
        ts=$(echo "$line" | grep -o '"timestamp":"[^"]*"' | cut -d'"' -f4 || echo "")
        event=$(echo "$line" | grep -o '"event":"[^"]*"' | cut -d'"' -f4 || echo "")
        [[ "$ts" < "$cutoff" ]] && continue
        [[ "$event" == *fail* ]] || [[ "$event" == *error* ]] || [[ "$event" == *cc_fail* ]] && {
            echo "- $ts : $event — $(echo "$line" | grep -o '"agent":"[^"]*"' | cut -d'"' -f4)"
            count=$((count + 1))
        }
    done < "$ACTIVITY_LOG"
    [[ $count -eq 0 ]] && echo "_Aucun événement d'échec dans les $SINCE_DAYS derniers jours_"
}

# ═══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION DU RAPPORT
# ═══════════════════════════════════════════════════════════════════════════════

# Détection projet neuf (toutes sources vides)
_all_sources_empty=true
for _f in "$DECISIONS_LOG" "$CONTRADICTION_LOG" "$HANDOFF_LOG" "$ACTIVITY_LOG"; do
    [[ -f "$_f" ]] && [[ -s "$_f" ]] && _all_sources_empty=false && break
done
if [[ -d "$LEARNINGS_DIR" ]]; then
    for _f in "$LEARNINGS_DIR"/*.md; do
        [[ -f "$_f" ]] && [[ -s "$_f" ]] && _all_sources_empty=false && break
    done
fi

if $_all_sources_empty; then
    warn "Aucune source de données disponible."
    echo ""
    echo "  💡 C'est normal sur un projet neuf ou peu actif."
    echo "     Le SIL a besoin de données accumulées pour être utile :"
    echo "     - decisions-log.md       : décisions et retours post-livraison"
    echo "     - contradiction-log.md   : désaccords entre agents"
    echo "     - agent-learnings/*.md   : apprentissages des agents"
    echo "     - activity.jsonl         : journal d'activité"
    echo ""
    echo "  → Revenez après 2-3 semaines d'utilisation normale."
    echo "  → Pour forcer la génération quand même : relancez avec --force-empty"
    if [[ "${*:-}" != *--force-empty* ]]; then
        exit 0
    fi
fi

info "Collecte des signaux SIL depuis $PROJECT_ROOT..."

cat > "$OUTPUT_FILE" <<HEADER
# Self-Improvement Loop — Snapshot brut
**Généré le** : $DATE_NOW
**Période analysée** : $SINCE_DAYS derniers jours
**Projet** : $PROJECT_ROOT

> ⚠️ Ce fichier est un snapshot BRUT à analyser par Sentinel ([FA]).
> Ne pas modifier manuellement. Re-générer via : \`bash _grimoire/_config/custom/sil-collect.sh\`

---

## 1. Signaux — Type A : CC_FAIL (terminé sans preuve)
Recherche dans decisions-log et learnings : "CC FAIL", "sans vérifier", "oubli", "terminé" sans CC.

### decisions-log.md
$(extract_signals "$DECISIONS_LOG" "cc.fail\|sans.verif\|terminé.sans\|cc_fail" "CC_FAIL")

### agent-learnings (occurrences CC FAIL)
HEADER

# Scan des learnings pour CC_FAIL
cc_fail_total=0
if [[ -d "$LEARNINGS_DIR" ]]; then
    for f in "$LEARNINGS_DIR"/*.md; do
        [[ -f "$f" ]] || continue
        count=$(grep -ci "cc.fail\|cc fail\|sans.cc\|oubli.*cc\|complet.*sans" "$f" 2>/dev/null || true)
        count="${count:-0}"
        if [[ "$count" -gt 0 ]]; then
            echo "- **$(basename "$f" .md)** : $count occurrence(s)" >> "$OUTPUT_FILE"
            cc_fail_total=$((cc_fail_total + count))
        fi
    done
fi
if [[ $cc_fail_total -eq 0 ]]; then
    echo "_Aucun signal CC_FAIL dans les learnings_" >> "$OUTPUT_FILE"
fi

cat >> "$OUTPUT_FILE" <<SECTION2

---

## 2. Signaux — Type B : INCOMPLETE (livraison partielle)
Recherche : "manquant", "TODO", "non implémenté", "à faire", "oublié".

### decisions-log.md
$(extract_signals "$DECISIONS_LOG" "manquant\|todo\|non.implémenté\|à.faire\|oublié\|incomplet" "INCOMPLETE")

### handoff-log.md
$(extract_signals "$HANDOFF_LOG" "manquant\|incomplet\|pas.fait\|reste\|oublié" "INCOMPLETE_HANDOFF")

---

## 3. Signaux — Type C : CONTRADICTION (désaccord inter-agents)

### contradiction-log.md (entrées actives)
SECTION2

if [[ -f "$CONTRADICTION_LOG" ]]; then
    # Extraire les lignes de tableau avec statut ⏳ (actif) ou ⚠️ (escaladé)
    active=$(grep -E "⏳|⚠️" "$CONTRADICTION_LOG" 2>/dev/null | head -15 || true)
    if [[ -n "$active" ]]; then
        echo "$active" | while IFS= read -r line; do echo "- $line"; done >> "$OUTPUT_FILE"
    else
        echo "_Aucune contradiction active_" >> "$OUTPUT_FILE"
    fi
    total_contradictions=$(grep -c "^|" "$CONTRADICTION_LOG" 2>/dev/null || true)
    echo "" >> "$OUTPUT_FILE"
    echo "**Total entrées contradiction-log** : ${total_contradictions:-0}" >> "$OUTPUT_FILE"
else
    echo "_contradiction-log.md absent_" >> "$OUTPUT_FILE"
fi

cat >> "$OUTPUT_FILE" <<SECTION4

---

## 4. Signaux — Type D : GUARDRAIL_MISS (action destructive sans confirmation)
Recherche : "supprimé sans", "destroy", "écrasé", "overwrite", "sans confirmer".

### decisions-log.md
$(extract_signals "$DECISIONS_LOG" "supprimé.sans\|destroy.*sans\|écrasé\|overwrite\|sans.confirm" "GUARDRAIL_MISS")

### agent-learnings (guardrails manqués)
SECTION4

guardrail_total=0
if [[ -d "$LEARNINGS_DIR" ]]; then
    for f in "$LEARNINGS_DIR"/*.md; do
        [[ -f "$f" ]] || continue
        count=$(grep -ci "guardrail\|supprimé.sans\|détruit\|overwrite\|écrasé" "$f" 2>/dev/null || true)
        count="${count:-0}"
        if [[ "$count" -gt 0 ]]; then
            echo "- **$(basename "$f" .md)** : $count occurrence(s)" >> "$OUTPUT_FILE"
            guardrail_total=$((guardrail_total + count))
        fi
    done
fi
if [[ $guardrail_total -eq 0 ]]; then
    echo "_Aucun signal GUARDRAIL_MISS dans les learnings_" >> "$OUTPUT_FILE"
fi

cat >> "$OUTPUT_FILE" <<SECTION5

---

## 5. Signaux — Type E : EXPERTISE_GAP (utilisateur a corrigé un détail technique)
Recherche : "correction", "en fait", "non c'est", "mauvais\|incorrect", "tu t'es trompé".

### decisions-log.md
$(extract_signals "$DECISIONS_LOG" "correction\|en fait\|non.c.est\|incorrect\|trompé\|erreur.technique" "EXPERTISE_GAP")

### agent-learnings (expertise gap)
SECTION5

expertise_total=0
if [[ -d "$LEARNINGS_DIR" ]]; then
    for f in "$LEARNINGS_DIR"/*.md; do
        [[ -f "$f" ]] || continue
        count=$(grep -ci "correction\|erreur.technique\|incorrect\|trompé\|exact.c.est" "$f" 2>/dev/null || true)
        count="${count:-0}"
        if [[ "$count" -gt 0 ]]; then
            echo "- **$(basename "$f" .md)** : $count occurrence(s)" >> "$OUTPUT_FILE"
            expertise_total=$((expertise_total + count))
        fi
    done
fi
if [[ $expertise_total -eq 0 ]]; then
    echo "_Aucun signal EXPERTISE_GAP dans les learnings_" >> "$OUTPUT_FILE"
fi

cat >> "$OUTPUT_FILE" <<SECTION6

---

## 6. Activité récente — Événements d'échec (activity.jsonl)

$(collect_activity_failures)

---

## 7. Santé des learnings par agent

| Agent | Warnings | Fails |
|-------|----------|-------|
SECTION6

if [[ -d "$LEARNINGS_DIR" ]]; then
    for f in "$LEARNINGS_DIR"/*.md; do
        [[ -f "$f" ]] || continue
        summarize_learnings "$f" >> "$OUTPUT_FILE"
    done
fi

cat >> "$OUTPUT_FILE" <<FOOTER

---

## 8. Résumé des compteurs bruts

| Type | Label | Signaux détectés |
|------|-------|-----------------|
| A | CC_FAIL | $cc_fail_total |
| B | INCOMPLETE | $(count_pattern "$DECISIONS_LOG" "manquant\|incomplet\|todo") |
| C | CONTRADICTION | $(n=$(grep -c "⏳\|⚠️" "$CONTRADICTION_LOG" 2>/dev/null || true); echo "${n:-0}") |
| D | GUARDRAIL_MISS | $guardrail_total |
| E | EXPERTISE_GAP | $expertise_total |

---

## Instructions pour Sentinel
1. Charger ce fichier dans le contexte
2. Activer Sentinel → choisir [FA] Self-Improvement Loop
3. Sentinel analysera ces données et produira des propositions concrètes
4. Valider les propositions → Bond les applique
FOOTER

ok "Rapport SIL généré : $OUTPUT_FILE"
echo ""
echo "  Prochaine étape :"
echo "  → Ouvrir Sentinel dans VS Code"
echo "  → Choisir [FA] Self-Improvement Loop"
echo "  → Donner le contenu de $OUTPUT_FILE"
