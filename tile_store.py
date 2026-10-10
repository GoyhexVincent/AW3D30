#!/usr/bin/env python3
"""
Mode « bibliothèque de tuiles » (approche « tuiles d'abord »).

PROBLÈME
--------
Chaque MNT est une fenêtre 3° × 3° stockée en entier (Float32, ~445 Mo). Les
sommets voisins se recouvrent énormément : les 34 MNT du projet mobilisent 306
« tuiles-slots » pour seulement 205 tuiles JAXA distinctes, et plusieurs paires
de sommets produisent un MNT strictement identique.

PRINCIPE
--------
Ne stocker chaque tuile **qu'une seule fois**, compressée sans perte
(Int16 + DEFLATE + PREDICTOR=2, la source AW3D30 étant déjà de l'Int16), puis
reconstruire n'importe quelle fenêtre 3° × 3° à la demande. C'est le même calcul
que `download_reliefs.py` (option A : nodata -9999), mais la donnée de base
n'est plus dupliquée.

Ordres de grandeur (34 reliefs) :
    MNT actuels (Float32)                    ~12 Go
    magasin des 205 tuiles en Float32        ~10,6 Go
    magasin des 205 tuiles en Int16          ~5,3 Go
    magasin des 205 tuiles Int16 + DEFLATE   ~0,9 Go

USAGE
-----
    ./tile_store.py status
    ./tile_store.py build                 # toutes les tuiles des reliefs du CSV
    ./tile_store.py build --tuiles S044E170 S044E171
    ./tile_store.py mnt MontCook          # reconstruit reliefs_mnt/MontCook_mnt.tif
    ./tile_store.py mnt MontCook --algo vrt --out /tmp/cook.vrt

Le magasin ne remplace rien automatiquement : il est additif. Pour basculer
réellement dessus, supprimez `reliefs_mnt/` après avoir vérifié qu'un `mnt`
reconstruit est identique (le script le compare à l'existant).
"""

import argparse
import subprocess
import sys
from pathlib import Path

from download_reliefs import (
    DATA_DIR, NODATA_VALUE, get_adjacent_tiles, load_reliefs_from_csv,
    prefetch_tiles, relief_safe_name, extract_raw_dsm, remove_tile_dir,
)
from gdal_utils import get_raster_metadata

SCRIPT_DIR = Path(__file__).parent
STORE_DIR = SCRIPT_DIR / "tuiles"
MNT_DIR = SCRIPT_DIR / "reliefs_mnt"
DEFAULT_CSV = SCRIPT_DIR / "montagnes_50.csv"

STORE_CO = [
    "-ot", "Int16",
    "-a_nodata", str(NODATA_VALUE),
    "-co", "COMPRESS=DEFLATE",
    "-co", "TILED=YES",
    "-co", "PREDICTOR=2",
    "-co", "NUM_THREADS=ALL_CPUS",
]


def tuiles_du_csv(csv_path: Path):
    reliefs = load_reliefs_from_csv(csv_path)
    if not reliefs:
        raise SystemExit(f"Aucun relief dans {csv_path}")
    tuiles = set()
    for _name, _lat, _lon, tile in reliefs:
        tuiles |= get_adjacent_tiles(tile, 1)
    return sorted(tuiles)


def cmd_status(args):
    tuiles = sorted(STORE_DIR.glob("*.tif")) if STORE_DIR.exists() else []
    taille = sum(t.stat().st_size for t in tuiles)
    print(f"Magasin : {STORE_DIR}")
    print(f"  {len(tuiles)} tuile(s), {taille/1e6:.0f} Mo")
    manquantes = []
    if args.liste:
        requises = tuiles_du_csv(Path(args.liste))
        manquantes = [t for t in requises if not (STORE_DIR / f"{t}.tif").exists()]
        print(f"  {len(requises) - len(manquantes)}/{len(requises)} tuiles requises présentes")
    if manquantes:
        print(f"  manquantes : {', '.join(manquantes[:20])}"
              + (" …" if len(manquantes) > 20 else ""))


