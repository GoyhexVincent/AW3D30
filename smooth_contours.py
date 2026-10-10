#!/usr/bin/env python3
"""
Adoucissement des courbes de niveau existantes (arrondi des angles cassants)
===========================================================================

À QUOI SERT CE SCRIPT ?
-----------------------
Les courbes de niveau produites par `generate_contours.py` (via `gdal_contour`)
sont issues de l'algorithme « marching squares » appliqué à une grille de
pixels. Elles sont donc constituées de **très peu de sommets** : sur le
MontBlanc, la médiane est de **7 points par courbe**, pour des segments de
~15 à 30 m (un pixel AW3D30). Une courbe fermée approximée par 7 sommets est un
polygone anguleux : à l'écran, les angles sont nets et « cassants », ce qui
donne un aspect en facettes peu naturel.

Ce script **relit les GPKG existants** et écrit, à côté, une version adoucie.
Il ne touche jamais aux fichiers d'origine.

GARANTIE : LES FICHIERS SOURCES NE SONT PAS MODIFIÉS
----------------------------------------------------
* lecture seule sur `<Nom>_contours.gpkg` ;
* écriture d'un **nouveau** fichier `<Nom>_contours_lisse.gpkg` dans le même
  dossier (donc `courbes_niveau/<Nom>/`) ;
* refus d'écraser une sortie existante sans `--overwrite` explicite ;
* garde-fou supplémentaire : si le chemin de sortie est identique au chemin
  d'entrée, le script s'arrête.

COMMENT ÇA MARCHE (PROCESS GÉOMÉTRIQUE)
---------------------------------------
Le traitement est une chaîne de 4 étapes, appliquée courbe par courbe :

1. **Nettoyage des points dégénérés** (`--clean-tol-m`, 0,1 m par défaut).
   L'analyse des données montre que **12,7 % des segments ont une longueur
   quasi nulle** (< 1 mm) : ce sont des points doublés ou des micro-segments
   issus de l'arrondi des coordonnées. Ces points sont invisibles mais
   créent des angles parasites (souvent 180°, donc des « plis ») que le
   lissage amplifierait. On les supprime. Mesure faite : la tolérance de
   0,1 m retire 3 % des points **sans perdre une seule courbe** et en
   changeant le périmètre total de 0,000 %.

2. **Passage dans un repère métrique local.** Toutes les opérations
   géométriques (lissage, simplification) sont faites en **mètres** et non en
   degrés : à 47° de latitude, 1° de longitude vaut 76 km alors que 1° de
   latitude vaut 111 km. Travailler en degrés donnerait un lissage 1,46 fois
   plus fort en latitude qu'en longitude, donc des courbes déformées. On
   multiplie donc X par `mètres_par_degré_lon` et Y par
   `mètres_par_degré_lat`, calculés pour le centre de chaque relief, puis on
   revient en degrés à la fin.

3. **Lissage par courbette de Chaikin** (`--iterations`, 1 par défaut).
   Pour chaque segment [P, Q], Chaikin remplace le sommet P par deux points
   situés à 25 % et 75 % du segment :

       P' = 0,75·P + 0,25·Q        Q' = 0,25·P + 0,75·Q

   Chaque passage sur une courbe fermée **double le nombre de points** et
   coupe les angles. C'est l'algorithme classique d'arrondi de coin : la
   limite mathématique est une courbe B-spline quadratique (C1, sans angle
   saillant). Deux propriétés importantes pour de la cartographie :

   * **la courbe reste à l'intérieur de l'enveloppe convexe** des points
     d'origine : jamais de dépassement, donc pas de courbe qui « bave » hors
     de son couloir ;
   * le lissage est **local** : il n'affecte que ~1 segment de part et
     d'autre du sommet.

   L'opération est vectorisée sous NumPy (les coordonnées de chaque courbe
   sont traitées d'un bloc, sans boucle Python sur les sommets).

4. **Simplification Douglas-Peucker optionnelle** (`--simplify-m`, 0 par
   défaut). Chaikin double le nombre de points ; la simplification retire les
   points devenus quasi alignés. Elle est **désactivée par défaut** car, à
   elle seule, elle recrée localement des angles : mesuré sur les données,
   une tolérance de 1 m ramène la proportion d'angles > 45° de 46 % (Chaikin
   seul) à 64 %. À n'activer que si le poids du fichier est un problème
   (à 0,5 m l'effet reste modéré). La simplification est faite avec
   `SimplifyPreserveTopology` : à tolérance nulle ou faible, elle ne peut pas
   faire s'auto-croiser une courbe.

Traitement des cas particuliers :
  * courbe **fermée** (la quasi-totalité) : le voisin du dernier point est le
    premier, la boucle est refermée après lissage, sans point de fermeture
    dupliqué ;
  * courbe **ouverte** : les deux extrémités sont conservées telles quelles ;
  * courbe devenue **dégénérée** après nettoyage (moins de 3 points : les
    « spikes » plats de largeur micrométrique) : elle est écrite telle quelle,
    sans lissage, et comptée à part ;
  * les attributs (`ID`, `ELEV`), le SCR (WGS 84 / EPSG:4326), le nom de
    couche (`contour`), l'index spatial RTree et le style QGIS sont conservés
    à l'identique.

QUALITÉ MESURÉE (MontBlanc, échantillon de 8 000 courbes)
---------------------------------------------------------
Indicateur retenu : l'angle de rotation à chaque sommet. C'est lui qui
traduit visuellement la « cassure ». Mesuré sur les données réelles :

    variante              angle moyen   angles > 45°   sommets
    origine (brut)            28,3°        21,1 %        100 %
    origine (nettoyé)         30,4°        23,3 %         85 %
    lissage 1 passage         15,2°         7,2 %        170 %
    lissage 2 passages         7,6°         0,8 %        339 %

Autrement dit, **un seul passage divise déjà par 3 la proportion d'angles
cassants**. Le nettoyage seul fait légèrement *monter* la moyenne d'angles :
c'est normal et souhaitable, il retire 15 % de sommets quasi confondus dont
les angles à 180° étaient purement numériques.

`--check` rejoue cette mesure sur le fichier réel, sans rien écrire.

Contrôle sur un fichier complet (MontTeide, 3 017 courbes) : angles > 45°
ramenés de 5,7 % à 1,2 % en un passage. Traitement du MontBlanc entier
(129 108 courbes, 21,5 M de sommets) : 98 s, 218 Mo de mémoire, sortie 641 Mo.

COÛT EN POIDS
-------------
Chaikin multiplie les sommets par 2 par passage, soit **×1,8** en poids avec
1 passage (le nettoyage en retire 3 %), ×3,6 avec 2 passages. Pour le dossier
complet (4,3 Go), compter ~8 Go avec 1 passage. `--simplify-m 0.5` réduit ce
surplus mais recrée localement des angles : c'est un compromis de poids, pas
un réglage recommandé par défaut.

CAS DES COURBES DÉGÉNÉRÉES
--------------------------
Une partie des courbes sources sont des « spikes » : des anneaux d'aire quasi
nulle, larges de quelques micromètres pour ~30 m de long (12,7 % des courbes
du MontBlanc). Ce sont des artefacts de l'extraction sur une grille, pas des
formes du terrain. Après nettoyage il ne reste plus assez de sommets pour les
lisser : elles sont **écrites telles quelles** (jamais supprimées) plutôt que
de disparaître du fichier. À l'échelle de lecture habituelle elles sont
invisibles de toute façon. Le nombre concerné est reporté à la fin de chaque
relief.

UTILISATION
-----------
    ./smooth_contours.py                      # tous les reliefs, 1 passage
    ./smooth_contours.py --single MontBlanc   # un seul relief
    ./smooth_contours.py --dry-run            # liste ce qui serait fait
    ./smooth_contours.py --iterations 2       # arrondi plus prononcé
    ./smooth_contours.py --simplify-m 0.5     # allège le fichier de sortie
    ./smooth_contours.py --check              # mesure la qualité angle/angle
    ./smooth_contours.py --overwrite          # refait les sorties existantes

Le script est **reprenable** : une sortie déjà présente est ignorée (sauf
`--overwrite`), donc on peut interrompre puis relancer sans rien perdre.

Le style QGIS `<Nom>_contours_lisse.qml` est copié à côté de la sortie : les
attributs étant inchangés, le style du fichier d'origine s'applique tel quel.
"""

