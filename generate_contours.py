#!/usr/bin/env python3
"""
Génération automatique des courbes de niveau
- Lecture des fichiers MNT dans reliefs_mnt/
- Calcul de l'altitude max réelle
- Intervalle adapté (100m, 200m, 500m selon le dénivelé)
- Densification de l'intervalle via CONTOUR_DENSITY (~+50 % de courbes)
- Sortie en GPKG (plus robuste que Shapefile)

NOTE SUR LA PRÉCISION DES DONNÉES (AW3D30)
-----------------------------------------
Les MNT sources sont des modèles numériques de SURFACE (DSM) à 1 seconde d'arc
(~30 m), d'une précision verticale d'environ 5 m (RMSE). Deux conséquences
directes pour l'étiquetage des courbes :
  * les sommets enneigés/englacés sont sous-estimés (Everest : 8767 m dans le
    MNT contre 8848,86 m officiels), donc la plus haute courbe peut se situer
    plusieurs dizaines de mètres sous le vrai sommet ;
  * les emprises 3°x3° incluent des plaines et des zones marines (valeurs
    négatives, ex. -88 m sur la fenêtre du Mont Fuji), d'où des courbes à
    0 m le long des côtes.
Les altitudes produites sont cohérentes entre elles (même source) mais ne
doivent pas être présentées comme des altitudes de référence officielles.

CORRECTION (v2) : les statistiques étaient lues avec un parsing défectueux qui
renvoyait toujours min == max (voir gdal_utils.py), ce qui aurait produit des
intervalles de courbes absurdes. La lecture passe désormais par get_raster_stats().

CORRECTION (v3) : l'appel à gdal_contour passait `-a_srs EPSG:4326`, option qui
n'existe pas pour cet utilitaire (GDAL 3.10 refuse l'argument et n'écrit rien :
« ERROR 1: Unknown argument: -a_srs »). L'option est supprimée : le SCR des
courbes est celui du raster source (EPSG:4326). L'option de format passe aussi
de l'alias déprécié `-f` à `-of`.

ÉVOLUTION (v4) — DENSITÉ DES COURBES
------------------------------------
Les courbes produites étaient jugées trop peu nombreuses. Le nombre de courbes
varie comme 1 / intervalle : pour obtenir +50 % de courbes il faut donc
DIVISER l'intervalle par 1,5 — et non le réduire de 50 % (ce qui en aurait
donné deux fois plus). C'est tout le rôle de CONTOUR_DENSITY.

Les intervalles de référence (500 / 400 / 200 / 100 / 50 / 20 m, plus les
surcharges de CUSTOM_INTERVALS) sont conservés : ils restent la description du
niveau de détail visé. L'intervalle réellement passé à gdal_contour vaut
`intervalle de référence / CONTOUR_DENSITY`, arrondi à la valeur lisible la
plus proche de NICE_INTERVALS. Valeurs obtenues avec la densité par défaut
(1,5) :

    référence   ->  intervalle généré   gain en courbes
    500 m       ->  350 m               +43 %
    400 m       ->  275 m               +45 %
    200 m       ->  125 m               +60 %
    100 m       ->   70 m               +43 %
     50 m       ->   35 m               +43 %
     20 m       ->   15 m               +33 %

Sur les 34 reliefs du projet, l'ensemble représente +47 % de courbes (mesuré
en nombre de niveaux d'altitude). CONTOUR_DENSITY = 1.0 reproduit exactement
les intervalles d'origine ; `--density` permet de régler le facteur sans
toucher au fichier, `--dry-run` affiche le résultat sans rien générer.

ÉVOLUTION (v5) — STYLE QGIS PAR RELIEF
--------------------------------------
`QGIS_style/style_contours.qml` cale ses courbes maîtresses sur des multiples
fixes (épaisseur : `ELEV % 300 == 0` puis `ELEV % 100 == 0` ; étiquettes :
`ELEV % 300 == 0`). Ces règles étaient cohérentes avec les anciens intervalles
(100, 200, 400 et 500 m, tous diviseurs de 100, avec un index tous les 3 pas) :
chaque courbe était à 0,3 et une sur trois était épaissie et étiquetée.

Elles ne le sont plus avec 70, 125, 275 ou 350 m : sur le MontBlanc, une courbe
sur 10 seulement serait épaissie et une sur 200 étiquetée. Le script écrit donc,
à côté de chaque GPKG, un style `<Nom>_contours.qml` dérivé du modèle générique
dont seules les deux expressions dépendant de `ELEV` sont réécrites, avec un
index calculé sur l'intervalle réel du relief :

    courbe intermédiaire : épaisseur 0,1 mm
    courbe maîtresse     : toutes les INDEX_EVERY courbes (5 par défaut),
                           épaisseur 0,35 mm, étiquetée
    courbe majeure       : tous les INDEX_EVERY x MAJOR_FACTOR courbes
                           (15 par défaut), épaisseur 0,7 mm, étiquetée

Exemple pour un pas de 70 m (MontBlanc) : maîtresse tous les 350 m, majeure
tous les 1050 m. `--index-every` règle la fréquence, `--no-style` désactive la
génération, et `--style-only` réécrit les styles seuls (sans recalculer les
courbes) à partir de l'intervalle relu dans chaque GPKG.
"""

