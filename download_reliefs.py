#!/usr/bin/env python3
"""
Téléchargement AW3D30 - VERSION 3
- Téléchargement des tuiles, en parallèle (--jobs) et avec timeout/tentatives
- Extraction des DSM bruts, fusion avec gdal_merge.py -ot Float32 (option A :
  nodata -9999 propagé et déclaré)
- Statistiques min/max fiables + détection des MNT plats
- Reprise : les MNT déjà générés ne sont pas refaits (sauf --force)
- Tuiles non publiées mémorisées (missing_tiles.json) pour ne plus les retenter
- Deux reliefs de même tuile centrale partagent le même MNT (lien dur)
- Les ZIP restent la source ; le cache DSM est supprimé après fusion par défaut
  (--keep-tiles pour le conserver)

NOTE SUR LA PRÉCISION DES DONNÉES (AW3D30)
-----------------------------------------
AW3D30 est un modèle numérique de SURFACE (DSM), pas de terrain (DTM) :
la valeur d'un pixel est l'altitude du premier obstacle rencontré (neige,
glace, canopée, bâti), avec une résolution de 1 seconde d'arc (~30 m) et une
précision verticale annoncée d'environ 5 m (RMSE). Les sommets enneigés ou
englacés sont donc systématiquement SOUS-ESTIMÉS (Everest : 8767 m mesurés
contre 8848,86 m officiels, soit -82 m) et les minima des emprises 3°x3°
correspondent souvent à des plaines ou à des zones marines (valeurs négatives,
ex. -88 m sur la fenêtre du Mont Fuji). Les altitudes issues de ces MNT sont
donc cohérentes entre elles et exploitables pour des courbes de niveau, mais
elles ne doivent pas être présentées comme des altitudes de sommet officielles.
"""

import csv
import json
import os
import subprocess
import sys
import zipfile
import shutil
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Set, List, Tuple, Optional

from gdal_utils import get_raster_metadata, get_raster_stats, is_flat

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "aw3d30_data"
OUTPUT_DIR = SCRIPT_DIR / "reliefs_mnt"
LOG_FILE = SCRIPT_DIR / "download_log.txt"
BASE_URL = "https://www.eorc.jaxa.jp/ALOS/aw3d30/data/release_v2404"
EXTENSION = 1  # 3x3 tuiles
MIN_VALID_SIZE = 100000  # taille minimale (octets) d'un DSM ou d'un MNT valide

# Valeur de nodata du MNT fusionné.
# -9999 est la convention d'AW3D30 lui-même : les tuiles sources sont des Int16
# dont le nodata vaut -9999 (vérifié sur les tuiles extraites). Cette valeur est
# très en dessous de toute altitude terrestre (-430 m à la mer Morte), elle ne
# peut donc pas masquer une donnée réelle. Elle sert à deux choses :
#   1. déclarer en sortie les pixels non couverts (tuiles JAXA non publiées) ;
#   2. propager le nodata déjà présent dans les tuiles sources, que gdal_merge
#      recopierait sinon comme de simples valeurs d'altitude.
# Les zones marines ne sont PAS concernées : dans AW3D30 l'océan vaut 0 m, pas
# -9999 (vérifié : Mont Fuji, Puncak Jaya...). Les courbes de niveau à 0 m le
# long des côtes sont donc conservées.
NODATA_VALUE = -9999

# Robustesse réseau : sans timeout, wget peut rester bloqué indéfiniment quand la
# connexion tombe en cours de run. Une coupure franche doit échouer proprement
# pour que la reprise (relancer le script) fonctionne.
WGET_TIMEOUT = 30      # secondes
WGET_TRIES = 3

# Tuiles non publiées par la JAXA : mémorisées pour ne plus retenter un
# téléchargement (ni créer/supprimer un dossier) à chaque passage.
MISSING_FILE = DATA_DIR / "missing_tiles.json"
_MISSING_LOCK = threading.Lock()