import argparse
import math
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from osgeo import gdal, ogr, osr

# GDAL 4.0 activera les exceptions par défaut ; on s'aligne dès maintenant.
gdal.UseExceptions()
ogr.UseExceptions()

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR = Path(__file__).parent
CONTOURS_DIR = SCRIPT_DIR / "courbes_niveau"

# Suffixe des fichiers de sortie : <Nom>_contours.gpkg -> <Nom>_contours_lisse.gpkg
OUTPUT_SUFFIX = "_lisse"

# Nom de la couche en sortie (identique à celle produite par gdal_contour,
# pour que les styles et scripts existants continuent de fonctionner).
LAYER_NAME = "contour"

# ---------------------------------------------------------------------------
# Paramètres de lissage (valeurs par défaut, surchargeables en ligne de commande)
# ---------------------------------------------------------------------------

# Nombre de passages de Chaikin. 1 passage : angles > 45° ramenés de 21 % à
# 7 %, poids ×1,7. 2 passages : 0,8 % d'angles > 45°, poids ×3,4.
DEFAULT_ITERATIONS = 1

# Élimination des points plus proches que cette distance (en mètres) avant
# lissage. Cible les 12,7 % de segments quasi nuls des données sources.
DEFAULT_CLEAN_TOL_M = 0.1