import argparse
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Tuple, Optional

from gdal_utils import get_raster_stats

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR = Path(__file__).parent
MNT_DIR = SCRIPT_DIR / "reliefs_mnt"
OUTPUT_DIR = SCRIPT_DIR / "courbes_niveau"

# ---------------------------------------------------------------------------
# Densité des courbes de niveau
# ---------------------------------------------------------------------------
# Facteur appliqué à l'intervalle de référence. Le nombre de courbes étant
# inversement proportionnel à l'intervalle :
#     intervalle final = intervalle de référence / CONTOUR_DENSITY
#     1.5 -> ~50 % de courbes en plus   (valeur par défaut)
#     1.0 -> intervalles d'origine (500/400/200/100/50/20 m)
#     2.0 -> deux fois plus de courbes
CONTOUR_DENSITY = 1.5

# Valeurs d'intervalle considérées comme lisibles sur une carte. L'intervalle
# calculé y est arrondi à la valeur la plus proche, ce qui évite des pas
# inattendus (333 m, 267 m, ...) tout en restant au plus près de la densité
# demandée. Les altitudes des courbes tombent ainsi régulièrement sur des
# valeurs rondes (…, 250, 500, 700, …), ce qui facilite l'étiquetage.
NICE_INTERVALS = (
    5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 75,
    100, 125, 150, 200, 250, 275, 300, 350, 400,
    500, 600, 750, 1000,
)

# Intervalles de référence personnalisés (optionnel).
# ATTENTION : ce sont des intervalles de RÉFÉRENCE, avant application de
# CONTOUR_DENSITY. Ex. MontBlanc : 100 m -> 70 m générés. Pour forcer un
# intervalle exact et ignorer la densité, utiliser --interval.
CUSTOM_INTERVALS = {
    "Everest": 500,
    "K2": 500,
    "Kangchenjunga": 500,
    "Lhotse": 500,
    "Makalu": 500,
    "ChoOyu": 400,
    "Dhaulagiri": 400,
    "Manaslu": 400,
    "Aconcagua": 200,
    "MontBlanc": 100,
    "Kilimandjaro": 200,
    "MontKenya": 100,
}

# ---------------------------------------------------------------------------
# Style QGIS
# ---------------------------------------------------------------------------
# Modèle de style : seules les deux expressions dépendant de ELEV y sont
# remplacées, par des règles calées sur l'intervalle réel du relief (voir
# write_relief_style()). Le reste (couleur, police, placement des étiquettes)
# est repris tel quel.
STYLE_TEMPLATE = SCRIPT_DIR / "QGIS_style" / "style_contours.qml"

