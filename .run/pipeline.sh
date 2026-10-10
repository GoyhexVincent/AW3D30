#!/usr/bin/env bash
# ============================================================================
# Pipeline complète AW3D30 — reprise et extension du projet
#   1. les 16 montagnes de montagnes_50.csv encore manquantes
#   2. les 50 reliefs de reliefs_50.csv (liste alternative, jamais traitée)
#   3. les courbes de niveau + styles QGIS des reliefs nouvellement produits
#
# Reprise : chaque étape ignore ce qui existe déjà, le script peut donc être
# relancé tel quel après une interruption sans rien refaire.
# ============================================================================
set -u

DIR="/media/oryx/83ef66fd-bcb6-4e48-bf47-a2d6e8d45835/geomatique/AW3D30"
cd "$DIR" || exit 1

RUN="$DIR/.run"
date '+### DÉBUT PIPELINE : %F %T'

# ---------------------------------------------------------------------------
# ÉTAPE 1 — tuiles + fusion des MNT (100 cibles, 34 déjà présentes → ignorées)
# ---------------------------------------------------------------------------
echo
echo "### ÉTAPE 1/3 — TÉLÉCHARGEMENT + FUSION DES MNT : $(date '+%F %T')"
./download_reliefs.py --liste "$RUN/toutes_cibles.csv" --jobs 6
echo "### ÉTAPE 1 terminée (rc=$?) : $(date '+%F %T')"

# ---------------------------------------------------------------------------
# ÉTAPE 2 — courbes de niveau + styles, relief par relief (reprise unitaire)
# ---------------------------------------------------------------------------
echo
echo "### ÉTAPE 2/3 — COURBES DE NIVEAU : $(date '+%F %T')"
while read -r nom; do
    [ -z "$nom" ] && continue
    mnt="reliefs_mnt/${nom}_mnt.tif"
    gpkg="courbes_niveau/${nom}/${nom}_contours.gpkg"

    if [ ! -f "$mnt" ]; then
        echo "⛔ MNT absent — relief ignoré : $nom"
        continue
    fi
    if [ -f "$gpkg" ]; then
        echo "⏭️  Courbes déjà là : $nom"
        continue
    fi

    echo "--- $nom ($(date '+%T'))"
    ./generate_contours.py --single "$nom" || echo "❌ ÉCHEC courbes : $nom"
done < "$RUN/noms_a_traiter.txt"
echo "### ÉTAPE 2 terminée : $(date '+%F %T')"

# ---------------------------------------------------------------------------
# ÉTAPE 3 — bilan
# ---------------------------------------------------------------------------
echo
echo "### ÉTAPE 3/3 — BILAN : $(date '+%F %T')"
python3 "$RUN/bilan.py"
date '+### FIN PIPELINE : %F %T'