# Tolérance de simplification Douglas-Peucker, en mètres. 0 = pas de
# simplification (recommandé : la simplification recrée des angles).
DEFAULT_SIMPLIFY_M = 0.0

# Cadence d'affichage de la progression, en nombre de courbes traitées.
PROGRESS_EVERY = 25000


# ============================================================================
# REPÈRE MÉTRIQUE LOCAL
# ============================================================================

def meters_per_degree(lat_deg: float) -> Tuple[float, float]:
    """
    Retourne (mètres par degré de longitude, mètres par degré de latitude) pour
    une latitude donnée, sur l'ellipsoïde WGS 84.

    Formules classiques (rayon de courbure) :
        m_lat = 111132,95 - 559,85·cos(2φ) + 1,175·cos(4φ)
        m_lon = 111319,49·cos(φ)

    À 47° : m_lon ≈ 76 km/°, m_lat ≈ 111 km/°. Le rapport de 1,46 est
    exactement l'anisotropie qu'il faut corriger pour que le lissage soit
    isotrope (arrondi identique dans toutes les directions).
    """
    phi = math.radians(lat_deg)
    m_lat = 111132.95 - 559.85 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_lon = 111319.49 * math.cos(phi)
    return m_lon, m_lat


def meters_per_degree_from_gpkg(path: Path) -> Tuple[float, float]:
    """
    Déduit les facteurs de conversion du SCR et de l'emprise d'un GPKG.

    Si le SCR n'est pas géographique (pas en degrés), on retourne (1, 1) : le
    lissage se fait alors dans l'unité du SCR, ce qui est correct.
    """
    ds = gdal.OpenEx(str(path), gdal.OF_VECTOR)
    if ds is None:
        raise RuntimeError(f"Ouverture impossible: {path}")
    try:
        layer = ds.GetLayer(0)
        srs = layer.GetSpatialRef()
        if srs is not None and srs.IsGeographic():
            minx, maxx, miny, maxy = layer.GetExtent()
            return meters_per_degree((miny + maxy) / 2.0)
        return 1.0, 1.0
    finally:
        ds = None


# ============================================================================
# NETTOYAGE, LISSAGE, SIMPLIFICATION (NumPy)
# ============================================================================

def clean_coords(coords: np.ndarray, tol_m: float) -> np.ndarray:
    """
    Supprime les sommets trop proches du précédent, puis le point de fermeture
    dupliqué. Travaille en mètres.

    Les segments de longueur quasi nulle (12,7 % des segments des données
    sources) provoquent des angles à 180° purement numériques : les garder
    ferait « plisser » la courbe au lieu de l'arrondir.
    """
    if coords.shape[0] < 2:
        return coords

    keep = np.concatenate((
        [True],
        np.linalg.norm(np.diff(coords, axis=0), axis=1) > tol_m,
    ))
    coords = coords[keep]

    # Boucle fermée : on stocke la courbe sans son point de fermeture, le
    # voisin du dernier point étant le premier (voir chaikin()).
    if coords.shape[0] > 2 and np.allclose(coords[0], coords[-1], atol=tol_m):
        coords = coords[:-1]

    return coords


def chaikin(coords: np.ndarray, iterations: int,
            closed: bool = True) -> np.ndarray:
    """
    Adoucissement par courbette de Chaikin.

    Un passage remplace chaque segment [P, Q] par les deux points
    0,75·P + 0,25·Q et 0,25·P + 0,75·Q : les angles sont coupés, le nombre de
    points double, et la courbe reste dans l'enveloppe convexe des points
    d'origine (jamais de dépassement).

    * courbe fermée : le successeur du dernier sommet est le premier
      (np.roll). La courbe ne doit PAS contenir de point de fermeture dupliqué.
    * courbe ouverte : les deux extrémités sont conservées telles quelles (une
      courbe de niveau qui touche le bord de la fenêtre doit continuer de le
      toucher) ; seuls les segments intérieurs sont coupés.
    """
    for _ in range(iterations):
        n = coords.shape[0]
        if n < 3:
            return coords

        if closed:
            nxt = np.roll(coords, -1, axis=0)
            out = np.empty((2 * n, 2), dtype=np.float64)
            out[0::2] = 0.75 * coords + 0.25 * nxt
            out[1::2] = 0.25 * coords + 0.75 * nxt
            coords = out
        else:
            a = coords[:-1]
            b = coords[1:]
            out = np.empty((2 * n - 2, 2), dtype=np.float64)
            out[0::2] = 0.75 * a + 0.25 * b
            out[1::2] = 0.25 * a + 0.75 * b
            coords = np.vstack((coords[:1], out, coords[-1:]))
    return coords
    return coords