# Hiérarchie du style généré : une courbe maîtresse toutes les INDEX_EVERY
# courbes, une courbe majeure tous les INDEX_EVERY x MAJOR_FACTOR.
# Ex. pas de 70 m : maîtresse tous les 350 m, majeure tous les 1050 m.
# MAJOR_FACTOR = 3 garantit au moins une courbe majeure sur les reliefs à gros
# pas (350 m x 15 = 5250 m, atteint par tous les sommets de plus de 6000 m).
INDEX_EVERY = 5
MAJOR_FACTOR = 3

# Épaisseurs (mm) des trois niveaux hiérarchiques
STYLE_WIDTHS = {"intermediaire": 0.1, "maitresse": 0.35, "majeure": 0.7}


# ============================================================================
# FONCTIONS
# ============================================================================

def log(msg: str):
    print(msg)


def get_mnt_stats(mnt_path: Path) -> Tuple[Optional[float], Optional[float]]:
    """
    Récupère les valeurs min et max du MNT.

    Délègue à gdal_utils.get_raster_stats() : l'ancien parsing local de la
    sortie de gdalinfo lisait la valeur du minimum pour le minimum ET pour le
    maximum (les deux champs sont sur la même ligne), ce qui faussait
    l'altitude maximale utilisée pour choisir l'intervalle des courbes.
    """
    return get_raster_stats(mnt_path)


def calculate_interval(max_alt: float) -> int:
    """
    Intervalle de RÉFÉRENCE, adapté à l'altitude maximale (avant densification).
    Règle: plus l'altitude est élevée, plus l'intervalle est grand.
    """
    if max_alt > 8000:
        return 500      # Himalaya
    elif max_alt > 6000:
        return 400      # Très hautes montagnes
    elif max_alt > 4500:
        return 200      # Hautes montagnes (Alpes, Andes)
    elif max_alt > 3000:
        return 100      # Moyennes montagnes
    elif max_alt > 1000:
        return 50       # Collines
    else:
        return 20       # Petits reliefs


def densify_interval(base_interval: int, density: float = CONTOUR_DENSITY) -> int:
    """
    Convertit un intervalle de référence en intervalle réellement généré.

    Le nombre de courbes varie comme 1/intervalle : diviser l'intervalle par
    `density` multiplie donc le nombre de courbes par `density` (densité 1.5
    => ~+50 % de courbes). Le résultat est arrondi à la valeur lisible la plus
    proche de NICE_INTERVALS, sans jamais dépasser l'intervalle de référence
    (une densification ne doit pas raréfier les courbes).

    Args:
        base_interval: intervalle de référence, en mètres.
        density:       facteur de densification (> 0).

    Returns:
        Intervalle en mètres à passer à `gdal_contour -i`.
    """
    if density <= 0:
        raise ValueError(f"CONTOUR_DENSITY doit être > 0 (reçu: {density})")
    if base_interval <= 0:
        raise ValueError(f"Intervalle de référence invalide: {base_interval}")

    # Densité neutre : on reproduit à l'identique les intervalles d'origine,
    # y compris une éventuelle surcharge absente de NICE_INTERVALS.
    if abs(density - 1.0) < 1e-9:
        return base_interval

    target = base_interval / density
    snapped = min(NICE_INTERVALS, key=lambda v: (abs(v - target), v))
    return max(1, min(snapped, base_interval))


def contour_gain(base_interval: int, final_interval: int) -> float:
    """Gain estimé du nombre de courbes, en pourcentage (nombre ~ 1/intervalle)."""
    return (base_interval / final_interval - 1.0) * 100.0


def get_interval_for_relief(
    name: str,
    max_alt: float,
    density: float = CONTOUR_DENSITY,
) -> int:
    """
    Retourne l'intervalle final (densifié) pour un relief.
    Priorité: intervalle personnalisé > calcul automatique, puis densification.
    """
    # 1) Intervalle de référence : surcharge personnalisée ou calcul auto
    base_interval = None
    for key, interval in CUSTOM_INTERVALS.items():
        if key in name:
            base_interval = interval
            source = f"personnalisé ({key})"
            break
    if base_interval is None:
        base_interval = calculate_interval(max_alt)
        source = f"auto, altitude max {max_alt:.0f} m"

    # 2) Densification
    interval = densify_interval(base_interval, density)
    gain = contour_gain(base_interval, interval)
    gain_txt = f"{gain:+.0f} %" if abs(gain) > 0.5 else "identique"
    log(f"  📌 Intervalle: {interval}m "
        f"(référence {base_interval}m, densité ×{density:g} → {gain_txt} de courbes, {source})")
    return interval