def load_missing_tiles() -> Set[str]:
    if MISSING_FILE.exists():
        try:
            return set(json.loads(MISSING_FILE.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return set()
    return set()


def save_missing_tiles(tiles: Set[str]) -> None:
    try:
        MISSING_FILE.write_text(json.dumps(sorted(tiles), indent=2), encoding="utf-8")
    except OSError:
        pass


MISSING_TILES: Set[str] = load_missing_tiles()


# ============================================================================
# FONCTIONS
# ============================================================================

_LOG_LOCK = threading.Lock()


def log_message(msg: str):
    print(msg, flush=True)
    with _LOG_LOCK:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')


def get_parent_dir(tile_name: str) -> str:
    """
    Calcule le répertoire parent (bloc 5°) pour l'URL.

    JAXA range les tuiles de 1°x1° dans des dossiers de 5°x5° : la tuile
    N045E006.zip se trouve dans le dossier N045E005.

    CORRECTION (v2) : l'ancienne version forçait le bloc « N045E000 » pour
    toute tuile située entre 30-50°N et 0-90°E. Cette exception cassait les
    17 reliefs de cette zone (Karakoram, Alpes, Caucase, Iran, Grèce, Sicile) :
    l'URL répondait 302 vers une page d'erreur au lieu du ZIP. Le calcul
    ci-dessous, appliqué à toutes les tuiles sans exception, donne le bon bloc
    (N045E005 pour les Alpes, N035E075 pour le K2...).
    """
    lat = int(tile_name[1:4]) * (-1 if tile_name[0] == 'S' else 1)
    lon = int(tile_name[5:8]) * (-1 if tile_name[4] == 'W' else 1)
    parent_lat = math.floor(lat / 5) * 5
    parent_lon = math.floor(lon / 5) * 5
    ns = "N" if parent_lat >= 0 else "S"
    ew = "E" if parent_lon >= 0 else "W"
    return f"{ns}{abs(parent_lat):03d}{ew}{abs(parent_lon):03d}"


def get_tile_url(tile_name: str) -> str:
    return f"{BASE_URL}/{get_parent_dir(tile_name)}/{tile_name}.zip"


def is_zip_valid(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            return zf.testzip() is None
    except:
        return False


def download_tile(tile_name: str) -> Optional[Path]:
    """
    Télécharge une tuile si elle n'existe pas ou est corrompue.

    Un timeout et des tentatives bornent l'attente : si le réseau tombe, l'échec
    est franc (au lieu d'un blocage) et un simple relancement reprend le travail.

    Une tuile que la JAXA ne publie pas (le serveur répond 200 avec une page
    d'erreur HTML de ~3 Ko au lieu du ZIP) est mémorisée dans
    `missing_tiles.json` : les passages suivants l'ignorent immédiatement.
    """
    zip_path = DATA_DIR / f"{tile_name}.zip"

    if zip_path.exists() and is_zip_valid(zip_path):
        log_message(f"  📦 ZIP valide: {tile_name}.zip")
        return zip_path

    if zip_path.exists():
        log_message(f"  ⚠️  ZIP corrompu, retéléchargement...")
        zip_path.unlink()

    if tile_name in MISSING_TILES:
        log_message(f"  ⏭️  {tile_name} connue non publiée (missing_tiles.json)")
        return None

    url = get_tile_url(tile_name)
    log_message(f"  📥 Téléchargement: {tile_name}.zip")
    result = subprocess.run(
        ["wget", "-q", "--show-progress",
         f"--timeout={WGET_TIMEOUT}", f"--tries={WGET_TRIES}", "--waitretry=2",
         "-O", str(zip_path), url],
        check=False,
    )

    if zip_path.exists() and is_zip_valid(zip_path):
        log_message(f"  ✅ Téléchargé")
        return zip_path

    # Le fichier existe mais n'est pas un ZIP exploitable.
    if zip_path.exists():
        taille = zip_path.stat().st_size
        zip_path.unlink()
        # rc 0 (page HTML servie en 200) ou 8 (erreur serveur) avec du contenu :
        # la tuile n'est pas publiée. Un rc réseau (4) sans contenu reste
        # transitoire et n'est PAS mémorisé, pour permettre un nouvel essai.
        if taille > 0 and result.returncode in (0, 8):
            with _MISSING_LOCK:
                MISSING_TILES.add(tile_name)
                save_missing_tiles(MISSING_TILES)
            log_message(f"  ⛔ {tile_name} non publiée par la JAXA ({taille} o) — mémorisée")
        else:
            log_message(f"  ❌ Échec téléchargement (réseau, rc={result.returncode})")
        return None

    log_message(f"  ❌ Échec téléchargement (rc={result.returncode})")
    return None


def prefetch_tiles(tiles: List[str], jobs: int) -> None:
    """
    Télécharge en amont, éventuellement en parallèle, les ZIP manquants.

    Les tuiles sont totalement indépendantes : c'est le seul vrai levier de
    temps du pipeline (la fusion ne prend que ~2,5 s, les téléchargements non).
    """
    a_telecharger = []
    for t in tiles:
        z = DATA_DIR / f"{t}.zip"
        if z.exists() and is_zip_valid(z):
            continue
        if t in MISSING_TILES:
            continue
        a_telecharger.append(t)

    if not a_telecharger:
        return

    if jobs <= 1 or len(a_telecharger) == 1:
        for t in a_telecharger:
            download_tile(t)
        return

    log_message(f"  ⬇️  {len(a_telecharger)} tuile(s) en parallèle ({jobs} threads)...")
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futures = {ex.submit(download_tile, t): t for t in a_telecharger}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:  # un échec isolé ne doit pas casser le lot
                log_message(f"  ❌ {futures[fut]}: {e}")


def find_dsm_in_directory(directory: Path) -> Optional[Path]:
    """Cherche un fichier DSM dans le dossier ou ses sous-dossiers."""
    if not directory.exists():
        return None
    
    # Chercher dans le dossier principal
    for pattern in ['*_DSM.tif', '*_STK.tif', '*.tif']:
        files = list(directory.glob(pattern))
        if files:
            return files[0]
    
    # Chercher dans les sous-dossiers
    for subdir in directory.iterdir():
        if subdir.is_dir():
            for pattern in ['*_DSM.tif', '*_STK.tif', '*.tif']:
                files = list(subdir.glob(pattern))
                if files:
                    return files[0]
    return None


def extract_raw_dsm(tile_name: str) -> Optional[Path]:
    """
    Extrait le DSM brut (Byte, non converti) d'une tuile.

    Le dossier aw3d30_data/<tuile>/ sert de CACHE PERSISTANT : une tuile déjà
    extraite n'est ni retéléchargée ni réextraite. Les sommets voisins qui
    partagent les mêmes tuiles (K2 / Broad Peak / Gasherbrum / Nanga Parbat,
    ou les six sommets des Alpes) réutilisent donc le même travail au lieu de
    le refaire intégralement — c'était le principal gaspillage de temps et
    d'espace disque de la v1.

    Seul le fichier <tuile>_dsm.tif est conservé : les fichiers annexes extraits
    du ZIP (masques MSK, notices) sont supprimés pour limiter l'occupation.
    """
    tile_dir = DATA_DIR / tile_name
    raw_dsm_path = tile_dir / f"{tile_name}_dsm.tif"

    # Cache : DSM déjà extrait lors d'un passage précédent
    if raw_dsm_path.exists() and raw_dsm_path.stat().st_size > MIN_VALID_SIZE:
        log_message(f"  ♻️  DSM en cache: {raw_dsm_path.name}")
        return raw_dsm_path

    tile_dir.mkdir(parents=True, exist_ok=True)

    # Télécharger le ZIP
    zip_path = download_tile(tile_name)
    if not zip_path:
        return None

    # Extraire le ZIP si nécessaire
    existing_dsm = find_dsm_in_directory(tile_dir)
    if not existing_dsm:
        log_message(f"  📂 Extraction...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tile_dir)
        except Exception as e:
            log_message(f"  ❌ Erreur extraction: {e}")
            return None
        existing_dsm = find_dsm_in_directory(tile_dir)

    if not existing_dsm:
        log_message(f"  ❌ Aucun DSM trouvé")
        return None

    log_message(f"  📁 DSM: {existing_dsm.name}")

    # Ramener le DSM à la racine du dossier de tuile, sous son nom canonique
    if existing_dsm != raw_dsm_path:
        shutil.move(str(existing_dsm), str(raw_dsm_path))

    # Ne garder que le DSM en cache (supprimer masques, sous-dossiers, notices)
    for leftover in tile_dir.iterdir():
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
        elif leftover != raw_dsm_path:
            leftover.unlink()

    return raw_dsm_path


def get_adjacent_tiles(tile_center: str, extension: int) -> Set[str]:
    """Retourne l'ensemble des tuiles adjacentes (extension = 1 donne 3x3)."""
    lat = int(tile_center[1:4]) * (-1 if tile_center[0] == 'S' else 1)
    lon = int(tile_center[5:8]) * (-1 if tile_center[4] == 'W' else 1)
    
    tiles = set()
    for dy in range(-extension, extension + 1):
        for dx in range(-extension, extension + 1):
            y = lat + dy
            x = lon + dx
            if -90 <= y <= 89 and -180 <= x <= 179:
                ns = "N" if y >= 0 else "S"
                ew = "E" if x >= 0 else "W"
                tiles.add(f"{ns}{abs(y):03d}{ew}{abs(x):03d}")
    return tiles


def merge_tiles(dsm_paths: List[Path], output_path: Path) -> bool:
    """
    Fusionne les DSM bruts avec gdal_merge.py -ot Float32.
    C'est la méthode qui a été validée manuellement.
    """
    if not dsm_paths:
        return False

    # Repartir d'un fichier propre : un .aux.xml résiduel contiendrait des
    # statistiques obsolètes que gdalinfo réutiliserait ensuite (cache PAM).
    aux_path = Path(str(output_path) + ".aux.xml")
    if aux_path.exists():
        aux_path.unlink()
    if output_path.exists():
        output_path.unlink()

    # Si une seule tuile, copier directement (le nodata -9999 de la source,
    # déjà déclaré, est conservé tel quel)
    if len(dsm_paths) == 1:
        shutil.copy(dsm_paths[0], output_path)
        log_message(f"  ✅ Fichier unique: {output_path.name}")
    else:
        # Créer le fichier optfile pour gdal_merge.py
        optfile_path = output_path.parent / f"{output_path.stem}_optfile.txt"
        with open(optfile_path, 'w') as f:
            for p in dsm_paths:
                f.write(f"{p}\n")

        log_message(f"  🔗 Fusion de {len(dsm_paths)} tuiles avec gdal_merge.py...")

        # Commande validée qui fonctionne, complétée par la gestion du nodata :
        #   -n         : ignorer les pixels nodata (-9999) des tuiles sources
        #                (gdal_merge ne les détecte PAS tout seul : sans -n, un
        #                -9999 source serait recopié comme une altitude valide)
        #   -init      : remplir explicitement les trous (tuiles non publiées par
        #                la JAXA) avec le nodata, au lieu du 0 implicite du
        #                fichier GeoTIFF qui simule une plaine au niveau de la mer
        #   -a_nodata  : déclarer cette valeur dans le MNT de sortie, pour que
        #                gdalinfo, gdal_contour et QGIS l'ignorent
        cmd = [
            "gdal_merge.py",
            "-ot", "Float32",
            "-of", "GTiff",
            "-n", str(NODATA_VALUE),
            "-init", str(NODATA_VALUE),
            "-a_nodata", str(NODATA_VALUE),
            "-o", str(output_path),
            "--optfile", str(optfile_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # Nettoyer le fichier optfile
        if optfile_path.exists():
            optfile_path.unlink()

        if result.returncode != 0:
            log_message(f"  ❌ Échec fusion: {result.stderr[:200]}")
            return False

    if not output_path.exists() or output_path.stat().st_size < MIN_VALID_SIZE:
        log_message(f"  ❌ Fichier fusionné trop petit")
        return False

    # Statistiques réelles (l'ancien parsing renvoyait toujours min == max :
    # voir l'en-tête de gdal_utils.py). refresh=True force le recalcul sur le
    # fichier qui vient d'être écrit ; les pixels nodata sont exclus.
    meta = get_raster_metadata(output_path, refresh=True)
    min_val, max_val = meta["min"], meta["max"]

    if min_val is None or max_val is None:
        log_message(f"  ❌ Statistiques illisibles (fichier probablement corrompu)")
        return False

    couverture = ""
    if meta["valid_percent"] is not None and meta["valid_percent"] < 99.995:
        couverture = f", couverture {meta['valid_percent']:.1f}% (reste en nodata)"

    if is_flat(min_val, max_val):
        log_message(f"  ⚠️  MNT plat: min=max={min_val:.0f}m — vérifier les tuiles source")
        return True

    log_message(
        f"  ✅ Fusion réussie: min={min_val:.0f}m, max={max_val:.0f}m "
        f"(dénivelé {max_val - min_val:.0f}m{couverture})"
    )
    return True


def remove_tile_dir(tile_name: str):
    """
    Supprime le dossier d'une tuile (DSM extrait) pour libérer de l'espace.

    Appelé après chaque fusion par défaut : les ZIP restent la source et la
    réextraction ne prend que quelques secondes. L'option --keep-tiles conserve
    ce cache pour éviter les réextractions.
    """
    tile_dir = DATA_DIR / tile_name
    if tile_dir.exists():
        shutil.rmtree(tile_dir)
        log_message(f"  🧹 Nettoyage: {tile_dir} supprimé")

    # Optionnel: supprimer aussi le ZIP (commenté par défaut)
    # zip_path = DATA_DIR / f"{tile_name}.zip"
    # if zip_path.exists():
    #     zip_path.unlink()
    #     log_message(f"  🧹 ZIP supprimé: {zip_path}")


def purge_empty_tile_dirs() -> int:
    """
    Supprime les dossiers de tuiles vides laissés par d'anciens échecs de
    téléchargement (le dossier était créé avant l'appel à wget).
    """
    removed = 0
    if not DATA_DIR.exists():
        return 0
    for entry in DATA_DIR.iterdir():
        if entry.is_dir() and not any(entry.iterdir()):
            entry.rmdir()
            removed += 1
    return removed


def relief_safe_name(name: str) -> str:
    """Nom de fichier sûr pour un relief (règle historique, inchangée)."""
    return (name.replace(" ", "").replace("'", "").replace("-", "")
                .replace("é", "e").replace("è", "e"))


def link_or_copy(src: Path, dst: Path) -> None:
    """
    Crée `dst` comme lien dur de `src` (repli : copie).

    Deux sommets de même tuile centrale produisent le même MNT (Everest/Lhotse,
    Mont Cook/Mont Tasman…) : plutôt que de refusionner et de stocker deux fois
    445 Mo, on partage l'inode. Réécrire l'un des deux casse le lien au lieu de
    modifier l'autre — aucune contamination possible.
    """
    if dst.exists():
        if dst.samefile(src):
            return
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def process_relief(name: str, lat: float, lon: float, tile_center: str,
                   cleanup: bool = False, force: bool = False,
                   jobs: int = 1) -> bool:
    """Traite un relief complet (reprise : ne refait pas un MNT déjà présent)."""
    log_message(f"\n{'='*60}")
    log_message(f"🏔️  {name}")
    log_message(f"   Centre: {lat}°N, {lon}°E | Tuile centrale: {tile_center}")

    output_path = OUTPUT_DIR / f"{relief_safe_name(name)}_mnt.tif"

    # Reprise : un MNT déjà généré n'est pas refait (sauf --force)
    if not force and output_path.exists() and output_path.stat().st_size >= MIN_VALID_SIZE:
        min_val, max_val = get_raster_stats(output_path)  # lecture du cache .aux.xml
        if min_val is not None and max_val is not None:
            apercu = f"{min_val:.0f}–{max_val:.0f}m"
        else:
            apercu = "statistiques inconnues"
        log_message(f"  ⏭️  MNT déjà présent: {output_path.name} ({apercu}) — --force pour refaire")
        return True

    # Déterminer les tuiles à traiter
    tiles = get_adjacent_tiles(tile_center, EXTENSION)
    log_message(f"   Tuiles à traiter: {len(tiles)}")

    # Télécharger en amont (parallèle si --jobs > 1) : les ZIP manquants sont
    # récupérés avant les extractions, ce qui évite d'attendre en série.
    prefetch_tiles(sorted(tiles), jobs)

    # Extraire tous les DSM bruts (cache : les tuiles communes aux reliefs
    # voisins ne sont ni retéléchargées ni réextraites)
    dsm_paths = []
    for tile in sorted(tiles):
        log_message(f"\n   --- Tuile {tile} ---")
        dsm_path = extract_raw_dsm(tile)
        if dsm_path:
            dsm_paths.append(dsm_path)

    if not dsm_paths:
        log_message(f"  ❌ Aucune donnée valide")
        return False

    if len(dsm_paths) < len(tiles):
        log_message(
            f"  ⚠️  {len(tiles) - len(dsm_paths)} tuile(s) indisponible(s) : "
            f"emprise réduite ({len(dsm_paths)}/{len(tiles)} tuiles), "
            f"les zones non couvertes seront en nodata ({NODATA_VALUE} m)"
        )

    # Fusionner les tuiles
    if merge_tiles(dsm_paths, output_path):
        # Nettoyer le cache des tuiles si explicitement demandé
        if cleanup:
            for tile in sorted(tiles):
                remove_tile_dir(tile)
        size_mb = output_path.stat().st_size / (1024 * 1024)
        log_message(f"  ✅ MNT final: {output_path} ({size_mb:.0f} MB)")
        return True
    else:
        log_message(f"  ❌ Échec de la fusion")
        return False


def load_reliefs_from_csv(csv_path: Path) -> List[Tuple[str, float, float, str]]:
    """Charge la liste des reliefs depuis un fichier CSV."""
    reliefs = []
    if not csv_path.exists():
        return reliefs
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('nom') or row.get('Nom') or row.get('name')
            lat_str = row.get('latitude') or row.get('Latitude') or row.get('lat')
            lon_str = row.get('longitude') or row.get('Longitude') or row.get('lon')
            tile = row.get('tuile') or row.get('Tuile') or row.get('tile')
            if name and lat_str and lon_str and tile:
                try:
                    reliefs.append((name, float(lat_str), float(lon_str), tile))
                except ValueError:
                    log_message(f"⚠️  Erreur coordonnées pour {name}")
    return reliefs


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Téléchargement et fusion AW3D30")
    parser.add_argument("--single", type=str, default=None,
                        help="Traiter un seul relief (nom exact)")
    parser.add_argument("--force", action="store_true",
                        help="Refaire les MNT déjà présents dans reliefs_mnt/")
    parser.add_argument("--keep-tiles", action="store_true",
                        help="Conserver les DSM extraits en cache après fusion. "
                             "Par défaut ils sont supprimés : les ZIP restent la "
                             "source et la réextraction ne prend que quelques secondes.")
    parser.add_argument("--jobs", type=int, default=4,
                        help="Nombre de téléchargements en parallèle (défaut 4 ; "
                             "1 = série).")
    parser.add_argument("--liste", type=str, default=None,
                        help="CSV de reliefs à traiter (défaut : montagnes_50.csv, "
                             "repli reliefs_50.csv). Permet de traiter une liste "
                             "personnalisée ou de fusionner les deux jeux.")
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs doit valoir au moins 1")

    print("=" * 70)
    print("🏔️  AW3D30 - TÉLÉCHARGEMENT ET FUSION (VERSION 3)")
    print("   Fusion: gdal_merge.py -ot Float32 + nodata -9999 (option A)")
    print("=" * 70)
    print(f"📁 Données: {DATA_DIR}")
    print(f"📁 Sortie MNT: {OUTPUT_DIR}")
    print(f"📐 Extension: {EXTENSION} (bloc {2*EXTENSION+1}x{2*EXTENSION+1})")
    print(f"⬇️  Téléchargements parallèles: {args.jobs}")
    print(f"♻️  Cache DSM: {'conservé (--keep-tiles)' if args.keep_tiles else 'supprimé après fusion (ZIP conservés)'}")
    print(f"⏭️  Reprise: {'désactivée (--force)' if args.force else 'activée (MNT existants ignorés)'}")
    print("=" * 70)

    # Créer les dossiers
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Archiver le log du run précédent plutôt que de l'effacer (traçabilité)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
        stamp = datetime.fromtimestamp(LOG_FILE.stat().st_mtime).strftime("%Y%m%d-%H%M")
        archive = LOG_FILE.with_name(f"download_log_{stamp}.txt")
        suffix = 1
        while archive.exists():
            archive = LOG_FILE.with_name(f"download_log_{stamp}_{suffix}.txt")
            suffix += 1
        LOG_FILE.rename(archive)
        print(f"📝 Log précédent archivé: {archive.name}")

    # Ménage : dossiers de tuiles vides laissés par d'anciens échecs
    removed = purge_empty_tile_dirs()
    if removed:
        log_message(f"🧹 {removed} dossier(s) de tuile vide(s) supprimé(s)")

    # Charger les reliefs (liste personnalisée, sinon montagnes_50 puis reliefs_50)
    if args.liste:
        csv_path = Path(args.liste)
        if not csv_path.is_absolute():
            csv_path = SCRIPT_DIR / csv_path
        reliefs = load_reliefs_from_csv(csv_path)
        if not reliefs:
            log_message(f"❌ Aucun relief chargé depuis {csv_path}")
            sys.exit(1)
    else:
        reliefs = load_reliefs_from_csv(SCRIPT_DIR / "montagnes_50.csv")
        if not reliefs:
            log_message(f"⚠️  montagnes_50.csv vide, repli sur reliefs_50.csv")
            reliefs = load_reliefs_from_csv(SCRIPT_DIR / "reliefs_50.csv")

    if not reliefs:
        log_message("❌ Aucun relief chargé. Vérifiez les fichiers CSV.")
        sys.exit(1)

    log_message(f"📄 Total reliefs: {len(reliefs)}")

    # Filtrer si un seul relief est demandé
    if args.single:
        reliefs = [(n, lat, lon, t) for (n, lat, lon, t) in reliefs if n.lower() == args.single.lower()]
        if not reliefs:
            log_message(f"❌ Relief '{args.single}' non trouvé")
            sys.exit(1)
        log_message(f"🎯 Traitement unique: {args.single}")

    # Pré-téléchargement global : on rassemble les tuiles de tous les reliefs à
    # traiter et on les récupère en parallèle AVANT les fusions. C'est le seul
    # vrai poste de temps du pipeline ; le faire relief par relief le
    # sérialiserait inutilement.
    tuiles_a_venir = set()
    for (name, lat, lon, tile) in reliefs:
        mnt = OUTPUT_DIR / f"{relief_safe_name(name)}_mnt.tif"
        if not args.force and mnt.exists() and mnt.stat().st_size >= MIN_VALID_SIZE:
            continue  # déjà fait, aucun besoin de ses tuiles
        tuiles_a_venir |= get_adjacent_tiles(tile, EXTENSION)
    if tuiles_a_venir:
        manquantes = [t for t in sorted(tuiles_a_venir)
                      if not (DATA_DIR / f"{t}.zip").exists() and t not in MISSING_TILES]
        log_message(f"\n⬇️  Pré-téléchargement: {len(tuiles_a_venir)} tuile(s) requises, "
                    f"{len(manquantes)} à récupérer (parallèle x{args.jobs})")
        prefetch_tiles(sorted(tuiles_a_venir), args.jobs)

    # Traitement. Deux reliefs de même tuile centrale donnent le MÊME MNT
    # (Everest/Lhotse, Mont Cook/Mont Tasman…) : le premier est fusionné, les
    # suivants sont de simples liens durs, sans refusion ni double stockage.
    success = 0
    mnt_par_tuile = {}
    for i, (name, lat, lon, tile) in enumerate(reliefs, 1):
        log_message(f"\n📌 [{i}/{len(reliefs)}]")

        source = mnt_par_tuile.get(tile)
        if source is not None and source.exists():
            destination = OUTPUT_DIR / f"{relief_safe_name(name)}_mnt.tif"
            log_message(f"🏔️  {name}")
            log_message(f"   Tuile centrale {tile} déjà traitée → lien dur vers {source.name}")
            link_or_copy(source, destination)
            success += 1
            continue

        if process_relief(name, lat, lon, tile,
                          cleanup=not args.keep_tiles, force=args.force,
                          jobs=args.jobs):
            success += 1
            mnt = OUTPUT_DIR / f"{relief_safe_name(name)}_mnt.tif"
            if mnt.exists():
                mnt_par_tuile[tile] = mnt

    # Bilan
    print("\n" + "=" * 70)
    print(f"✅ TERMINÉ: {success}/{len(reliefs)} reliefs traités")
    print(f"📁 MNT dans: {OUTPUT_DIR}")
    print(f"📝 Log: {LOG_FILE}")
    print("=" * 70)

    # Afficher les fichiers générés avec leur vraie plage d'altitude
    mnt_files = sorted(OUTPUT_DIR.glob("*_mnt.tif"))
    if mnt_files:
        print(f"\n📄 {len(mnt_files)} MNT disponibles:")
        for f in mnt_files:
            size_mb = f.stat().st_size / (1024 * 1024)
            min_val, max_val = get_raster_stats(f)
            if min_val is not None and max_val is not None:
                plage = f"{min_val:>6.0f} → {max_val:>5.0f} m"
            else:
                plage = "   stats indisponibles"
            print(f"   - {f.stem.replace('_mnt', ''):<18} {plage}   ({size_mb:.0f} MB)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompu")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)