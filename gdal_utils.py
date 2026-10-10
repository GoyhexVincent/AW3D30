#!/usr/bin/env python3
"""
Utilitaires GDAL partagés par download_reliefs.py et generate_contours.py.

POURQUOI CE MODULE ?
--------------------
gdalinfo -stats affiche les statistiques d'une bande sur UNE SEULE ligne :

    Minimum=23.000, Maximum=6183.000, Mean=300.042, StdDev=446.630

L'ancien code faisait :

    if "Minimum=" in line: min_val = float(line.split("=")[1].split(",")[0])
    if "Maximum=" in line: max_val = float(line.split("=")[1].split(",")[0])

Les deux tests portent sur la même ligne, et `split("=")[1]` renvoie dans les
deux cas la valeur qui suit « Minimum= ». Conséquence : max_val == min_val
systématiquement. Tous les MNT étaient donc journalisés avec min == max
(ex. Vinson « min=209m, max=209m » alors que le raster va bien de 209 à 4880 m)
et, dans generate_contours.py, l'intervalle des courbes de niveau aurait été
calculé sur une altitude maximale fausse (donc un intervalle absurde).

Ce module lit les statistiques de façon fiable, via la sortie JSON de gdalinfo,
avec repli sur une expression régulière. Il est volontairement basé sur les
outils GDAL en ligne de commande, comme le reste du projet (pas de dépendance
au binding Python osgeo).
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

# Repli si la sortie JSON n'est pas exploitable
_STATS_RE = re.compile(r"Minimum=([-+0-9.eE]+),\s*Maximum=([-+0-9.eE]+)")
_META_RE = {
    "min": re.compile(r"STATISTICS_MINIMUM[=:]\s*\"?([-+0-9.eE]+)"),
    "max": re.compile(r"STATISTICS_MAXIMUM[=:]\s*\"?([-+0-9.eE]+)"),
}


def get_raster_stats(path, refresh: bool = False) -> Tuple[Optional[float], Optional[float]]:
    """
    Retourne (minimum, maximum) réels de la bande 1 d'un raster, hors nodata.

    Les pixels nodata (ex. -9999 pour AW3D30) sont exclus par GDAL : c'est
    exactement le comportement souhaité pour un MNT troué.

    Args:
        path:    chemin du raster.
        refresh: si True, supprime le fichier .aux.xml avant lecture pour forcer
                 un recalcul (sinon GDAL réutilise les statistiques en cache,
                 qui peuvent être obsolètes après une réécriture du raster).

    Returns:
        (min, max) en float, ou (None, None) si illisible.
    """
    meta = get_raster_metadata(path, refresh=refresh)
    return meta["min"], meta["max"]


def get_raster_metadata(path, refresh: bool = False) -> dict:
    """
    Retourne les métadonnées utiles de la bande 1 d'un raster.

    Returns:
        dict avec les clés :
          'min'           : altitude minimale (hors nodata), ou None
          'max'           : altitude maximale (hors nodata), ou None
          'valid_percent' : pourcentage de pixels valides, ou None
          'nodata'        : valeur de nodata déclarée, ou None
    """
    path = Path(path)
    resultat = {"min": None, "max": None, "valid_percent": None, "nodata": None}

    if not path.exists():
        return resultat

    if refresh:
        aux_path = Path(str(path) + ".aux.xml")
        if aux_path.exists():
            aux_path.unlink()

    cmd = ["gdalinfo", "-json", "-stats", "-norat", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)

    # 1) Voie nominale : sortie JSON
    if result.returncode == 0 and result.stdout.strip():
        try:
            info = json.loads(result.stdout)
            bands = info.get("bands") or []
            if bands:
                band = bands[0]
                metadata = band.get("metadata", {}).get("", {})
                mn = metadata.get("STATISTICS_MINIMUM")
                mx = metadata.get("STATISTICS_MAXIMUM")
                vp = metadata.get("STATISTICS_VALID_PERCENT")
                nodata = band.get("noDataValue", info.get("noDataValue"))
                if mn is not None:
                    resultat["min"] = float(mn)
                if mx is not None:
                    resultat["max"] = float(mx)
                if vp is not None:
                    resultat["valid_percent"] = float(vp)
                if nodata is not None:
                    resultat["nodata"] = float(nodata)
                if resultat["min"] is not None and resultat["max"] is not None:
                    return resultat
        except (ValueError, TypeError, KeyError):
            pass

    # 2) Repli : analyse textuelle de la ligne « Minimum=..., Maximum=... »
    text = result.stdout or ""
    match = _STATS_RE.search(text)
    if match:
        resultat["min"] = float(match.group(1))
        resultat["max"] = float(match.group(2))
        return resultat

    # 3) Dernier repli : clés STATISTICS_* du bloc de métadonnées
    mn_match = _META_RE["min"].search(text)
    mx_match = _META_RE["max"].search(text)
    if mn_match and mx_match:
        resultat["min"] = float(mn_match.group(1))
        resultat["max"] = float(mx_match.group(1))

    return resultat


def is_flat(min_val: Optional[float], max_val: Optional[float], tolerance: float = 1.0) -> bool:
    """Vrai si le raster est (quasi) constant, ce qui est anormal pour un MNT de relief."""
    if min_val is None or max_val is None:
        return False
    return (max_val - min_val) <= tolerance