def generate_contours(mnt_path: Path, output_gpkg: Path, interval: int) -> bool:
    """
    Génère les courbes de niveau avec gdal_contour.
    Format GPKG (plus robuste que Shapefile).
    """
    if output_gpkg.exists():
        output_gpkg.unlink()

    # NOTE (v3) : gdal_contour n'a jamais eu d'option -a_srs (erreur
    # « Unknown argument: -a_srs » avec GDAL 3.10). Le SCR des courbes est
    # repris de celui du raster source, qui est déjà en WGS 84 (AW3D30),
    # donc la reprojection était de toute façon inutile.
    # De même, l'option de format s'écrit -of ; -f n'est qu'un alias
    # déprécié, voué à disparaître dans les prochaines versions de GDAL.
    cmd = [
        "gdal_contour",
        "-b", "1",
        "-a", "ELEV",
        "-i", str(interval),
        "-of", "GPKG",
        str(mnt_path),
        str(output_gpkg)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        log(f"    ❌ Erreur: {result.stderr[:200]}")
        return False

    if not output_gpkg.exists():
        log(f"    ❌ Fichier non créé")
        return False

    return True


def count_features(gpkg_path: Path) -> str:
    """Compte les entités d'un GPKG via ogrinfo (« Feature Count »)."""
    info = subprocess.run(
        ["ogrinfo", "-so", "-al", str(gpkg_path)],
        capture_output=True, text=True
    )
    for line in info.stdout.split('\n'):
        if "Feature Count:" in line:
            return line.split(":")[1].strip()
    return "?"


# ============================================================================
# STYLE QGIS PAR RELIEF
# ============================================================================

# Les deux seules expressions du modèle qui dépendent de ELEV. Elles sont
# repérées par leur contenu (et non par un numéro de ligne) pour rester
# robustes à une réédition du modèle dans QGIS.
_WIDTH_EXPR_RE = re.compile(r'(<Option value=")if\(\s*&quot;ELEV&quot;[^"]*(" name="expression")')
_LABEL_EXPR_RE = re.compile(r'fieldName="if\([^"]*&quot;ELEV&quot;[^"]*\)"')


def index_steps(interval: int, index_every: int = INDEX_EVERY,
                major_factor: int = MAJOR_FACTOR) -> Tuple[int, int]:
    """Pas (en m) des courbes maîtresses et majeures pour un intervalle donné."""
    index_step = interval * index_every
    return index_step, index_step * major_factor


def build_style_expressions(interval: int, index_every: int = INDEX_EVERY,
                            major_factor: int = MAJOR_FACTOR) -> Tuple[str, str]:
    """
    Construit les expressions QML (largeur, étiquette) pour un intervalle.

    Les valeurs sont échappées pour un attribut XML : guillemets en `&quot;` et
    saut de ligne en `&#xa;`, comme dans le modèle fourni.
    """
    index_step, major_step = index_steps(interval, index_every, major_factor)
    w = STYLE_WIDTHS
    width = (
        f'if( &quot;ELEV&quot; %{major_step}=0,{w["majeure"]},&#xa;'
        f'if(&quot;ELEV&quot; %{index_step}=0, {w["maitresse"]},{w["intermediaire"]}))'
    )
    label = f'if(&quot;ELEV&quot;%{index_step}=0, &quot;ELEV&quot; || \'m\',&quot;&quot;)'
    return width, label


def write_relief_style(
    gpkg_path: Path,
    interval: int,
    index_every: int = INDEX_EVERY,
    major_factor: int = MAJOR_FACTOR,
) -> Optional[Path]:
    """
    Écrit `<Nom>_contours.qml` à côté du GPKG, avec des courbes maîtresses
    calées sur l'intervalle réel du relief.

    Le style générique `QGIS_style/style_contours.qml` reste la référence pour
    les intervalles 100/200/400/500 m ; celui-ci s'adresse aux pas densifiés
    (70, 125, 275, 350 m) pour lesquels ses règles `ELEV % 300` / `ELEV % 100`
    ne tombent plus sur un rang fixe.
    """
    if not STYLE_TEMPLATE.exists():
        log(f"    ⚠️  Modèle de style introuvable: {STYLE_TEMPLATE}")
        return None

    qml = STYLE_TEMPLATE.read_text(encoding="utf-8")
    width, label = build_style_expressions(interval, index_every, major_factor)

    qml, n_width = _WIDTH_EXPR_RE.subn(lambda m: m.group(1) + width + m.group(2), qml)
    qml, n_label = _LABEL_EXPR_RE.subn(lambda m: 'fieldName="' + label + '"', qml)

    if n_width != 1 or n_label != 1:
        log(f"    ⚠️  Expressions ELEV introuvables dans le modèle "
            f"(largeur: {n_width}, étiquette: {n_label}) — style non généré")
        return None

    style_path = gpkg_path.with_suffix(".qml")
    style_path.write_text(qml, encoding="utf-8")
    return style_path


def read_interval_from_gpkg(gpkg_path: Path) -> Optional[int]:
    """
    Relit dans un GPKG de courbes l'intervalle réellement utilisé (pas le plus
    fréquent entre deux altitudes consécutives). Sert au mode `--style-only`,
    qui ne doit pas dépendre des paramètres courants du script.
    """
    con = sqlite3.connect(gpkg_path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features'")]
        if not tables:
            return None
        values = [r[0] for r in con.execute(
            f'SELECT DISTINCT ELEV FROM "{tables[0]}" ORDER BY ELEV')]
    except sqlite3.Error:
        return None
    finally:
        con.close()

    steps = [round(b - a) for a, b in zip(values, values[1:]) if b > a]
    if not steps:
        return None
    return Counter(steps).most_common(1)[0][0]


def style_only(output_dir: Path, index_every: int = INDEX_EVERY,
               major_factor: int = MAJOR_FACTOR) -> int:
    """Réécrit les styles des GPKG existants, sans régénérer les courbes."""
    gpkg_files = sorted(output_dir.glob("*/*_contours.gpkg"))
    if not gpkg_files:
        log(f"❌ Aucun GPKG de courbes trouvé dans {output_dir}")
        return 0

    done = 0
    for gpkg in gpkg_files:
        interval = read_interval_from_gpkg(gpkg)
        if interval is None:
            log(f"  ⚠️  {gpkg.name}: intervalle illisible, style ignoré")
            continue
        style_path = write_relief_style(gpkg, interval, index_every, major_factor)
        if style_path:
            index_step, major_step = index_steps(interval, index_every, major_factor)
            log(f"  🎨 {style_path.name:34s} pas {interval:>3d} m → "
                f"maîtresse {index_step:>4d} m, majeure {major_step:>4d} m")
            done += 1
    return done


def process_mnt(
    mnt_path: Path,
    output_dir: Path = OUTPUT_DIR,
    density: float = CONTOUR_DENSITY,
    forced_interval: Optional[int] = None,
    dry_run: bool = False,
    no_style: bool = False,
    index_every: int = INDEX_EVERY,
    major_factor: int = MAJOR_FACTOR,
) -> bool:
    """Traite un fichier MNT et génère les courbes de niveau."""

    name = mnt_path.stem.replace("_mnt", "")

    print(f"\n{'='*50}")
    print(f"🏔️  {name}")

    # 1. Lire les statistiques
    min_alt, max_alt = get_mnt_stats(mnt_path)

    if min_alt is None or max_alt is None:
        log(f"  ❌ Impossible de lire les stats du MNT")
        return False

    log(f"  📈 Altitude: {min_alt:.0f} - {max_alt:.0f}m (dénivelé: {max_alt - min_alt:.0f}m)")

    # Rappel : les altitudes sont issues d'un DSM (surface), pas d'un DTM.
    # Le sommet réel peut dépasser l'altitude max du MNT (neige/glace).
    log(f"  ℹ️  Altitudes DSM AW3D30 (~30 m, RMSE ~5 m) — sommet éventuellement sous-estimé")

    # 2. Déterminer l'intervalle
    if forced_interval is not None:
        interval = forced_interval
        log(f"  📌 Intervalle forcé: {interval}m (densité ignorée)")
    else:
        interval = get_interval_for_relief(name, max_alt, density)

    # Nombre de niveaux d'altitude attendu (approximation : 1 courbe par niveau)
    levels = int((max_alt - min_alt) / interval) + 1
    log(f"  🔢 Niveaux d'altitude attendus: ~{levels}")

    if dry_run:
        if not no_style:
            index_step, major_step = index_steps(interval, index_every, major_factor)
            log(f"  🎨 Style prévu: maîtresse tous les {index_step}m, majeure tous les {major_step}m")
        log(f"  🧪 dry-run : aucune génération")
        return True

    # 3. Créer le dossier de sortie
    relief_dir = output_dir / name
    relief_dir.mkdir(parents=True, exist_ok=True)

    # 4. Générer les courbes
    output_gpkg = relief_dir / f"{name}_contours.gpkg"

    log(f"  🗺️  Génération des courbes (intervalle {interval}m)...")

    if generate_contours(mnt_path, output_gpkg, interval):
        size_mb = output_gpkg.stat().st_size / (1024 * 1024)
        count = count_features(output_gpkg)
        log(f"  ✅ Terminé: {output_gpkg.name} ({size_mb:.1f} MB, {count} courbes)")
        if not no_style:
            style_path = write_relief_style(output_gpkg, interval, index_every, major_factor)
            if style_path:
                index_step, major_step = index_steps(interval, index_every, major_factor)
                log(f"  🎨 Style: {style_path.name} (maîtresse {index_step}m, majeure {major_step}m)")
        return True
    else:
        log(f"  ❌ Échec de la génération")
        return False


# ============================================================================
# MAIN
# ============================================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Génération automatique des courbes de niveau",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  ./generate_contours.py                      # tous les reliefs, densité 1.5 + styles\n"
            "  ./generate_contours.py --dry-run            # affiche les intervalles sans générer\n"
            "  ./generate_contours.py --style-only         # réécrit les .qml des GPKG existants\n"
            "  ./generate_contours.py --style-only --index-every 3      # maîtresse 1 courbe sur 3\n"
            "  ./generate_contours.py --density 2.0        # deux fois plus de courbes\n"
            "  ./generate_contours.py --density 1.0 --no-style          # intervalles d'origine\n"
            "  ./generate_contours.py --single Everest --interval 250   # intervalle exact imposé\n"
        ),
    )
    parser.add_argument("--single", type=str, default=None,
                        help="Traiter un seul relief (nom exact)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Forcer un intervalle exact (désactive l'adaptation auto ET la densité)")
    parser.add_argument("--density", type=float, default=CONTOUR_DENSITY,
                        help=f"Facteur de densification des courbes "
                             f"(défaut: {CONTOUR_DENSITY:g} → ~+50 %% ; 1.0 = intervalles d'origine)")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help=f"Dossier de sortie (défaut: {OUTPUT_DIR.name}/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les intervalles choisis sans générer de courbes")
    parser.add_argument("--index-every", type=int, default=INDEX_EVERY,
                        help=f"Une courbe maîtresse (épaissie + étiquetée) toutes les N courbes "
                             f"(défaut: {INDEX_EVERY}, soit {INDEX_EVERY * MAJOR_FACTOR} courbes "
                             f"pour une majeure)")
    parser.add_argument("--no-style", action="store_true",
                        help="Ne pas générer les styles .qml par relief")
    parser.add_argument("--style-only", action="store_true",
                        help="Ne réécrire que les styles .qml, à partir des GPKG existants")
    return parser.parse_args(argv)


