#!/usr/bin/env python3
"""Bilan de fin de pipeline : ce qui existe, ce qui manque, ce qui est douteux."""
import csv
import json
import sys
from pathlib import Path

DIR = Path("/media/oryx/83ef66fd-bcb6-4e48-bf47-a2d6e8d45835/geomatique/AW3D30")
sys.path.insert(0, str(DIR))
from gdal_utils import get_raster_stats  # noqa: E402


def safe(n):
    return (n.replace(" ", "").replace("'", "").replace("-", "")
            .replace("é", "e").replace("è", "e"))


def lignes(csv_path):
    return list(csv.DictReader(open(csv_path, encoding="utf-8")))


mont = lignes(DIR / "montagnes_50.csv")
rel = lignes(DIR / "reliefs_50.csv")
cibles = [(r["nom"], "montagnes") for r in mont] + [(r["nom"], "reliefs") for r in rel]

mnt_dir, ctn_dir = DIR / "reliefs_mnt", DIR / "courbes_niveau"

manque_mnt, manque_gpkg, plats, sans_style, stats = [], [], [], [], []
for nom, origine in cibles:
    s = safe(nom)
    mnt = mnt_dir / f"{s}_mnt.tif"
    gpkg = ctn_dir / s / f"{s}_contours.gpkg"
    qml = ctn_dir / s / f"{s}_contours.qml"
    if not mnt.exists():
        manque_mnt.append((nom, origine))
        continue
    mini, maxi = get_raster_stats(mnt)
    if mini is None:
        stats.append((nom, "stats illisibles"))
    elif abs(maxi - mini) < 1:
        plats.append(nom)
    else:
        stats.append((nom, f"{mini:.0f} → {maxi:.0f} m"))
    if not gpkg.exists():
        manque_gpkg.append((nom, origine))
    elif not qml.exists():
        sans_style.append(nom)

n_mnt = len(list(mnt_dir.glob("*_mnt.tif")))
n_gpkg = len(list(ctn_dir.glob("*/*_contours.gpkg")))
taille_mnt = sum(p.stat().st_size for p in mnt_dir.glob("*_mnt.tif")) / 1e9
taille_ctn = sum(p.stat().st_size for p in ctn_dir.glob("*/*.gpkg")) / 1e9

print("=" * 70)
print("📊 BILAN")
print("=" * 70)
print(f"montagnes_50.csv : {len(mont) - len([m for m in manque_mnt if m[1] == 'montagnes'])}/{len(mont)} MNT")
print(f"reliefs_50.csv   : {len(rel) - len([m for m in manque_mnt if m[1] == 'reliefs'])}/{len(rel)} MNT")
print(f"reliefs_mnt/     : {n_mnt} fichiers .tif ({taille_mnt:.1f} GB)")
print(f"courbes_niveau/  : {n_gpkg} GeoPackage ({taille_ctn:.1f} GB)")
print()
if manque_mnt:
    print(f"❌ MNT manquants ({len(manque_mnt)}) :")
    for nom, origine in manque_mnt:
        print(f"   - {nom} [{origine}]")
else:
    print("✅ Tous les MNT des deux listes sont présents.")
if manque_gpkg:
    print(f"❌ Courbes manquantes ({len(manque_gpkg)}) :")
    for nom, origine in manque_gpkg:
        print(f"   - {nom} [{origine}]")
else:
    print("✅ Toutes les courbes de niveau sont présentes.")
if sans_style:
    print(f"⚠️  Styles .qml manquants ({len(sans_style)}) : {', '.join(sans_style)}")
else:
    print("✅ Un style .qml accompagne chaque GeoPackage.")
if plats:
    print(f"⚠️  MNT plats (min == max) : {', '.join(plats)}")
missing = json.loads((DIR / "aw3d30_data" / "missing_tiles.json").read_text())
print(f"ℹ️  Tuiles non publiées par la JAXA : {len(missing)} → {', '.join(sorted(missing))}")
print("=" * 70)