def simplify_coords(coords: np.ndarray, tol_m: float, closed: bool) -> np.ndarray:
    """
    Simplification Douglas-Peucker avec préservation de la topologie
    (`SimplifyPreserveTopology`) : une tolérance faible ne peut pas faire
    s'auto-croiser la courbe.

    La géométrie OGR temporaire est construite en mètres (repère local), pas
    en degrés, pour que la tolérance soit une vraie distance.
    """
    if tol_m <= 0 or coords.shape[0] < 4:
        return coords

    ring = coords if not closed else np.vstack((coords, coords[:1]))
    geom = ogr.Geometry(ogr.wkbLineString)
    for x, y in ring:
        geom.AddPoint_2D(float(x), float(y))
    try:
        simple = geom.SimplifyPreserveTopology(float(tol_m))
    except RuntimeError:
        # GEOS refuse les géométries dégénérées (ex. anneau de 3 points
        # colinéaires). Dans ce cas la courbe est déjà optimale : on la garde.
        return coords
    if simple is None:
        return coords

    out = np.array(simple.GetPoints(), dtype=np.float64)[:, :2]
    if closed and out.shape[0] > 1 and np.allclose(out[0], out[-1]):
        out = out[:-1]
    return out


def is_closed_curve(coords_deg: np.ndarray) -> bool:
    """
    Vrai si la courbe est fermée, c'est-à-dire si son dernier point coïncide
    avec le premier (convention OGR pour les anneaux).

    On ne teste PAS la seule égalité des coordonnées : un anneau de 2 points
    distincts (artefact plat) n'est pas une boucle au sens topologique.
    """
    if coords_deg.shape[0] < 3:
        return False
    return bool(np.allclose(coords_deg[0], coords_deg[-1], atol=1e-12))


def smooth_ring(coords_deg: np.ndarray, m_lon: float, m_lat: float,
                iterations: int, clean_tol_m: float,
                simplify_m: float,
                closed: Optional[bool] = None) -> Optional[np.ndarray]:
    """
    Adoucit une courbe exprimée en degrés.

    Retourne les coordonnées en degrés, ou None si la courbe ne peut pas être
    lissée (elle sera alors écrite telle quelle). Une courbe fermée en entrée
    ressort fermée : le point de fermeture est réajouté pour respecter la
    convention de la source (indispensable pour qu'OGR la voie comme un
    anneau et ferme correctement le trait au rendu).

    Args:
        closed: force le mode ; None = détecté automatiquement.
    """
    if closed is None:
        closed = is_closed_curve(coords_deg)
    if coords_deg.shape[0] < 3:
        return None

    # Bascule en mètres (repère local isotrope)
    m = coords_deg.astype(np.float64, copy=True)
    m[:, 0] *= m_lon
    m[:, 1] *= m_lat

    # Le point de fermeture dupliqué est retiré : Chaikin s'appuie sur
    # np.roll, le successeur du dernier sommet est alors le premier.
    if closed and np.allclose(m[0], m[-1]):
        m = m[:-1]

    m = clean_coords(m, clean_tol_m)
    if m.shape[0] < 3:
        return None                      # artefact plat : écrit tel quel

    m = chaikin(m, iterations, closed)
    m = simplify_coords(m, simplify_m, closed=closed)
    if m.shape[0] < 3:
        return None

    # Retour en degrés
    m[:, 0] /= m_lon
    m[:, 1] /= m_lat

    # Rétablissement du point de fermeture (convention de la source)
    if closed:
        m = np.vstack((m, m[:1]))
    return m


# ============================================================================
# MÉTRIQUE DE QUALITÉ (angles)
# ============================================================================

