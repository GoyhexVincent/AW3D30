#!/usr/bin/env python3
"""
Déduplication des MNT identiques par liens durs.

Deux reliefs dont la tuile centrale est la même (Everest/Lhotse,
Mont Cook/Mont Tasman, Annapurna/Dhaulagiri, Mont Logan/Mont Saint-Élie,
Monte Pissis/Ojos del Salado) produisent EXACTEMENT le même MNT : la fenêtre
3° × 3° et les tuiles sources sont identiques. Le pipeline les génère donc deux
fois, soit ~445 Mo stockés en double.

Ce script remplace la copie redondante par un **lien dur** : même contenu, même
chemin, même inode, mais un seul bloc sur le disque. Aucune donnée n'est
touchée ; réécrire un des fichiers casse le lien au lieu de contaminer l'autre.

Sécurité : deux fichiers ne sont liés qu'après vérification de la taille ET du
MD5. Toute différence est signalée et laissée intacte.

Usage :
    ./dedupe_mnt.py --dry-run     # montre ce qui serait fait
    ./dedupe_mnt.py               # applique
"""

import argparse
import hashlib
from pathlib import Path

from download_reliefs import load_reliefs_from_csv, relief_safe_name

SCRIPT_DIR = Path(__file__).parent
MNT_DIR = SCRIPT_DIR / "reliefs_mnt"
DEFAULT_CSV = SCRIPT_DIR / "montagnes_50.csv"
CHUNK = 8 * 1024 * 1024


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for bloc in iter(lambda: f.read(CHUNK), b""):
            h.update(bloc)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Liens durs sur les MNT identiques")
    parser.add_argument("--liste", type=str, default=None,
                        help="CSV des reliefs (défaut : montagnes_50.csv)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Afficher les actions sans rien modifier")
    args = parser.parse_args()

    csv_path = Path(args.liste) if args.liste else DEFAULT_CSV
    if not csv_path.is_absolute():
        csv_path = SCRIPT_DIR / csv_path

    reliefs = load_reliefs_from_csv(csv_path)
    if not reliefs:
        raise SystemExit(f"Aucun relief dans {csv_path}")

    # Regrouper les MNT existants par tuile centrale
    groupes = {}
    for name, _lat, _lon, tile in reliefs:
        chemin = MNT_DIR / f"{relief_safe_name(name)}_mnt.tif"
        if chemin.exists():
            groupes.setdefault(tile, []).append((name, chemin))

    libere = 0
    for tile, membres in sorted(groupes.items()):
        if len(membres) < 2:
            continue
        ref_name, ref = membres[0]
        for name, chemin in membres[1:]:
            if chemin.samefile(ref):
                print(f"= {name:<16} déjà lié à {ref_name}")
                continue
            if chemin.stat().st_size != ref.stat().st_size:
                print(f"! {name:<16} taille différente — ignoré")
                continue
            if md5(chemin) != md5(ref):
                print(f"! {name:<16} contenu différent — ignoré")
                continue
            taille = chemin.stat().st_size
            if args.dry_run:
                print(f"[dry] {name:<16} deviendrait un lien dur de {ref_name} "
                      f"({taille/1e6:.0f} Mo récupérables)")
                libere += taille
                continue
            chemin.unlink()
            chemin.hardlink_to(ref)
            libere += taille
            print(f"🔗 {name:<16} lié à {ref_name}  ({taille/1e6:.0f} Mo récupérés)")

    print(f"\nTotal {'récupérable' if args.dry_run else 'récupéré'} : {libere/1e9:.2f} Go")


if __name__ == "__main__":
    main()