def cmd_build(args):
    if args.tuiles:
        tuiles = sorted(set(args.tuiles))
    else:
        csv_path = Path(args.liste) if args.liste else DEFAULT_CSV
        if not csv_path.is_absolute():
            csv_path = SCRIPT_DIR / csv_path
        tuiles = tuiles_du_csv(csv_path)

    STORE_DIR.mkdir(parents=True, exist_ok=True)
    a_faire = [t for t in tuiles if not (STORE_DIR / f"{t}.tif").exists()]
    print(f"{len(tuiles)} tuile(s) demandée(s), {len(a_faire)} à construire")

    # 1) Récupérer les ZIP manquants en parallèle
    prefetch_tiles(tuiles, args.jobs)

    # 2) Extraire puis compresser chaque tuile dans le magasin
    ok = 0
    for i, tile in enumerate(a_faire, 1):
        print(f"\n[{i}/{len(a_faire)}] {tile}")
        dsm = extract_raw_dsm(tile)   # depuis le cache ZIP, sinon téléchargement
        if not dsm:
            print(f"  ⛔ {tile} indisponible — ignorée")
            continue
        sortie = STORE_DIR / f"{tile}.tif"
        cmd = ["gdal_translate", "-q", *STORE_CO, str(dsm), str(sortie)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not sortie.exists():
            print(f"  ❌ échec gdal_translate : {result.stderr[:200]}")
            continue
        # Le DSM extrait est transitoire : le magasin le remplace.
        if not args.keep_tiles:
            remove_tile_dir(tile)
        taille = sortie.stat().st_size / 1e6
        print(f"  ✅ {sortie.name} ({taille:.1f} Mo)")
        ok += 1

    total = sum(t.stat().st_size for t in STORE_DIR.glob("*.tif"))
    print(f"\n✅ {ok} tuile(s) ajoutée(s) — magasin : "
          f"{len(list(STORE_DIR.glob('*.tif')))} tuile(s), {total/1e6:.0f} Mo")


def cmd_mnt(args):
    csv_path = Path(args.liste) if args.liste else DEFAULT_CSV
    if not csv_path.is_absolute():
        csv_path = SCRIPT_DIR / csv_path
    reliefs = load_reliefs_from_csv(csv_path)
    correspondance = [r for r in reliefs if r[0].lower() == args.nom.lower()]
    if not correspondance:
        raise SystemExit(f"Relief '{args.nom}' absent de {csv_path}")
    name, _lat, _lon, tile_center = correspondance[0]

    disponibles, absentes = [], []
    for t in sorted(get_adjacent_tiles(tile_center, 1)):
        chemin = STORE_DIR / f"{t}.tif"
        (disponibles if chemin.exists() else absentes).append(t)
    if not disponibles:
        raise SystemExit("Aucune tuile du magasin pour ce relief : lancez d'abord "
                         "`./tile_store.py build`.")
    if absentes:
        print(f"⚠️  {len(absentes)} tuile(s) absente(s) : {', '.join(absentes)} "
              f"→ nodata {NODATA_VALUE}")

    sortie = Path(args.out) if args.out else MNT_DIR / f"{relief_safe_name(name)}_mnt.tif"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    aux = Path(str(sortie) + ".aux.xml")
    if aux.exists():
        aux.unlink()

    # Si la cible existe déjà, on la met de côté : cela casse aussi un éventuel
    # lien dur (on ne veut pas réécrire l'inode partagé avec un sommet jumeau)
    # et permet de comparer l'ancien et le nouveau MNT.
    existant = None
    if sortie.exists():
        existant = sortie.with_name(sortie.name + ".bak")
        if existant.exists():
            existant.unlink()
        sortie.rename(existant)

    optfile = sortie.parent / f"{sortie.stem}_optfile.txt"
    optfile.write_text("\n".join(str(STORE_DIR / f"{t}.tif") for t in disponibles) + "\n")

    if args.algo == "vrt":
        # VRT : aucune matérialisation intermédiaire si seul gdal_contour consomme.
        cmd = ["gdalbuildvrt", "-q",
               "-srcnodata", str(NODATA_VALUE), "-vrtnodata", str(NODATA_VALUE),
               str(sortie), *[str(STORE_DIR / f"{t}.tif") for t in disponibles]]
    else:
        cmd = ["gdal_merge.py", "-ot", "Float32", "-of", "GTiff",
               "-n", str(NODATA_VALUE), "-init", str(NODATA_VALUE),
               "-a_nodata", str(NODATA_VALUE),
               "-o", str(sortie), "--optfile", str(optfile)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    optfile.unlink(missing_ok=True)
    if result.returncode != 0:
        # Ne pas laisser la cible absente si l'ancienne version existait
        if existant is not None and existant.exists() and not sortie.exists():
            existant.rename(sortie)
        raise SystemExit(f"Échec {args.algo} : {result.stderr[:300]}")

    meta = get_raster_metadata(sortie, refresh=True)
    couv = "" if meta["valid_percent"] is None else f", couverture {meta['valid_percent']:.1f}%"
    print(f"✅ {sortie} — min={meta['min']:.0f} max={meta['max']:.0f}{couv}")

    # Preuve de non-régression : le MNT reconstruit doit être identique à l'ancien
    if existant is not None:
        from dedupe_mnt import md5
        if existant.stat().st_size == sortie.stat().st_size and md5(existant) == md5(sortie):
            existant.unlink()
            print("   ✔ identique à l'ancien MNT (MD5) — ancien fichier supprimé")
        else:
            print(f"   ⚠️  diffère de l'ancien MNT — conservé sous {existant.name}")


def main():
    parser = argparse.ArgumentParser(description="Bibliothèque de tuiles AW3D30")
    sub = parser.add_subparsers(dest="commande", required=True)

    p_status = sub.add_parser("status", help="État du magasin de tuiles")
    p_status.add_argument("--liste", default=None)
    p_status.set_defaults(func=cmd_status)

    p_build = sub.add_parser("build", help="Construire le magasin de tuiles")
    p_build.add_argument("--liste", default=None, help="CSV des reliefs à couvrir")
    p_build.add_argument("--tuiles", nargs="*", default=None, help="Tuiles explicites")
    p_build.add_argument("--jobs", type=int, default=4)
    p_build.add_argument("--keep-tiles", action="store_true",
                         help="Conserver aussi les DSM extraits (déconseillé)")
    p_build.set_defaults(func=cmd_build)

    p_mnt = sub.add_parser("mnt", help="Reconstruire le MNT d'un relief")
    p_mnt.add_argument("nom")
    p_mnt.add_argument("--liste", default=None)
    p_mnt.add_argument("--out", default=None, help="Chemin de sortie")
    p_mnt.add_argument("--algo", choices=["merge", "vrt"], default="merge")
    p_mnt.set_defaults(func=cmd_mnt)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