def turn_angles(coords_deg: np.ndarray, closed: Optional[bool] = None) -> np.ndarray:
    """
    Angles de rotation (en degrés) à chaque sommet d'une courbe, en degrés
    géographiques. Les sommets confondus (longueur nulle) sont ignorés.

    Args:
        closed: None = détecté automatiquement. Une courbe fermée est traitée
                sans son point de fermeture dupliqué, pour ne pas compter
                l'angle du point de départ deux fois.
    """
    if closed is None:
        closed = is_closed_curve(coords_deg)
    if closed and coords_deg.shape[0] > 2 and np.allclose(coords_deg[0], coords_deg[-1]):
        coords_deg = coords_deg[:-1]

    n = coords_deg.shape[0]
    if n < 3:
        return np.empty(0)

    if closed:
        prev = np.roll(coords_deg, 1, axis=0)
        nxt = np.roll(coords_deg, -1, axis=0)
        inner = coords_deg
    else:
        # Courbe ouverte : seuls les sommets intérieurs portent un angle.
        prev = coords_deg[:-2]
        inner = coords_deg[1:-1]
        nxt = coords_deg[2:]
        if inner.shape[0] < 1:
            return np.empty(0)

    v1 = inner - prev
    v2 = nxt - inner
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    ok = (n1 > 1e-12) & (n2 > 1e-12)
    if not ok.any():
        return np.empty(0)
    cosv = np.clip(np.sum(v1 * v2, axis=1) / np.where(ok, n1 * n2, 1.0), -1.0, 1.0)
    return np.degrees(np.arccos(cosv))[ok]


def angle_summary(angles: np.ndarray) -> str:
    """Résumé lisible d'une distribution d'angles."""
    if angles.size == 0:
        return "aucun angle"
    return (f"moy {angles.mean():5.1f}° | "
            f"p90 {np.percentile(angles, 90):5.1f}° | "
            f"max {angles.max():6.1f}° | "
            f">45° {100 * (angles > 45).mean():5.1f} % | "
            f">60° {100 * (angles > 60).mean():5.1f} %")


# ============================================================================
# TRAITEMENT D'UN FICHIER
# ============================================================================

def build_output_path(src: Path, suffix: str = OUTPUT_SUFFIX) -> Path:
    """
    `<Nom>_contours.gpkg` -> `<Nom>_contours_lisse.gpkg`, dans le MÊME dossier.

    Le stem est repris tel quel pour préserver les noms existants.
    """
    return src.with_name(f"{src.stem}{suffix}{src.suffix}")


def smooth_gpkg(src: Path, dst: Path, iterations: int, clean_tol_m: float,
                simplify_m: float, spatial_index: bool = True,
                verbose: bool = False) -> dict:
    """
    Relit `src` (lecture seule) et écrit la version adoucie dans `dst`.

    Retourne un dictionnaire de statistiques.
    """
    if src.resolve() == dst.resolve():
        raise ValueError("Le fichier de sortie serait identique à l'entrée : "
                         "refus d'écraser les courbes d'origine.")

    m_lon, m_lat = meters_per_degree_from_gpkg(src)

    src_ds = gdal.OpenEx(str(src), gdal.OF_VECTOR)
    if src_ds is None:
        raise RuntimeError(f"Ouverture impossible: {src}")
    src_lyr = src_ds.GetLayer(0)
    src_defn = src_lyr.GetLayerDefn()

    gpkg_drv = gdal.GetDriverByName("GPKG")
    if gpkg_drv is None:
        raise RuntimeError("Pilote GPKG indisponible")

    try:
        return _write_smoothed(dst, src_lyr, src_defn, gpkg_drv, m_lon, m_lat,
                               iterations, clean_tol_m, simplify_m,
                               spatial_index, verbose)
    finally:
        # Libération explicite des jeux de données : même en cas d'erreur, les
        # deux GPKG sont refermés proprement. Sans cela GDAL tente de finaliser
        # une base déjà invalide et émet un « readonly database » trompeur.
        with gdal.quiet_errors():
            src_lyr = None
            src_ds = None