def main():
    args = parse_args()

    if args.density <= 0:
        log(f"❌ --density doit être > 0 (reçu: {args.density})")
        sys.exit(1)

    if args.interval is not None and args.interval <= 0:
        log(f"❌ --interval doit être > 0 (reçu: {args.interval})")
        sys.exit(1)

    if args.index_every < 1:
        log(f"❌ --index-every doit être ≥ 1 (reçu: {args.index_every})")
        sys.exit(1)

    output_dir = args.output_dir

    # Mode « styles seuls » : on relit l'intervalle dans chaque GPKG et on
    # réécrit le .qml, sans retoucher aux courbes.
    if args.style_only:
        print("=" * 70)
        print("🎨 RÉÉCRITURE DES STYLES QGIS PAR RELIEF (sans régénérer les courbes)")
        print(f"   Courbe maîtresse toutes les {args.index_every} courbes, "
              f"majeure tous les {args.index_every * MAJOR_FACTOR}")
        print("=" * 70)
        print(f"📁 Sortie: {output_dir}")
        print("=" * 70)
        if not output_dir.exists():
            log(f"❌ Dossier introuvable: {output_dir}")
            sys.exit(1)
        done = style_only(output_dir, args.index_every, MAJOR_FACTOR)
        print("\n" + "=" * 70)
        print(f"✅ {done} style(s) écrit(s) — un .qml par GPKG, à charger dans QGIS")
        print("=" * 70)
        return

    print("=" * 70)
    print("🗺️  GÉNÉRATION DES COURBES DE NIVEAU (VERSION ADAPTATIVE + DENSITÉ)")
    print("   Intervalle adapté à l'altitude maximale de chaque relief")
    print(f"   Densité ×{args.density:g} → environ {(args.density - 1) * 100:+.0f} % de courbes "
          f"par rapport aux intervalles de référence")
    if not args.no_style:
        print(f"   Style par relief : maîtresse toutes les {args.index_every} courbes, "
              f"majeure tous les {args.index_every * MAJOR_FACTOR}")
    print("=" * 70)
    print(f"📁 MNT source: {MNT_DIR}")
    print(f"📁 Sortie courbes: {output_dir}")
    if args.dry_run:
        print("🧪 Mode dry-run : aucune écriture")
    print("=" * 70)

    if not MNT_DIR.exists():
        log(f"❌ Dossier MNT non trouvé: {MNT_DIR}")
        log(f"   Exécutez d'abord download_reliefs.py")
        sys.exit(1)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Lister les fichiers MNT
    mnt_files = sorted(MNT_DIR.glob("*_mnt.tif"))

    if not mnt_files:
        log(f"❌ Aucun fichier MNT trouvé dans {MNT_DIR}")
        sys.exit(1)

    # Filtrer si un seul relief est demandé
    if args.single:
        mnt_files = [f for f in mnt_files if args.single.lower() in f.stem.lower()]
        if not mnt_files:
            log(f"❌ Relief '{args.single}' non trouvé")
            sys.exit(1)
        log(f"🎯 Traitement unique: {args.single}")

    log(f"\n📊 {len(mnt_files)} fichier(s) MNT trouvé(s)")

    # Traitement
    success = 0
    for i, mnt_path in enumerate(mnt_files, 1):
        print(f"\n📌 [{i}/{len(mnt_files)}]")

        if process_mnt(
            mnt_path,
            output_dir=output_dir,
            density=args.density,
            forced_interval=args.interval,
            dry_run=args.dry_run,
            no_style=args.no_style,
            index_every=args.index_every,
            major_factor=MAJOR_FACTOR,
        ):
            success += 1

    print("\n" + "=" * 70)
    if args.dry_run:
        print(f"🧪 DRY-RUN: {success}/{len(mnt_files)} reliefs analysés (rien n'a été généré)")
    else:
        print(f"✅ TERMINÉ: {success}/{len(mnt_files)} reliefs traités")
        print(f"📁 Courbes dans: {output_dir}")
        if not args.no_style:
            print("🎨 Styles par relief: <Nom>_contours.qml (à côté de chaque GPKG)")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompu")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        sys.exit(1)
