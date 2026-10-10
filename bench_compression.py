#!/usr/bin/env python3
"""
Banc de mesure des formats de sortie MNT (taille, vitesse, perte).

Fusionne un jeu de tuiles DSM déjà en cache dans plusieurs formats et compare :
  * la taille obtenue ;
  * le temps de fusion ;
  * l'intégrité des valeurs (comparaison bit à bit avec la référence Float32).

Les tuiles AW3D30 sont des Int16 (nodata -9999) : le passage en Int16 est donc
SANS PERTE, contrairement au Float32 utilisé jusqu'ici par le pipeline.

Usage :
    ./bench_compression.py                     # 9 tuiles alpines en cache
    ./bench_compression.py --tuiles N044E005 N044E006 ... --keep
"""

import argparse
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from osgeo import gdal

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "aw3d30_data"
SCRATCH = SCRIPT_DIR / "_bench"

TUILES_PAR_DEFAUT = ["N044E005", "N044E006", "N044E007",
                     "N045E005", "N045E006", "N045E007",
                     "N046E005", "N046E006", "N046E007"]

VARIANTES = {
    "Int16 non compressé": [],
    "Int16 + DEFLATE + TILED + PREDICTOR=2": [
        "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", "-co", "PREDICTOR=2"],
    "Int16 + DEFLATE + 12 threads": [
        "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", "-co", "PREDICTOR=2",
        "-co", "NUM_THREADS=ALL_CPUS"],
    "Int16 + ZSTD + 12 threads": [
        "-co", "COMPRESS=ZSTD", "-co", "TILED=YES", "-co", "PREDICTOR=2",
        "-co", "NUM_THREADS=ALL_CPUS"],
}


def mo(chemin: Path) -> float:
    return chemin.stat().st_size / 1024 / 1024


def chrono(cmd) -> float:
    t0 = time.time()
    resultat = subprocess.run(cmd, capture_output=True, text=True)
    if resultat.returncode != 0:
        raise RuntimeError(f"{cmd[0]} a échoué : {resultat.stderr[:200]}")
    return time.time() - t0


def main():
    parser = argparse.ArgumentParser(description="Banc de mesure des formats MNT")
    parser.add_argument("--tuiles", nargs="*", default=TUILES_PAR_DEFAUT,
                        help="Noms de tuiles à fusionner (doivent être en cache)")
    parser.add_argument("--keep", action="store_true",
                        help="Conserver les fichiers produits dans _bench/")
    args = parser.parse_args()

    chemins = [DATA_DIR / t / f"{t}_dsm.tif" for t in args.tuiles]
    manquants = [c for c in chemins if not c.exists()]
    if manquants:
        raise SystemExit(f"Tuiles absentes du cache : {[c.parent.name for c in manquants]}\n"
                         f"Lancez d'abord ./download_reliefs.py --single <relief>")

    SCRATCH.mkdir(exist_ok=True)
    optfile = SCRATCH / "opt.txt"
    optfile.write_text("\n".join(str(c) for c in chemins) + "\n")
    reference = SCRATCH / "reference_float32.tif"
    vrt = SCRATCH / "mosaique.vrt"

    print(f"Source : {len(chemins)} tuiles Int16 (nodata -9999)\n")

    # Référence : comportement historique du pipeline (Float32, non compressé)
    t_ref = chrono(["gdal_merge.py", "-ot", "Float32", "-of", "GTiff",
                    "-n", "-9999", "-init", "-9999", "-a_nodata", "-9999",
                    "-o", str(reference), "--optfile", str(optfile)])
    print(f"{'Float32 non compressé (actuel)':<40} {t_ref:6.1f} s  ->  {mo(reference):6.0f} Mo")

    t_vrt = chrono(["gdalbuildvrt", "-srcnodata", "-9999", "-vrtnodata", "-9999",
                    str(vrt), *[str(c) for c in chemins]])

    produits = {}
    for label, co in VARIANTES.items():
        sortie = SCRATCH / (label.replace(" ", "_").replace("+", "").replace("=", "") + ".tif")
        t = chrono(["gdalwarp", "-q", "-ot", "Int16", "-dstnodata", "-9999", "-r", "near",
                    *co, str(vrt), str(sortie)])
        produits[label] = sortie
        print(f"{label:<40} {t + t_vrt:6.1f} s  ->  {mo(sortie):6.0f} Mo")

    # Intégrité : les données valides doivent être strictement identiques
    print("\n--- intégrité vs référence Float32 (hors nodata) ---")
    ds_ref = gdal.Open(str(reference))
    ref = ds_ref.GetRasterBand(1).ReadAsArray()
    for label, chemin in produits.items():
        ds = gdal.Open(str(chemin))
        band = ds.GetRasterBand(1)
        arr = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        masque = arr != nodata
        ecart = np.abs(ref[masque].astype("float64") - arr[masque].astype("float64")).max()
        verdict = "✅ sans perte" if ecart == 0 else f"❌ écart {ecart:.0f} m"
        print(f"  {label:<40} {verdict}")
        del ds, band, arr
    del ds_ref, ref

    if not args.keep:
        shutil.rmtree(SCRATCH, ignore_errors=True)
        print("\n(fichiers de test supprimés ; --keep pour les conserver)")


if __name__ == "__main__":
    main()