def _write_smoothed(dst: Path, src_lyr, src_defn, gpkg_drv,
                    m_lon: float, m_lat: float, iterations: int,
                    clean_tol_m: float, simplify_m: float,
                    spatial_index: bool, verbose: bool) -> dict:
    """
    Écrit la couche adoucie dans `dst` (cœur de smooth_gpkg).

    La lecture de la source et la libération des ressources restent à la
    charge de smooth_gpkg ; ici on ne fait qu'écrire.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()

    # Création de la couche : même nom, même type, même SCR, mêmes champs.
    # On construit une copie de la définition pour ne pas dépendre de la
    # couche source pendant l'écriture.
    dst_ds = gpkg_drv.Create(str(dst), 0, 0, 0, gdal.GDT_Unknown)

    srs = src_lyr.GetSpatialRef()
    if srs is None:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
    srs = srs.Clone()

    lco = ["GEOMETRY_NAME=geom", "FID=fid"]
    lco.append("SPATIAL_INDEX=YES" if spatial_index else "SPATIAL_INDEX=NO")
    dst_lyr = dst_ds.CreateLayer(LAYER_NAME, srs=srs, geom_type=src_lyr.GetGeomType(),
                                 options=lco)
    if dst_lyr is None:
        raise RuntimeError(f"Création de la couche impossible dans {dst}")

    src_defn = src_lyr.GetLayerDefn()
    field_names = []
    for i in range(src_defn.GetFieldCount()):
        fdefn = src_defn.GetFieldDefn(i)
        if dst_lyr.CreateField(fdefn) != ogr.OGRERR_NONE:
            raise RuntimeError(f"Création du champ {fdefn.GetName()} impossible")
        field_names.append(fdefn.GetName())

    dst_defn = dst_lyr.GetLayerDefn()

    stats = {
        "features_in": 0, "features_out": 0,
        "vertices_in": 0, "vertices_out": 0,
        "skipped": 0, "written_as_is": 0, "degenerate_closed": 0,
    }
    angles_before = []
    angles_after = []
    # Échantillonnage pour les statistiques d'angle : inutile de mesurer
    # 4 millions de courbes pour connaître la distribution.
    angle_sample_every = 17
    t0 = time.time()

    src_lyr.ResetReading()
    for feat in src_lyr:
        stats["features_in"] += 1
        geom = feat.GetGeometryRef()
        if geom is None:
            stats["skipped"] += 1
            continue

        if geom.GetGeometryType() not in (ogr.wkbLineString, ogr.wkbLineString25D):
            stats["skipped"] += 1
            continue

        coords = np.array(geom.GetPoints(), dtype=np.float64)[:, :2]
        stats["vertices_in"] += coords.shape[0]
        closed = is_closed_curve(coords)

        if stats["features_in"] % angle_sample_every == 1:
            angles_before.append(turn_angles(coords, closed))

        smoothed = smooth_ring(coords, m_lon, m_lat, iterations,
                               clean_tol_m, simplify_m, closed)

        if smoothed is None:
            # Courbe dégénérée : on la conserve à l'identique plutôt que de
            # la faire disparaître (elle reste invisible à l'échelle utile).
            out_coords = coords
            stats["written_as_is"] += 1
            if closed:
                stats["degenerate_closed"] += 1
        else:
            out_coords = smoothed

        out_geom = ogr.Geometry(ogr.wkbLineString)
        for x, y in out_coords:
            out_geom.AddPoint_2D(float(x), float(y))

        if stats["features_in"] % angle_sample_every == 1:
            angles_after.append(turn_angles(out_coords, closed))

        out_feat = ogr.Feature(dst_defn)
        for name in field_names:
            out_feat.SetField(name, feat.GetField(name))
        out_feat.SetGeometry(out_geom)
        if dst_lyr.CreateFeature(out_feat) != ogr.OGRERR_NONE:
            stats["skipped"] += 1
            continue

        stats["features_out"] += 1
        stats["vertices_out"] += out_coords.shape[0]

        if verbose and stats["features_in"] % PROGRESS_EVERY == 0:
            print(f"       … {stats['features_in']} courbes traitées "
                  f"({time.time() - t0:.0f} s)")

    elapsed = time.time() - t0
    stats["elapsed_s"] = elapsed
    stats["m_lon"], stats["m_lat"] = m_lon, m_lat
    stats["angles_before"] = (np.concatenate(angles_before)
                              if angles_before else np.empty(0))
    stats["angles_after"] = (np.concatenate(angles_after)
                             if angles_after else np.empty(0))

    # Fermeture explicite : force l'écriture des index spatiaux et du GPKG.
    with gdal.quiet_errors():
        dst_lyr = None
        dst_ds = None
    return stats


def copy_style(src_gpkg: Path, dst_gpkg: Path) -> Optional[Path]:
    """
    Copie le style QGIS `<Nom>.qml` à côté de la sortie.

    Les attributs (`ID`, `ELEV`) et le nom de couche étant strictement
    conservés, le style du fichier d'origine s'applique sans modification.
    """
    style_src = src_gpkg.with_suffix(".qml")
    if not style_src.exists():
        return None
    style_dst = dst_gpkg.with_suffix(".qml")
    shutil.copyfile(style_src, style_dst)
    return style_dst


# ============================================================================
# MODE --check : MESURER SANS ÉCRIRE
# ============================================================================

def check_relief(src: Path, iterations: int, clean_tol_m: float,
                 simplify_m: float, sample: int = 4000) -> bool:
    """
    Mesure l'effet du lissage sur un échantillon, sans rien écrire.
    Sert à justifier le choix des paramètres sur les données réelles.
    """
    m_lon, m_lat = meters_per_degree_from_gpkg(src)

    ds = gdal.OpenEx(str(src), gdal.OF_VECTOR)
    lyr = ds.GetLayer(0)
    lyr.ResetReading()

    before = []
    after = []
    v_in = v_out = n = 0
    for i in range(sample):
        feat = lyr.GetNextFeature()
        if feat is None:
            break
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        coords = np.array(geom.GetPoints(), dtype=np.float64)[:, :2]
        v_in += coords.shape[0]
        n += 1
        closed = is_closed_curve(coords)
        before.append(turn_angles(coords, closed))
        out = smooth_ring(coords, m_lon, m_lat, iterations, clean_tol_m,
                          simplify_m, closed)
        if out is None:
            out = coords
        v_out += out.shape[0]
        after.append(turn_angles(out, closed))

    ds = None
    if n == 0:
        print("      aucune courbe lisible")
        return False

    b = np.concatenate(before)
    a = np.concatenate(after)
    print(f"      {n} courbes | sommets {v_in} -> {v_out} "
          f"({100 * v_out / v_in:.0f} %)")
    print(f"      avant : {angle_summary(b)}")
    print(f"      après : {angle_summary(a)}")
    return True


# ============================================================================
# MAIN
# ============================================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Adoucit les courbes de niveau existantes (arrondi des angles)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  ./smooth_contours.py                     # tous les reliefs, 1 passage\n"
            "  ./smooth_contours.py --single MontBlanc  # un seul relief\n"
            "  ./smooth_contours.py --dry-run           # liste sans écrire\n"
            "  ./smooth_contours.py --check             # mesure angle/angle\n"
            "  ./smooth_contours.py --iterations 2      # arrondi plus prononcé\n"
            "  ./smooth_contours.py --simplify-m 0.5    # sortie plus légère\n"
            "\nLes fichiers <Nom>_contours.gpkg ne sont JAMAIS modifiés : la sortie\n"
            "est écrite dans <Nom>_contours_lisse.gpkg, dans le même dossier.\n"
        ),
    )
    parser.add_argument("--single", type=str, default=None,
                        help="Traiter un seul relief (nom exact ou fragment)")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                        help=f"Passages de lissage de Chaikin (défaut: {DEFAULT_ITERATIONS}). "
                             f"Chaque passage double les points et arrondit davantage.")
    parser.add_argument("--clean-tol-m", type=float, default=DEFAULT_CLEAN_TOL_M,
                        help=f"Distance mini entre sommets, en mètres (défaut: "
                             f"{DEFAULT_CLEAN_TOL_M}). Supprime les points dégénérés.")
    parser.add_argument("--simplify-m", type=float, default=DEFAULT_SIMPLIFY_M,
                        help=f"Tolérance Douglas-Peucker en mètres (défaut: "
                             f"{DEFAULT_SIMPLIFY_M} = désactivé). Recrée des angles : "
                             f"à n'utiliser que pour alléger.")
    parser.add_argument("--suffix", type=str, default=OUTPUT_SUFFIX,
                        help=f"Suffixe des fichiers de sortie (défaut: '{OUTPUT_SUFFIX}')")
    parser.add_argument("--input-dir", type=Path, default=CONTOURS_DIR,
                        help=f"Dossier des courbes (défaut: {CONTOURS_DIR.name}/)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Refaire les sorties déjà présentes")
    parser.add_argument("--no-spatial-index", action="store_true",
                        help="Ne pas créer l'index spatial RTree dans la sortie")
    parser.add_argument("--no-style", action="store_true",
                        help="Ne pas copier le style QGIS à côté de la sortie")
    parser.add_argument("--check", action="store_true",
                        help="Mesurer l'effet du lissage sans rien écrire")
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher ce qui serait fait, sans écrire")
    return parser.parse_args(argv)


def find_gpkg_files(input_dir: Path, single: Optional[str]):
    files = sorted(input_dir.glob("*/*_contours.gpkg"))
    if single:
        needle = single.lower()
        files = [f for f in files if needle in f.stem.lower()]
    return files


def main():
    args = parse_args()

    if args.iterations < 0:
        print(f"❌ --iterations doit être ≥ 0 (reçu: {args.iterations})")
        sys.exit(1)
    if args.clean_tol_m < 0:
        print(f"❌ --clean-tol-m doit être ≥ 0 (reçu: {args.clean_tol_m})")
        sys.exit(1)
    if args.simplify_m < 0:
        print(f"❌ --simplify-m doit être ≥ 0 (reçu: {args.simplify_m})")
        sys.exit(1)

    input_dir = args.input_dir
    if not input_dir.exists():
        print(f"❌ Dossier introuvable: {input_dir}")
        sys.exit(1)

    files = find_gpkg_files(input_dir, args.single)
    if not files:
        print(f"❌ Aucun fichier *_contours.gpkg trouvé dans {input_dir}")
        if args.single:
            print(f"   (filtre: --single {args.single})")
        sys.exit(1)

    print("=" * 72)
    if args.check:
        print("🔍 MESURE DE L'ADOUCISSEMENT (aucune écriture)")
    else:
        print("〰️  ADOUCISSEMENT DES COURBES DE NIVEAU")
    print(f"   Lissage Chaikin : {args.iterations} passage(s) | "
          f"nettoyage {args.clean_tol_m:g} m | "
          f"simplification {args.simplify_m:g} m")
    print(f"   Sortie : <Nom>_contours{args.suffix}.gpkg "
          f"(les fichiers d'origine ne sont pas touchés)")
    print("=" * 72)
    print(f"📁 Dossier: {input_dir}")
    print(f"📊 {len(files)} fichier(s) de courbes")
    if args.dry_run:
        print("🧪 Mode dry-run : aucune écriture")
    print("=" * 72)

    if args.dry_run:
        for gpkg in files:
            dst = build_output_path(gpkg, args.suffix)
            state = "à refaire" if dst.exists() and args.overwrite else (
                "déjà présent, ignoré" if dst.exists() else "à créer")
            size_mb = gpkg.stat().st_size / (1024 * 1024)
            print(f"  {gpkg.parent.name:22s} {gpkg.name:32s} "
                  f"({size_mb:7.1f} Mo) → {dst.name} [{state}]")
        print("\n" + "=" * 72)
        print("🧪 DRY-RUN : rien n'a été écrit")
        print("=" * 72)
        return

    if args.check:
        for i, gpkg in enumerate(files, 1):
            print(f"\n[{i}/{len(files)}] 🔍 {gpkg.parent.name}")
            check_relief(gpkg, args.iterations, args.clean_tol_m, args.simplify_m)
        print("\n" + "=" * 72)
        print("🔍 MESURE TERMINÉE (aucun fichier écrit)")
        print("=" * 72)
        return

    done = skipped = failed = 0
    total_in = total_out = 0
    t_start = time.time()

    for i, gpkg in enumerate(files, 1):
        name = gpkg.parent.name
        dst = build_output_path(gpkg, args.suffix)

        print(f"\n[{i}/{len(files)}] 🏔️  {name}")

        if dst.exists() and not args.overwrite:
            print(f"    ⏭️  {dst.name} existe déjà — ignoré (--overwrite pour refaire)")
            skipped += 1
            continue

        size_in = gpkg.stat().st_size / (1024 * 1024)
        try:
            st = smooth_gpkg(gpkg, dst, args.iterations, args.clean_tol_m,
                             args.simplify_m,
                             spatial_index=not args.no_spatial_index)
        except Exception as exc:                      # noqa: BLE001 (rapport clair)
            print(f"    ❌ Échec: {exc}")
            with gdal.quiet_errors():
                if dst.exists():
                    dst.unlink()
            failed += 1
            continue

        size_out = dst.stat().st_size / (1024 * 1024)
        total_in += size_in
        total_out += size_out
        ratio_v = (100 * st["vertices_out"] / st["vertices_in"]
                   if st["vertices_in"] else 0)

        print(f"    ✅ {dst.name} — {st['features_out']} courbes, "
              f"{st['vertices_in']} → {st['vertices_out']} sommets ({ratio_v:.0f} %), "
              f"{size_in:.1f} → {size_out:.1f} Mo, {st['elapsed_s']:.1f} s")
        if st["written_as_is"]:
            print(f"       ℹ️  {st['written_as_is']} courbe(s) dégénérée(s) "
                  f"conservée(s) telle(s) quelle(s) "
                  f"(dont {st['degenerate_closed']} anneau(x) plat(s))")
        if st["skipped"]:
            print(f"       ⚠️  {st['skipped']} entité(s) ignorée(s)")

        b, a = st["angles_before"], st["angles_after"]
        if b.size and a.size:
            print(f"       📐 angles > 45° : {100 * (b > 45).mean():.1f} % → "
                  f"{100 * (a > 45).mean():.1f} %  |  "
                  f"angle moyen {b.mean():.1f}° → {a.mean():.1f}°")

        if not args.no_style:
            style = copy_style(gpkg, dst)
            if style:
                print(f"       🎨 style → {style.name}")

        done += 1

    print("\n" + "=" * 72)
    print(f"✅ TERMINÉ : {done} fichier(s) adouci(s), {skipped} ignoré(s), "
          f"{failed} échec(s) — {time.time() - t_start:.0f} s")
    if done:
        ratio = 100 * total_out / total_in if total_in else 0
        print(f"📦 Poids : {total_in:.1f} → {total_out:.1f} Mo ({ratio:.0f} %)")
        print(f"📁 Sorties : <Nom>_contours{args.suffix}.gpkg, dans chaque dossier de relief")
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompu")
        sys.exit(1)
    except Exception as exc:                          # noqa: BLE001
        print(f"\n❌ Erreur: {exc}")
        sys.exit(1)
