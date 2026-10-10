# AW3D30 — Documentation technique

Chaîne de traitement géomatique qui télécharge les données d'élévation **ALOS
AW3D30** (JAXA, `release_v2404`) autour de 50 grands reliefs mondiaux, les
assemble en **modèles numériques de terrain (MNT)** GeoTIFF 3° × 3°, puis en
dérive des **courbes de niveau** (GPKG) destinées à QGIS.

Ce document décrit l'architecture, les choix techniques, les commandes, les
garanties et les limites du projet. Pour une présentation simple, voir
[`README.md`](README.md).

---

## Table des matières

1. [But et architecture](#1-but-et-architecture)
2. [Source de données et limites](#2-source-de-données-et-limites)
3. [Prérequis et installation](#3-prérequis-et-installation)
4. [Structure du dossier](#4-structure-du-dossier)
5. [Étape 1 — téléchargement et fusion (`download_reliefs.py`)](#5-étape-1--téléchargement-et-fusion-download_reliefspy)
6. [Gestion du nodata (option A)](#6-gestion-du-nodata-option-a)
7. [Étape 2 — courbes de niveau (`generate_contours.py`)](#7-étape-2--courbes-de-niveau-generate_contourspy)
8. [Choix techniques et compromis](#8-choix-techniques-et-compromis)
9. [Référence des options en ligne de commande](#9-référence-des-options-en-ligne-de-commande)
10. [Garanties et contrôles qualité](#10-garanties-et-contrôles-qualité)
11. [Mesures de performance](#11-mesures-de-performance)
12. [Historique des corrections](#12-historique-des-corrections)
13. [Limites connues et pistes d'amélioration](#13-limites-connues-et-pistes-damélioration)
14. [État d'avancement](#14-état-davancement)
15. [Glossaire](#15-glossaire)

---

## 1. But et architecture

### 1.1 Objectif

Produire, pour 50 grands sommets et sites remarquables, des **MNT locaux
cohérents** (une fenêtre de 3° × 3° centrée sur le relief) et les **courbes de
niveau** correspondantes, exploitables dans QGIS. La donnée de base est le
modèle numérique de surface **ALOS AW3D30** de la JAXA (`release_v2404`),
distribué en tuiles de 1° × 1°.

### 1.2 Vue d'ensemble du pipeline

```
                 montagnes_50.csv   (repli : reliefs_50.csv, ou --liste)
                        │
                        ▼
              ┌───────────────────────┐
              │  download_reliefs.py  │   ÉTAPE 1
              │       (version 3)     │
              └───────────┬───────────┘
                          │ 9 tuiles 3×3 autour de la tuile centrale
                          ▼
        wget ──► aw3d30_data/<tuile>.zip          (source, conservée)
                          │ extraction du seul DSM
                          ▼
        aw3d30_data/<tuile>/<tuile>_dsm.tif       (cache, supprimé après fusion)
                          │ gdal_merge.py (option A : nodata -9999)
                          ▼
        reliefs_mnt/<Nom>_mnt.tif                 (Float32, ~445 Mo, EPSG:4326)
                          │
                          ▼
              ┌───────────────────────┐
              │  generate_contours.py │   ÉTAPE 2
              └───────────┬───────────┘
                          │ gdal_contour -a ELEV -i <intervalle> -of GPKG
                          ▼
        courbes_niveau/<Nom>/<Nom>_contours.gpkg  ──► QGIS
                                                   + QGIS_style/style_contours.qml
```

Outils annexes :

* `gdal_utils.py` — lecture fiable des statistiques min/max et du taux de
  couverture (utilisé par les deux étapes).
* `bench_compression.py` — banc de mesure des formats de sortie.
* `dedupe_mnt.py` — remplacement par lien dur des MNT strictement identiques.
* `tile_store.py` — mode « bibliothèque de tuiles » (approche « tuiles
  d'abord »), additif.

### 1.3 Source de données

Les tuiles sont téléchargées à l'URL :

```
https://www.eorc.jaxa.jp/ALOS/aw3d30/data/release_v2404/<bloc 5°>/<tuile>.zip
```

La JAXA range les tuiles de 1° × 1° dans des dossiers de 5° × 5°. Par exemple
`N045E006.zip` se trouve dans `N045E005`. Le bloc est calculé génériquement :

```
dossier_lat = floor(latitude / 5) × 5
dossier_lon = floor(longitude / 5) × 5
```

(voir `get_parent_dir()` dans `download_reliefs.py`, corrigé en v2 — l'ancienne
version forçait `N045E000` pour toute la zone 30–50° N / 0–90° E et cassait
17 reliefs).

---

## 2. Source de données et limites

### 2.1 AW3D30 est un modèle de surface (DSM), pas de terrain (DTM)

La valeur d'un pixel est l'altitude du **premier obstacle rencontré** (neige,
glace, canopée, bâti), et non le sol nu.

| Caractéristique | Valeur |
|---|---|
| Type | Modèle numérique de **surface** (DSM) |
| Tuile | 1° × 1°, 3600 × 3600 pixels |
| Type de données | `Int16` |
| Valeur de nodata | `-9999` |
| Résolution | 1 seconde d'arc (~30 m) |
| Précision verticale annoncée | ~5 m RMSE |
| Projection | EPSG:4326 (WGS 84) |

### 2.2 Sommets enneigés sous-estimés

La neige et la glace font partie du sursol : les calottes sommitales sont donc
mesurées plus bas que le rocher. Constats sur les MNT du projet :

| Relief | Max du MNT | Altitude officielle | Écart |
|---|---:|---:|---:|
| Everest | 8 767 m | 8 848,86 m | **−82 m** |
| Puncak Jaya | 4 839 m | 4 884 m | −45 m |
| Mont Kenya | 5 183 m | 5 199 m | −16 m |
| Vinson | 4 880 m | 4 892 m | −12 m |
| Kilimandjaro | 5 886 m | 5 895 m | −9 m |
| Denali | 6 183 m | 6 190 m | −7 m |
| Mont Fuji | 3 771 m | 3 776 m | −5 m |
| Mont Blanc | 4 813 m | 4 808,7 m | +4 m |
| Aconcagua | 6 961 m | 6 961 m | 0 m |
| Kangchenjunga | 8 590 m | 8 586 m | +4 m |

Les altitudes sont **cohérentes entre elles** (même source, même traitement) et
parfaitement exploitables pour des courbes de niveau ou une stylisation
topographique, mais elles **ne doivent pas être présentées comme des altitudes
officielles**. Pour une précision métrique sur les sommets, il faut une source
complémentaire (LiDAR HD, RGE Alti, GNSS) ou un MNT stéréo plus fin.

### 2.3 L'océan vaut 0 m (ce n'est pas du nodata)

Dans AW3D30, la mer est codée `0 m`, pas `-9999`. Les courbes de niveau à 0 m
qui longent les côtes sont donc **normales** : ce ne sont pas des artefacts.
Corollaire pour le traitement : il ne faut **jamais** convertir l'océan en
nodata ; seul `-9999` est nodata.

### 2.4 Pas angulaire variable selon la latitude

La JAXA conserve un pas au sol voisin de 30 m : la taille des tuiles en degrés
augmente donc en longitude loin de l'équateur. Ce n'est **pas un bug du
pipeline**.

| Zone | Taille de tuile | Pixel X (longitude) | Exemples |
|---|---|---|---|
| Latitudes tempérées | 3600 × 3600 px (1 s d'arc) | ~0,000277° | majorité des reliefs (88 sur 100) |
| ~60–65° N | 1800 × 3600 px (2 s d'arc) | 0,000555° | Denali, Mont Blackburn, Mont Foraker, Hornelen, Eyjafjallajökull |
| Antarctique | 1200 × 3600 px (3 s d'arc) | 0,000833° | Vinson |

Vérifié sur les tuiles sources (21/09/2026) : `ALPSMLC30_N063W152_DSM.tif` fait
1800 × 3600 px à 2″ × 1″, `ALPSMLC30_S079W086_DSM.tif` fait 1200 × 3600 px à
3″ × 1″, tandis que `ALPSMLC30_N045E006_DSM.tif` fait bien 3600 × 3600 px à
1″ × 1″. `gdal_merge.py` reprend le pas du premier fichier de l'optfile : la
résolution d'origine est donc conservée telle quelle, sans rééchantillonnage.

Ces MNT restent à ~30 m au sol, mais leurs pixels sont plus larges en degrés.
**Ne pas comparer naïvement les résolutions en degrés entre reliefs.**

### 2.5 Tuiles non publiées et trous

Quelques tuiles ne sont pas publiées par la JAXA. Le serveur répond alors
**200 avec une page HTML d'erreur d'environ 3 100 octets** au lieu du ZIP.
Elles créent des trous si elles sont intérieures au bloc 3 × 3 :

| Effet | Relief(s) concerné(s) | Tuile(s) non publiée(s) |
|---|---|---|
| trou intérieur → nodata (option A) | Puncak Jaya | `S006E136` |
| trou intérieur → nodata (option A) | Mont Cook, Mont Tasman | `S043E169` |
| trou intérieur → nodata (option A) | Mont Etna | `N036E013` |
| trou intérieur → nodata (option A) | Vésuve, Stromboli | `N039E013`, `N039E014` |
| trou intérieur → nodata (option A) | Verdon Gorge, Verdun Gorge | `N042E005`, `N042E007` |
| trou intérieur → nodata (option A) | Samaria Gorge | `N034E022`, `N035E022` |
| trou intérieur → nodata (option A) | Preikestolen | `N057E005` |
| trou intérieur → nodata (option A) | White Cliffs of Dover | `N052E002` |
| trou intérieur → nodata (option A) | Twelve Apostles | `S040E142` |
| trou intérieur → nodata (option A) | Krakatoa | `S007E104`, `S008E104` |
| trou intérieur → nodata (option A) | Merapi | `S009E109` |
| trou intérieur → nodata (option A) | Pinatubo | `N014E119` |
| trou intérieur → nodata (option A) | Tsingy de Bemaraha | `S020E043` |
| trou intérieur → nodata (option A) | Mauna Kea, Mauna Loa, Kīlauea | `N018W155`, `N018W157`, `N020W155` |
| **bordure** → emprise réduite, sans faux plateau | Mont Teide, Peak of Tenerife | `N029W016`–`N029W018` (→ 3° × 2°) |
| **bordure** → emprise réduite | Montagne Pelée | `N013W063`, `N014W063`, `N015W061`, `N015W063` (→ 2° × 3°) |
| **bordure** → emprise réduite | Kalaupapa Cliffs | `N021W156` (→ 3° × 2°) |
| **bordure** → emprise réduite | Bunda Cliffs | `S033E129`–`S033E131` (→ 3° × 2°) |
| **bordure** → emprise réduite | Cliffs of Saint-John's | `N046W052`–`N048W052` (→ 2° × 3°) |
| **bordure** → emprise réduite | Eyjafjallajökull | `N062W019`–`N062W021` (→ 3° × 2°) |

Le script mémorise les tuiles absentes dans `aw3d30_data/missing_tiles.json`
pour ne plus les retenter (au 21/09/2026 : **40 tuiles**, presque toutes
océaniques ou situées au-delà des limites du levé). Au total, **26 reliefs sur
100** sont concernés : **21** comportent des pixels en nodata et **7** ont une
emprise simplement réduite (Kalaupapa Cliffs et Montagne Pelée cumulent les
deux — la tuile manquante est en bordure du bloc *et* crée un trou dans la
fenêtre). Aucun de ces cas n'est un défaut du pipeline : le nodata `-9999` les
rend propres à l'affichage, et aucune courbe n'y est tracée.

### 2.6 La fenêtre 3° × 3° dépasse le relief ciblé

L'emprise englobe des plaines et des zones marines. Conséquences :

* les minima peuvent être **négatifs** (Mont Fuji −88 m, Mont Blackburn −80 m) ;
* un MNT « ChoOyu » ou « Shishapangma » peut afficher 8 767 m parce que la
  fenêtre contient le sommet de l'Everest : le maximum n'est pas forcément
  celui du sommet ciblé.

### 2.7 Les deux listes `montagnes_50.csv` et `reliefs_50.csv`

`montagnes_50.csv` est la liste **prioritaire** (50 sommets) : appelé sans
option, `download_reliefs.py` traite celle-ci. `reliefs_50.csv` (canyons,
falaises, volcans…) est une liste alternative, **traitée elle aussi depuis le
21/09/2026** (50 / 50). L'option `--liste` permet de traiter un CSV
personnalisé ou de fusionner les deux jeux.

Fusion des deux jeux en une seule passe (le script ignore de lui-même ce qui
existe déjà) :

```bash
./download_reliefs.py --liste .run/toutes_cibles.csv   # 100 cibles, 34 déjà faites
```

Trois reliefs de `reliefs_50.csv` recouvrent exactement un MNT déjà produit
(même tuile centrale, donc même fenêtre 3° × 3°) : `Kilimanjaro` =
`Kilimandjaro`, `Peak of Tenerife` = `MontTeide`, `Columbia River Gorge` =
`MontHood`. Ils sont partagés par **lien dur** au lieu d'être refusionnés,
comme le fait déjà le script pour les reliefs d'une même passe (§5.9).

### 2.8 Noms de fichiers et caractères accentués

`relief_safe_name()` retire espaces, apostrophes et tirets et translittère
seulement `é` et `è` **minuscules**. Trois reliefs de `reliefs_50.csv`
conservent donc un caractère non ASCII dans leur nom de fichier :
`FalaisedÉtretat` (`É`), `Kīlauea` (`ī`), `Eyjafjallajökull` (`ö`). C'est sans
incidence sur Linux, sur GDAL et dans QGIS, mais un transfert vers un système
de fichiers non UTF-8 demanderait une normalisation préalable.

---

## 3. Prérequis et installation

* **Python 3** — bibliothèque standard uniquement pour `download_reliefs.py`,
  `generate_contours.py`, `gdal_utils.py`, `dedupe_mnt.py`, `tile_store.py`.
* **GDAL en ligne de commande** : `gdalinfo`, `gdal_merge.py`, `gdal_contour`,
  `gdal_translate`, `gdalbuildvrt`, `gdalwarp`, `ogrinfo`.
* **wget** pour le téléchargement des tuiles.
* **QGIS 3.22+** pour l'application du style `QGIS_style/style_contours.qml`.
* **Uniquement pour `bench_compression.py`** : les bindings Python GDAL
  (`from osgeo import gdal`) et `numpy`.

Aucune installation particulière n'est requise pour les scripts principaux :
ils s'exécutent directement, et Python ajoute automatiquement le dossier du
script au chemin d'import, ce qui permet d'importer `gdal_utils` (l'usage
typique est de se placer dans le dossier du projet).

```bash
cd /chemin/vers/AW3D30
./download_reliefs.py --single MontBlanc
```

---

## 4. Structure du dossier

| Fichier / dossier | Rôle |
|---|---|
| `download_reliefs.py` | Étape 1 — téléchargement des tuiles, extraction, fusion en MNT |
| `generate_contours.py` | Étape 2 — génération des courbes de niveau (GPKG) |
| `smooth_contours.py` | Étape 2 bis — adoucissement (arrondi des angles) des GPKG existants, en sortie séparée |
| `gdal_utils.py` | Utilitaires GDAL partagés (lecture fiable des statistiques) |
| `bench_compression.py` | Banc de mesure des formats de sortie (taille, vitesse, pertes) |
| `dedupe_mnt.py` | Remplace par un lien dur les MNT identiques (taille + MD5) |
| `tile_store.py` | Mode « bibliothèque de tuiles » (additif) |
| `montagnes_50.csv` | Liste **prioritaire** : 50 sommets (`nom, latitude, longitude, tuile`) |
| `reliefs_50.csv` | Liste des sites remarquables (canyons, falaises, volcans…) — traitée explicitement via `--liste` |
| `aw3d30_data/` | ZIP AW3D30 + `missing_tiles.json` + éventuels caches DSM |
| `reliefs_mnt/` | MNT fusionnés : `<Nom>_mnt.tif` (+ `.aux.xml` = statistiques en cache) |
| `courbes_niveau/` | Sortie de l'étape 2 : `<Nom>/<Nom>_contours.gpkg` (+ `<Nom>_contours_lisse.gpkg` si l'étape 2 bis a été lancée) |
| `tuiles/` | Magasin du mode bibliothèque (`tile_store.py`), s'il est construit |
| `QGIS_style/style_contours.qml` | Style QGIS 3.22 : ligne marron, épaisseur dynamique, étiquettes tous les 300 m |
| `download_log.txt` | Journal de la dernière exécution |
| `download_log_AAAAMMJJ-HHMM*.txt` | Archives des journaux précédents |
| `smooth_contours_exemple.png` | Figure de contrôle : tracé source vs adouci (étape 2 bis) |
| `deepseek_geojson_*.txt` | Artefact externe (GeoJSON des camps de base de l'Everest), hors chaîne |
| `__pycache__/` | Cache Python (généré) |
| `.run/` | Pilotage de la passe de complétion du 21/09/2026 : `pipeline.sh` (idempotent), `toutes_cibles.csv` (100 cibles), `noms_a_traiter.txt`, `bilan.py` (contrôle de complétude) |

État disque observé (21/09/2026, 100 reliefs) : `reliefs_mnt/` ≈ 34 Go (liens
durs comptés une fois), `courbes_niveau/` ≈ 24 Go, `aw3d30_data/` ≈ 6,1 Go.

---

## 5. Étape 1 — téléchargement et fusion (`download_reliefs.py`)

Version 3 du script. Déroulé, dans l'ordre :

### 5.1 Lecture de la liste

Le script lit `montagnes_50.csv` (repli automatique sur `reliefs_50.csv` s'il
est vide), ou le fichier passé à `--liste CHEMIN`. Le CSV doit contenir les
colonnes `nom`, `latitude`, `longitude`, `tuile` (les variantes `Nom`,
`name`, `lat`, `lon`, `tile` sont aussi acceptées). `--single NOM` filtre
ensuite sur le nom exact (insensible à la casse).

### 5.2 Calcul des tuiles 3 × 3

Pour chaque relief, `get_adjacent_tiles(tile_center, 1)` calcule les **9 tuiles**
(3 × 3 = 3° × 3°) autour de la tuile centrale. Le domaine est borné à
`-90 ≤ lat ≤ 89` et `-180 ≤ lon ≤ 179`.

### 5.3 Pré-téléchargement global, en parallèle

Avant les fusions, toutes les tuiles nécessaires aux reliefs à traiter sont
rassemblées puis récupérées **en amont**. C'est le seul vrai poste de temps du
pipeline (la fusion ne prend que ~2,5 s) : la parallélisation (`--jobs`, défaut
4) divise le temps de mur, les tuiles étant indépendantes.

* `prefetch_tiles()` ignore les ZIP déjà présents et valides et les tuiles
  connues comme non publiées.
* Chaque `wget` utilise `--timeout=30 --tries=3 --waitretry=2` : une coupure
  réseau échoue franchement au lieu de bloquer indéfiniment, ce qui permet à la
  reprise de fonctionner.
* Un ZIP existant est validé par `zipfile.testzip()` ; s'il est corrompu il est
  supprimé et retéléchargé.

### 5.4 Mémoïsation des tuiles non publiées

Une tuile que la JAXA ne publie pas (page HTML de ~3 100 octets servie en 200)
est ajoutée à `aw3d30_data/missing_tiles.json` :

* réponse `rc=0` ou `rc=8` avec du contenu → tuile considérée **non publiée**,
  mémorisée ;
* erreur réseau `rc=4` sans contenu → échec **transitoire**, non mémorisé, pour
  permettre un nouvel essai.

Aux exécutions suivantes, la tuile est ignorée immédiatement (pas de `wget`,
pas de dossier créé puis supprimé).

### 5.5 Extraction du DSM et cache

Depuis le ZIP, le script extrait le DSM seul et le range sous
`aw3d30_data/<tuile>/<tuile>_dsm.tif`. Les fichiers annexes (masques MSK,
notices) sont supprimés.

Le dossier `<tuile>/` sert de **cache** : une tuile déjà extraite n'est ni
retéléchargée ni réextraite, ce qui bénéficie aux sommets voisins qui partagent
leurs tuiles (Karakoram, Alpes). En version 3, le cache DSM est **supprimé après
fusion par défaut** (les ZIP restent la source, la réextraction ne prend que
quelques secondes) ; `--keep-tiles` le conserve.

### 5.6 Fusion

Si une seule tuile est disponible, elle est simplement copiée. Sinon :

```bash
gdal_merge.py \
  -ot Float32 \
  -of GTiff \
  -n -9999 \
  -init -9999 \
  -a_nodata -9999 \
  -o reliefs_mnt/<Nom>_mnt.tif \
  --optfile <liste_des_tuiles.txt>
```

Sortie : **10 800 × 10 800 px** (9 × 3600 ; moins si des tuiles de bord
manquent), **EPSG:4326**, ~445 Mo non compressé. Le fichier `.aux.xml` et le
MNT existant sont supprimés avant fusion pour repartir d'un état propre.

### 5.7 Statistiques et détection d'anomalie

Après fusion, `gdal_utils.get_raster_metadata(path, refresh=True)` lit la sortie
JSON de `gdalinfo -json -stats -norat`. Les pixels nodata sont exclus. Le script
affiche `min`, `max`, le dénivelé et, si `valid_percent < 99,995 %`, le taux de
couverture (« reste en nodata »). Un MNT quasi constant (`is_flat`, tolérance
1 m) déclenche un avertissement.

### 5.8 Reprise

Par défaut, un MNT déjà présent (taille ≥ `MIN_VALID_SIZE` = 100 000 octets)
est ignoré, avec affichage de sa plage d'altitude. Un run interrompu peut donc
être relancé sans repayer le travail déjà fait. `--force` refait tout.

### 5.9 Liens durs entre reliefs de même tuile centrale

Deux reliefs dont la tuile centrale est identique produisent **le même MNT**
(même fenêtre 3° × 3°, mêmes tuiles sources). Le premier est fusionné ; les
suivants sont créés comme **liens durs** (`os.link`, repli sur copie) via
`link_or_copy()` : aucun stockage ni calcul en double. Réécrire l'un des
fichiers casse le lien au lieu de contaminer l'autre.

### 5.10 Journal

`download_log.txt` est ouvert en ajout. Au démarrage, un log existant non vide
est **archivé** sous `download_log_AAAAMMJJ-HHMM.txt` (suffixe `_1`, `_2`… en
cas de collision) au lieu d'être écrasé, pour conserver la traçabilité des
exécutions.

---

## 6. Gestion du nodata (option A)

L'« option A » désigne les trois paramètres de nodata passés à `gdal_merge.py`.

| Paramètre | Rôle | Problème évité |
|---|---|---|
| `-n -9999` | Ignorer les pixels nodata des tuiles sources | Sans lui, `gdal_merge` ne détecte pas le nodata source et le recopie comme une altitude valide |
| `-init -9999` | Remplir les zones non couvertes (tuiles non publiées) avec `-9999` | Sans lui, le `0` implicite du GeoTIFF crée un **plateau artificiel au niveau de la mer** (11 % du raster sur le cas de test) |
| `-a_nodata -9999` | Déclarer le nodata dans le MNT de sortie | Permet à `gdalinfo`, `gdal_contour` et QGIS de l'ignorer |

Pourquoi `-9999` :

* c'est le nodata **d'AW3D30 lui-même** (tuiles sources `Int16`) ;
* c'est très en dessous de toute altitude terrestre (−430 m à la mer Morte) :
  il ne peut masquer aucune donnée réelle ;
* l'océan à `0 m` **n'est pas touché** : seul `-9999` est nodata.

**Application et contrôle (11/09/2026).** Les 3 MNT porteurs d'un faux plateau
0 m — Puncak Jaya (`S006E136`), Mont Cook et Mont Tasman (`S043E169`) — ont été
régénérés avec l'option A :

* **12 960 000 pixels** (1° × 1°, soit **11,11 %** du raster) passés en nodata ;
* **0 pixel valide modifié** (écart maximal mesuré : 0 m) ;
* océan `0 m` conservé (15,8 M de pixels pour Puncak Jaya, 32,0 M pour Mont
  Cook / Mont Tasman) ;
* `gdal_contour` ne génère rien dans la zone nodata.

**Cas des tuiles de bordure.** Si la tuile manquante est en bordure du bloc
3 × 3 (et non à un coin), `gdal_merge` réduit simplement l'emprise au lieu de
combler : c'est le cas de Mont Teide (tuiles océaniques `N029W0xx` non
publiées), dont le MNT fait 3° × 2° **sans faux plateau**.

---

## 7. Étape 2 — courbes de niveau (`generate_contours.py`)

> État : **exécutée** ; `courbes_niveau/` contient les 100 reliefs (100 / 100),
> 7 238 755 entités au total, 24 Go de GeoPackage.

Pour chaque fichier `reliefs_mnt/*_mnt.tif` :

1. **Lecture des statistiques** via `gdal_utils.get_raster_stats()` (min/max
   hors nodata).
2. **Choix de l'intervalle de référence** :
   * intervalle personnalisé si le nom du relief contient une clé de
     `CUSTOM_INTERVALS` (recherche par sous-chaîne, **sensible à la casse** : les
     clés reprennent l'orthographe exacte des noms de fichiers) :

     | Clé | Intervalle |
     |---|---:|
     | Everest, K2, Kangchenjunga, Lhotse, Makalu | 500 m |
     | ChoOyu, Dhaulagiri, Manaslu | 400 m |
     | Aconcagua, Kilimandjaro | 200 m |
     | MontBlanc, MontKenya | 100 m |

   * sinon intervalle **automatique** selon l'altitude maximale :
     `> 8000 → 500`, `> 6000 → 400`, `> 4500 → 200`, `> 3000 → 100`,
     `> 1000 → 50`, sinon `20`.
3. **Densification** (`CONTOUR_DENSITY`, défaut `1.5`) : l'intervalle réellement
   passé à `gdal_contour` vaut `intervalle de référence / CONTOUR_DENSITY`,
   arrondi à la valeur la plus proche de `NICE_INTERVALS` (multiples de 5, 25
   ou 50 : 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 75, 100, 125, 150, 200,
   250, 275, 300, 350, 400, 500, 600, 750, 1000).

   Le nombre de courbes varie comme `1 / intervalle` : **diviser** l'intervalle
   par 1,5 ajoute ~50 % de courbes ; le **réduire de 50 %** en aurait donné
   deux fois plus (piège classique).

   | Référence | Généré (×1,5) | Gain en courbes |
   |---:|---:|---:|
   | 500 m | 350 m | +43 % |
   | 400 m | 275 m | +45 % |
   | 200 m | 125 m | +60 % |
   | 100 m | 70 m | +43 % |
   | 50 m | 35 m | +43 % |
   | 20 m | 15 m | +33 % |

   Sur les 100 reliefs du projet, l'ensemble représente **+46 % de niveaux
   d'altitude** (4 564 niveaux cumulés). Répartition des pas réellement
   utilisés : 15 m (5 reliefs), 35 m (22), 70 m (31), 125 m (20), 275 m (11),
   350 m (11). `--density 1.0` restaure à l'identique les intervalles
   d'origine ; `--density 2.0` double le nombre de courbes. Attention : le
   nombre d'**entités** croît un peu plus vite que le nombre de niveaux (±40
   à +130 % selon le relief, cf. Denali), gdal découpant les courbes en
   plusieurs lignes là où le relief est complexe.

   > **Pourquoi ces arrondis** : le style QGIS historique
   > (`QGIS_style/style_contours.qml`) cale ses courbes maîtresses sur
   > `ELEV % 300 == 0` (épaisseur + étiquettes) et `ELEV % 100 == 0`
   > (épaisseur). Ces règles n'étaient cohérentes qu'avec les anciens
   > intervalles, tous diviseurs de 100 (index = 3 pas). D'où le point 6
   > ci-dessous, qui produit un style propre à chaque relief.
4. **Génération** :

   ```bash
   gdal_contour -b 1 -a ELEV -i <intervalle> -of GPKG \
     reliefs_mnt/<Nom>_mnt.tif \
     courbes_niveau/<Nom>/<Nom>_contours.gpkg
   ```

   La bande 1 est l'altitude ; le champ d'altitude s'appelle `ELEV`.

   > **Piège (corrigé en v3)** : `gdal_contour` **n'accepte pas** `-a_srs`
   > (l'option n'a jamais existé pour cet utilitaire). Avec GDAL 3.10 cela
   > produit `ERROR 1: Unknown argument: -a_srs` et **aucune** courbe n'est
   > générée. Le SCR des courbes est repris de celui du raster source, déjà
   > en EPSG:4326. Autre piège de la même famille : l'option de format est
   > `-of` ; l'ancien alias `-f` fonctionne encore en 3.10 mais est déprécié.
5. **Comptage** des entités via `ogrinfo -so -al` (« Feature Count »).
6. **Style QGIS par relief** (`write_relief_style()`) : écrit
   `<Nom>_contours.qml` à côté du GPKG, en repartant du modèle
   `QGIS_style/style_contours.qml` dont **seules les deux expressions dépendant
   de `ELEV`** sont réécrites (largeur via `outlineWidth`, étiquette via le
   `fieldName` du `text-style`). Le reste — ligne marron `93,59,28`, police
   Lato Light 10, placement des étiquettes — est repris tel quel.

   Les règles deviennent, pour un intervalle `i` :

   | Niveau | Condition | Épaisseur | Étiquetée |
   |---|---|---:|---|
   | intermédiaire | sinon | 0,1 mm | non |
   | maîtresse | `ELEV % (5 × i) == 0` | 0,35 mm | oui |
   | majeure | `ELEV % (15 × i) == 0` | 0,7 mm | oui |

   Exemples : pas 70 m (MontBlanc) → maîtresse 350 m, majeure 1050 m ;
   pas 350 m (Everest) → maîtresse 1750 m, majeure 5250 m. Chaque relief a au
   moins une courbe majeure, et 5 à 36 % des entités sont des maîtresses
   (moins qu'avant, où **toutes** les courbes étaient à 0,3 et une sur trois
   étiquetée).

   Les constantes `INDEX_EVERY` (5) et `MAJOR_FACTOR` (3) et le dictionnaire
   `STYLE_WIDTHS` règlent la hiérarchie. `--style-only` réécrit les 100 `.qml`
   en quelques secondes sans retoucher aux courbes, l'intervalle étant relu
   dans chaque GPKG (`read_interval_from_gpkg()`, pas le plus fréquent entre
   deux altitudes consécutives) : on peut donc ajuster le style après coup.

   > **Le modèle générique reste valable** pour les intervalles d'origine
   > (100/200/400/500 m), c'est-à-dire avec `--density 1.0`. Les `.qml` par
   > relief sont le style à utiliser avec la densité par défaut.

Dans QGIS, charger le style du relief avec *Propriétés de la couche → Symbologie
→ Style → Charger le style… → Fichier QML* en sélectionnant
`<Nom>_contours.qml` (le fichier porte le même nom que le GPKG, il est donc
immédiatement identifiable).

### 7.1 Étape 2 bis — adoucissement des courbes (`smooth_contours.py`)

> État : **script écrit et validé**, exécution à la demande, relief par relief.
> Au 21/09/2026, 2 reliefs sur 100 sont lissés (MontTeide, MontBlanc) : c'est
> une étape facultative, à lancer via `./smooth_contours.py`.

**Le problème.** `gdal_contour` travaille par « marching squares » sur une
grille de pixels : les courbes sont des polylignes à **très peu de sommets**.
Sur le MontBlanc, la médiane est de **7 points par courbe**, pour des segments
de 15 à 30 m (un pixel AW3D30). Approximer une courbe de niveau par 7 sommets
donne un polygone anguleux : à l'écran, l'aspect est en facettes. C'est ce que
ce script corrige.

**Contrainte absolue** : les GPKG existants ne sont **jamais** modifiés. La
sortie est écrite à côté, sous le nom `<Nom>_contours_lisse.gpkg` (suffixe
réglable par `--suffix`). Le script refuse même de s'exécuter si le chemin de
sortie se confond avec celui d'entrée.

**Process géométrique**, appliqué courbe par courbe :

1. **Nettoyage des sommets dégénérés** (`--clean-tol-m`, défaut **0,1 m**).
   Mesure faite sur 12 000 courbes du MontBlanc : **12,7 % des segments ont une
   longueur quasi nulle** (330 000 segments sur 333 000 mesurés font moins de
   1 mm, la moitié moins de 5 µm). Ces points sont invisibles mais créent des
   angles de 180° purement numériques, que Chaikin transformerait en « plis »
   au lieu d'arrondir. À 0,1 m, le nettoyage retire **3 % des sommets sans
   perdre une seule courbe** et change le périmètre total de 0,000 %.

2. **Passage dans un repère métrique local.** Toutes les opérations se font en
   **mètres** : à 47° de latitude, 1° de longitude vaut 76 km contre 111 km
   pour 1° de latitude. Lisser en degrés donnerait un arrondi 1,46 fois plus
   fort en latitude qu'en longitude, donc des courbes déformées. Les facteurs
   `m_lon` / `m_lat` sont calculés par `meters_per_degree()` (formules WGS 84,
   `m_lat = 111132,95 − 559,85·cos2φ + 1,175·cos4φ`, `m_lon = 111319,49·cosφ`)
   au centre de chaque relief. Si le SCR n'est pas géographique, les facteurs
   valent 1 : le traitement se fait alors dans l'unité du SCR.

3. **Lissage de Chaikin** (`--iterations`, défaut **1**). Chaque segment
   `[P, Q]` est remplacé par les points `0,75·P + 0,25·Q` et
   `0,25·P + 0,75·Q`. Un passage **double le nombre de points** et coupe les
   angles ; la limite mathématique est une B-spline quadratique (C1, sans angle
   saillant). Deux propriétés recherchées ici :

   * la courbe reste **dans l'enveloppe convexe** de ses points d'origine :
     elle ne peut pas « baver » hors de son couloir, et ne peut pas
     s'auto-croiser ;
   * l'effet est **local** (un segment de part et d'autre du sommet).

   Formes fermées et ouvertes sont traitées différemment :

   * **fermée** (la quasi-totalité) : le successeur du dernier sommet est le
     premier (`np.roll`), et le point de fermeture est **réajouté en sortie**
     pour préserver la convention OGR (anneau fermé). Sans cela, OGR voit une
     ligne ouverte et QGIS ne referme pas le trait au rendu ;
   * **ouverte** : les **deux extrémités sont conservées telles quelles**.
     C'est indispensable pour les courbes qui touchent le bord de la fenêtre
     (vérifié : 18 courbes ouvertes, écart d'extrémité = 0,0000 m).

4. **Simplification Douglas-Peucker** (`--simplify-m`, défaut **0** = inactif).
   Elle allège le fichier mais **recrée localement des angles** : à 1 m de
   tolérance, la part d'angles > 45° remonte de 7 % (Chaikin seul) à 64 %.
   C'est un compromis de poids, pas un réglage recommandé. Elle utilise
   `SimplifyPreserveTopology` (donc sans auto-croisement) et **avale les
   exceptions GEOS** sur les géométries dégénérées.

Options en ligne de commande :

| Option | Défaut | Rôle |
|---|---|---|
| `--single NOM` | — | Traiter un seul relief (fragment de nom, insensible à la casse) |
| `--iterations N` | 1 | Passages de Chaikin |
| `--clean-tol-m X` | 0,1 | Distance mini entre sommets avant lissage |
| `--simplify-m X` | 0 | Tolérance Douglas-Peucker (0 = désactivé) |
| `--suffix S` | `_lisse` | Suffixe du fichier de sortie |
| `--overwrite` | non | Refaire une sortie existante (sinon elle est ignorée : reprise) |
| `--check` | non | Mesurer avant/après sur un échantillon, **sans rien écrire** |
| `--dry-run` | non | Lister les traitements prévus |
| `--no-spatial-index` | non | Ne pas créer l'index RTree en sortie |
| `--no-style` | non | Ne pas copier le `.qml` à côté de la sortie |

**Résultats mesurés.** MontTeide (3 017 courbes), comparaison source → lissé :

| Fichier | Courbes | Sommets | Angle moyen | Angles > 45° | Poids |
|---|---:|---:|---:|---:|---:|
| source | 3 017 | 1 154 966 | 15,0° | 5,7 % | 18,4 Mo |
| lissé ×1 | 3 017 | 2 163 381 | 7,9° | **1,2 %** | 34,0 Mo |

Sur un échantillon plus large (8 000 courbes du MontBlanc, angles mesurés
indépendamment du jeu de données) : de 21,1 % d'angles > 45° à **7,2 %** avec
1 passage, **0,8 %** avec 2 passages. Le gain est donc net dès le premier
passage ; le second double encore le poids.

`smooth_contours_exemple.png` (à la racine du projet) montre le résultat sur
une courbe réelle : en rouge le tracé source (8 sommets, angles jusqu'à 125°),
en vert le tracé adouci (16 sommets). La courbe verte reste **strictement à
l'intérieur** du tracé d'origine — c'est la garantie de l'enveloppe convexe.

**Contrôles qualité passés** sur la sortie :

* schéma, SCR (EPSG:4326), nom de couche (`contour`), champs (`ID`, `ELEV`),
  `fid` et index RTree **identiques** à la source ; correspondance `fid → ELEV`
  vérifiée sur 3 017/3 017 entités, 0 écart ;
* nombre de courbes fermées et ouvertes **inchangé**, aucune auto-intersection
  (`IsSimple()`), aucun anneau réduit à moins de 3 points ;
* fichiers sources intacts (aucune écriture ; horodatage et taille inchangés).

**Cas particuliers.** Les « spikes » plats — des anneaux de largeur
micrométrique pour ~30 m de long — s'effondrent à moins de 3 points au
nettoyage. Ils sont **écrits tels quels** plutôt que supprimés (359 sur 3 017
pour MontTeide, tous à l'altitude 0 m, donc dans la mer : invisibles à
l'échelle utile), et comptés dans le rapport de sortie.

**Coût.** Chaikin multiplie les sommets par 2 par passage, soit **×1,9** en
poids au total (le nettoyage en retire 3 %) ; ×3,5 environ avec 2 passages.
Le traitement est vectorisé NumPy par courbe : 4 s pour MontTeide, environ
30 s pour un relief de 130 000 courbes. `--check` et `--dry-run` n'écrivent
rien ; `--overwrite` étant absent par défaut, le script est **reprenable**.

Le style QGIS est copié sous `<Nom>_contours_lisse.qml` : les attributs étant
strictement identiques, le style du fichier d'origine s'applique sans
modification ni régénération des expressions `ELEV`.

---

## 8. Choix techniques et compromis

### 8.1 `gdal_merge.py` plutôt que VRT

* `gdal_merge.py` produit un GeoTIFF **matérialisé**, autonome, directement
  lisible par QGIS sans ses dépendances. Coût : écrire ~445 Mo par MNT.
* `gdalbuildvrt` est quasi instantané et évite l'intermédiaire, et
  `gdalwarp` peut compresser à la volée. Mais un VRT n'est pas un livrable
  autonome (il référence les tuiles) et n'apporte pas de gain de vitesse sur ce
  cas. Il est utilisé comme **variante** dans `tile_store.py --algo vrt` et
  comme étape du banc de compression.
* Mesure : `gdalbuildvrt` + `gdalwarp` n'est pas plus rapide que
  `gdal_merge.py` ici ; son intérêt est la compression à la volée.

### 8.2 Float32 (actuel) vs Int16

Les tuiles AW3D30 sont déjà de l'`Int16`. Le pipeline fusionne en **Float32**,
ce qui double la taille sans rien apporter : le passage en Int16 est
**strictement sans perte** (écart 0 m vérifié). Le changement n'a pas encore
été appliqué (voir §13, point 1) ; il se ferait par un simple `gdal_translate`
sur les 100 MNT existants, sans re-téléchargement ni re-fusion.

**Ne jamais utiliser de compression avec perte (JPEG) sur l'altitude.**

### 8.3 Statistiques via la sortie JSON de `gdalinfo`

`gdalinfo -stats` affiche `Minimum=…, Maximum=…` sur **une seule ligne**.
L'ancien parsing lisait `split("=")[1]` pour les deux champs et renvoyait donc
le minimum pour le maximum (`min == max`) sur les 33 MNT — par exemple Vinson
annoncé « 209–209 m » pour un raster allant de 209 à 4 880 m. Le bug aurait
aussi produit des intervalles de courbes absurdes dans l'étape 2.

`gdal_utils.get_raster_metadata()` lit la sortie **JSON** de
`gdalinfo -json -stats -norat`, avec deux replis (expression régulière sur la
ligne `Minimum=…, Maximum=…`, puis clés `STATISTICS_*`). Il renvoie `min`,
`max`, `valid_percent` et `nodata`, et exclut les pixels nodata. Le module reste
basé sur les outils GDAL en ligne de commande (pas de binding `osgeo`), comme le
reste du projet.

### 8.4 Cache

Trois niveaux, avec des compromis différents :

| Élément | Conservé ? | Compromis |
|---|---|---|
| ZIP (`aw3d30_data/<tuile>.zip`) | **Toujours** | Source d'origine ; réextraction sans Internet ; ~2,4 Go |
| DSM extrait (`<tuile>/<tuile>_dsm.tif`) | Supprimé après fusion par défaut ; `--keep-tiles` | Accélère les reliefs voisins ; ~0,65 Go ; redondant avec les ZIP |
| Statistiques `.aux.xml` | Recalculables à la demande | Évite de relire le raster. L'histogramme géant d'Everest (5 Mo) a été supprimé ; les statistiques se recréent en 359 o |

Un MNT déjà généré n'est pas refait (`--force` pour outrepasser) : c'est la
reprise.

### 8.5 Liens durs

Deux reliefs de même tuile centrale donnent un MNT identique. Le lien dur
partage l'inode : **même contenu, même chemin, un seul bloc disque**. Risque
nul en pratique : toute réécriture casse le lien au lieu de contaminer le
jumeau. Cinq paires ont été traitées (Everest / Lhotse, Annapurna / Dhaulagiri,
Mont Cook / Mont Tasman, Mont Logan / Mont Saint-Élie, Monte Pissis / Ojos del
Salado), soit **1,87 Go récupérés**.

`dedupe_mnt.py` automatise l'opération **après vérification de la taille et du
MD5** : toute différence est signalée et laissée intacte. `--dry-run` montre
les actions sans rien modifier.

### 8.6 Mode « bibliothèque de tuiles » (`tile_store.py`)

Approche « tuiles d'abord » : stocker chaque tuile JAXA **une seule fois**
(`tuiles/<tuile>.tif`, `Int16` + `DEFLATE` + `TILED` + `PREDICTOR=2` +
`NUM_THREADS=ALL_CPUS`), puis reconstruire n'importe quelle fenêtre 3° × 3° à la
demande (`gdal_merge.py --algo merge`, ou `gdalbuildvrt --algo vrt`).

* Les 100 MNT actuels mobilisent **900 tuiles-slots** pour seulement **621
  tuiles distinctes** (facteur 1,45×), dont 40 non publiées par la JAXA.
* Plafond théorique du magasin, sur les 581 tuiles réellement disponibles :
  ~30 Go en Float32, ~15 Go en Int16, ~2,6 Go en Int16 + DEFLATE (contre 34 Go
  pour `reliefs_mnt/` aujourd'hui, liens durs comptés une fois).
* Validé : MNT reconstruit **identique au MD5 près** (`2c59381d…`) et chemin VRT
  fonctionnel. Test : 8 tuiles stockées = 48 Mo compressés contre 207 Mo bruts.

Le magasin ne remplace rien automatiquement : il est **additif**. Pour basculer
dessus, il faudrait supprimer `reliefs_mnt/` après avoir vérifié l'identité des
MNT reconstruits (le script compare automatiquement l'ancien et le nouveau).

### 8.7 Robustesse réseau

`wget` avec `--timeout=30 --tries=3 --waitretry=2` : une coupure réseau échoue
au lieu de bloquer. Les téléchargements parallèles (`--jobs`) exploitent
l'indépendance des tuiles. Les tuiles non publiées sont mémorisées pour ne plus
générer de requêtes inutiles.

---

## 9. Référence des options en ligne de commande

### 9.1 `download_reliefs.py`

```bash
./download_reliefs.py                       # tous les reliefs de montagnes_50.csv
./download_reliefs.py --single MontBlanc    # un seul relief (nom exact)
./download_reliefs.py --single Everest --force
./download_reliefs.py --jobs 6
./download_reliefs.py --liste reliefs_50.csv
```

| Option | Défaut | Effet |
|---|---|---|
| `--single NOM` | aucun | Ne traiter qu'un relief (nom exact, insensible à la casse) |
| `--force` | désactivé | Refaire les MNT déjà présents dans `reliefs_mnt/` |
| `--keep-tiles` | désactivé | Conserver les DSM extraits en cache après fusion (sinon supprimés ; les ZIP restent) |
| `--jobs N` | `4` | Nombre de téléchargements en parallèle (`1` = série ; doit être ≥ 1) |
| `--liste CHEMIN` | `montagnes_50.csv` (repli `reliefs_50.csv`) | CSV de reliefs à traiter |

### 9.2 `generate_contours.py`

```bash
./generate_contours.py                       # tous les MNT de reliefs_mnt/, densité 1.5 + styles
./generate_contours.py --single Everest
./generate_contours.py --single Everest --interval 250
./generate_contours.py --dry-run             # intervalles choisis, sans génération
./generate_contours.py --density 2.0         # deux fois plus de courbes
./generate_contours.py --density 1.0 --no-style           # intervalles d'origine
./generate_contours.py --style-only          # réécrit les .qml des GPKG existants
./generate_contours.py --style-only --index-every 3       # maîtresse 1 courbe sur 3
./generate_contours.py --output-dir /tmp/test
```

| Option | Défaut | Effet |
|---|---|---|
| `--single NOM` | aucun | Ne traiter qu'un relief (recherche par **sous-chaîne** dans le nom du fichier, insensible à la casse — contrairement à `download_reliefs.py` qui exige le nom exact) |
| `--interval N` | aucun | Forcer l'intervalle **exact** des courbes (désactive l'adaptation automatique **et** la densification) |
| `--density F` | `1.5` | Facteur de densification : intervalle final = référence / F. `1.5` → ~+50 % de courbes, `1.0` → intervalles d'origine, `2.0` → ×2 |
| `--output-dir CHEMIN` | `courbes_niveau/` | Dossier de sortie (utile pour un essai sans écraser les livrables) |
| `--dry-run` | désactivé | Afficher les intervalles, les niveaux attendus et la hiérarchie du style, sans rien générer |
| `--index-every N` | `5` | Une courbe maîtresse (épaissie + étiquetée) toutes les N courbes ; la majeure suit tous les `N × 3` |
| `--no-style` | désactivé | Ne pas écrire les `.qml` par relief |
| `--style-only` | désactivé | Réécrire uniquement les `.qml` à partir des GPKG existants (intervalle relu dans chaque GPKG), sans recalculer les courbes |

### 9.3 `smooth_contours.py`

```bash
./smooth_contours.py                       # tous les reliefs, 1 passage de Chaikin
./smooth_contours.py --single MontBlanc    # un seul relief
./smooth_contours.py --check               # mesure avant/après, sans rien écrire
./smooth_contours.py --dry-run             # liste les sorties prévues
./smooth_contours.py --iterations 2        # arrondi plus prononcé (poids ×3,5)
./smooth_contours.py --simplify-m 0.5      # sortie plus légère (recrée des angles)
./smooth_contours.py --overwrite           # refaire les sorties existantes
./smooth_contours.py --suffix _adouci      # autre nom de sortie
```

| Option | Défaut | Effet |
|---|---|---|
| `--single NOM` | aucun | Ne traiter qu'un relief (recherche par **sous-chaîne**, insensible à la casse) |
| `--iterations N` | `1` | Passages de Chaikin : chaque passage double les sommets et arrondit davantage |
| `--clean-tol-m X` | `0.1` | Distance minimale entre sommets (m) avant lissage ; supprime les points dégénérés |
| `--simplify-m X` | `0` | Tolérance Douglas-Peucker (m), `0` = désactivé. Allège mais recrée des angles |
| `--suffix S` | `_lisse` | Suffixe des fichiers de sortie (`<Nom>_contours<S>.gpkg`) |
| `--input-dir CHEMIN` | `courbes_niveau/` | Dossier des courbes d'entrée |
| `--overwrite` | désactivé | Refaire les sorties déjà présentes (sinon elles sont ignorées : reprise) |
| `--no-spatial-index` | désactivé | Ne pas créer l'index RTree dans la sortie |
| `--no-style` | désactivé | Ne pas copier le `.qml` à côté de la sortie |
| `--check` | désactivé | Mesurer la distribution des angles avant/après sur un échantillon, sans écrire |
| `--dry-run` | désactivé | Afficher les traitements prévus, sans écrire |

Les fichiers d'entrée ne sont **jamais** modifiés : la sortie porte le suffixe
`_lisse` et reste dans le même dossier que la source.

### 9.4 `dedupe_mnt.py`

```bash
./dedupe_mnt.py --dry-run
./dedupe_mnt.py
./dedupe_mnt.py --liste montagnes_50.csv
```

| Option | Défaut | Effet |
|---|---|---|
| `--liste CHEMIN` | `montagnes_50.csv` | CSV des reliefs (regroupement par tuile centrale) |
| `--dry-run` | désactivé | Afficher les actions sans rien modifier |

### 9.5 `tile_store.py`

```bash
./tile_store.py status
./tile_store.py build
./tile_store.py build --tuiles S044E170 S044E171
./tile_store.py mnt MontCook
./tile_store.py mnt MontCook --algo vrt --out /tmp/cook.vrt
```

| Sous-commande | Option | Défaut | Effet |
|---|---|---|---|
| `status` | `--liste CHEMIN` | aucun | État du magasin ; compare aux tuiles requises par le CSV |
| `build` | `--liste CHEMIN` | `montagnes_50.csv` | Tuiles à couvrir |
| `build` | `--tuiles T1 T2 …` | aucun | Tuiles explicites (prioritaire sur `--liste`) |
| `build` | `--jobs N` | `4` | Téléchargements parallèles |
| `build` | `--keep-tiles` | désactivé | Conserver aussi les DSM extraits (déconseillé) |
| `mnt NOM` | `--liste CHEMIN` | `montagnes_50.csv` | CSV de correspondance du relief |
| `mnt NOM` | `--out CHEMIN` | `reliefs_mnt/<Nom>_mnt.tif` | Chemin de sortie |
| `mnt NOM` | `--algo {merge,vrt}` | `merge` | Reconstruction matérialisée ou VRT |

### 9.6 `bench_compression.py`

```bash
./bench_compression.py
./bench_compression.py --tuiles N044E005 N044E006 --keep
```

| Option | Défaut | Effet |
|---|---|---|
| `--tuiles T1 T2 …` | 9 tuiles alpines | Tuiles à fusionner (doivent être en cache) |
| `--keep` | désactivé | Conserver les fichiers produits dans `_bench/` |

---

## 10. Garanties et contrôles qualité

* **ZIP** : contrôle d'intégrité `zipfile.testzip()` ; un ZIP corrompu est
  supprimé et retéléchargé. Un faux ZIP (page HTML ~3 100 octets) est détecté,
  supprimé et mémorisé comme tuile non publiée.
* **Taille minimale** : un DSM ou un MNT de moins de 100 000 octets est refusé.
* **Statistiques fiables** : lecture JSON de `gdalinfo`, nodata exclu,
  `valid_percent` rapporté ; le bug `min == max` est corrigé.
* **Détection de MNT plat** : avertissement si `max − min ≤ 1 m`.
* **Option A** : les zones non couvertes sont en nodata déclaré, l'océan `0 m`
  est préservé, les pixels valides sont inchangés (écart 0 m vérifié).
* **Conversion Float32 → Int16** : sans perte (écart 0 m vérifié) ; compression
  **sans perte uniquement** (DEFLATE/ZSTD), jamais de JPEG sur l'altitude.
* **Déduplication** : taille + MD5 comparés avant tout lien dur ; différences
  signalées et laissées intactes.
* **Mode bibliothèque** : MNT reconstruit comparé au MD5 à l'ancien ; s'il
  diffère, l'ancien est conservé sous `.bak`.

Contrôle manuel recommandé avant/après toute conversion de masse :

```bash
gdalinfo -json -stats -norat reliefs_mnt/<Nom>_mnt.tif | \
  python3 -c "import json,sys; b=json.load(sys.stdin)['bands'][0]; m=b['metadata']['']; \
print('nodata', b.get('noDataValue'), 'min', m.get('STATISTICS_MINIMUM'), \
'max', m.get('STATISTICS_MAXIMUM'), 'valide %', m.get('STATISTICS_VALID_PERCENT'))"
```

---

## 11. Mesures de performance

Mesures reproductibles avec `./bench_compression.py` (9 tuiles réelles des
Alpes, cache disque chaud).

### 11.1 Formats de sortie

| Format | Taille | Temps | Perte |
|---|---:|---:|---|
| Float32 non compressé (**actuel**) | 445 Mo | ~2,6 s | — |
| Int16 non compressé | 223 Mo | ~2,6 s | aucune |
| Int16 + DEFLATE + TILED + PREDICTOR=2 | 77 Mo | ~10,8 s (1 thread) | aucune |
| Int16 + DEFLATE + NUM_THREADS=ALL_CPUS | 77 Mo | ~3,6 s | aucune |
| Int16 + ZSTD + NUM_THREADS=ALL_CPUS | 76 Mo | ~4,4 s | aucune |

**÷5,8 sur la taille, sans aucune perte.** Les tuiles AW3D30 étant déjà de
l'Int16, le Float32 du pipeline n'apporte rien.

### 11.2 Vitesse

* La fusion n'est **pas** le goulot d'étranglement : ~2,5 s pour 9 tuiles. Les
  runs longs viennent des téléchargements et des extractions.
* `NUM_THREADS=ALL_CPUS` fait passer la compression DEFLATE de ~10,8 s à
  ~3,6 s (×3).
* Le seul vrai levier de temps pour les reliefs restants est
  `--jobs` sur les téléchargements.

### 11.3 Emprise et affichage

| Variante | Taille |
|---|---:|
| 3° × 3° compressé (actuel) | 77 Mo |
| 1,5° × 1,5° centré sur le sommet | 20 Mo |
| 3° × 3° + pyramides internes (QGIS) | 105 Mo |

* Le recadrage divise encore par ~4 mais **change le livrable** (emprise non
  standard) : c'est un choix éditorial, pas une perte de qualité.
* Les pyramides coûtent +37 % de taille mais rendent l'affichage QGIS fluide.
  Inutiles si seuls `gdal_contour` et l'export consomment les MNT.

### 11.4 Gains déjà réalisés sur le dossier

| Action | Gain |
|---|---:|
| 5 paires de MNT identiques → liens durs | 1,87 Go |
| Suppression du cache DSM redondant (ZIP conservés) | ~0,65 Go |
| Suppression de l'histogramme `.aux.xml` géant d'Everest | ~5 Mo (recalcul en 359 o à la demande) |

`reliefs_mnt/` : 14,7 → **12 Go**. `aw3d30_data/` : 3,0 → **2,4 Go**.

Après l'extension du 21/09/2026 (100 reliefs), les mêmes dossiers pèsent
`reliefs_mnt/` **34 Go** (17 liens durs supplémentaires créés pendant la passe
de complétion), `courbes_niveau/` **24 Go** et `aw3d30_data/` **6,1 Go**.

---

## 12. Historique des corrections

### v4 — 21/09/2026 : complétion à 100 reliefs

* Les **16 derniers sommets** de `montagnes_50.csv` (Karakoram, Alpes, Caucase,
  Iran, Grèce, Sicile) et les **50 sites de `reliefs_50.csv`** sont produits :
  100 MNT, 100 jeux de courbes, 100 styles.
* 381 tuiles téléchargées, 0 échec réseau ; 40 tuiles non publiées au total
  (34 découvertes pendant la passe).
* 3 reliefs partageant la fenêtre d'un MNT existant partagés par lien dur
  (`Kilimanjaro`, `Peak of Tenerife`, `Columbia River Gorge`).
* Contrôles : 0 MNT plat, 0 valeur non entière sur les 100 rasters, 0 GeoPackage
  illisible, 0 `.qml` invalide.
* Documentation : §2.4 complété (tuiles 2″ vérifiées à 63° N), §2.5 réécrit
  (40 tuiles, 26 reliefs concernés), §2.7/§2.8 ajoutés, §14 mis à jour.

### v1 — avril 2026

* Bug `get_parent_dir()` : bloc `N045E000` forcé pour 30–50° N / 0–90° E, ce
  qui cassait 17 reliefs (Karakoram, Alpes, Caucase, Iran, Grèce, Sicile).
* `import sys` manquant dans `download_reliefs.py` (`NameError` au lieu d'un
  message d'erreur propre).
* Pas de cache DSM.
* Parsing des statistiques défectueux (`min == max` pour les 33 MNT).
* 90 faux ZIP (pages HTML de ~3 Ko) et 85 dossiers de tuiles vides.

### v2 — 11/09/2026

* Correction du bloc JAXA (`floor(lat/5)`, `floor(lon/5)` partout).
* Ajout de `import sys`.
* Cache DSM et reprise des MNT déjà générés.
* Statistiques via `gdal_utils.get_raster_stats()` (sortie JSON de `gdalinfo`).
* Documentation des limites du DSM dans le README et les scripts.
* Ménage des dossiers vides, avertissement « emprise réduite ».
* Nettoyage des faux ZIP et introduction de l'**option A** (nodata `-9999`).
* Archivage du log précédent.

### Point 2 — 11/09/2026

* Régénération avec l'option A des 3 MNT à faux plateau 0 m : Puncak Jaya,
  Mont Cook, Mont Tasman. Tuiles `S006E136` et `S043E169` confirmées non
  publiées (404 déguisé en page HTML de 3 100 octets).
* Contrôle : nodata déclaré = −9999, 12 960 000 pixels basculés (11,11 %),
  0 pixel valide modifié, océan 0 m préservé. Les autres MNT n'ont pas de trou
  intérieur (Mont Teide : emprise réduite sans faux plateau).

### v3 — 11/09/2026

* Téléchargements parallèles `--jobs`.
* `wget --timeout=30 --tries=3 --waitretry=2`.
* Mémoïsation des tuiles non publiées (`missing_tiles.json`).
* Liens durs pour les MNT de même tuile centrale.
* Cache DSM supprimé après fusion par défaut (`--keep-tiles` pour le garder).
* Option `--liste` pour un CSV personnalisé.
* Outils `dedupe_mnt.py`, `tile_store.py`, `bench_compression.py`.

### Améliorations fichier — 11/09/2026

* 5 paires de MNT strictement identiques transformées en liens durs.
* Cache DSM redondant supprimé.
* Histogramme `.aux.xml` géant d'Everest supprimé.

---

## 13. Limites connues et pistes d'amélioration

### Non traité (points 1 et 6)

1. **Int16 + DEFLATE sur les MNT** *(point 1, non traité)*. Passer `merge_tiles`
   en `Int16 + COMPRESS=DEFLATE + TILED=YES + PREDICTOR=2 +
   NUM_THREADS=ALL_CPUS`, puis convertir les 100 MNT existants par
   `gdal_translate`. Gain attendu : **÷5,8 sur la taille, zéro perte**. Le
   dossier `reliefs_mnt/` occupe 34 Go (liens durs comptés une fois) : environ
   **28 Go seraient libérés**. La conversion est sans perte parce que les
   valeurs sont des entiers : **vérifié sur les 100 MNT le 21/09/2026**
   (`gdal_calc.py` comparant chaque pixel à sa partie entière — aucun raster ne
   contient de valeur non entière). Les tuiles sources sont des `Int16`
   recopiées telles quelles par `gdal_merge`, sans rééchantillonnage.
2. **Recadrage optionnel (`--emprise`) et pyramides optionnelles
   (`--pyramides`)** *(point 6, non traité, sur demande)*. Le recadrage change
   le livrable (emprise non standard) ; les pyramides sont inutiles si seuls
   `gdal_contour` et l'export consomment les MNT.

### Autres pistes

* **Style QGIS embarqué dans le GPKG** : les `.qml` par relief sont aujourd'hui
  des fichiers annexes, à charger couche par couche. La convention QGIS
  `layer_styles` (table SQLite dans le GeoPackage, `useAsDefault=1`) les
  appliquerait automatiquement à l'ouverture. Non implémenté faute de pouvoir
  le tester ici (QGIS n'est pas disponible dans l'environnement de dev).
* ~~**`reliefs_50.csv` jamais traité**~~ : **fait le 21/09/2026** (50 / 50, voir
  §14). Les deux listes sont désormais complètes.
* **Adoucissement des 98 reliefs restants** : `smooth_contours.py` n'a tourné
  que sur MontTeide et MontBlanc. Sur 100 reliefs, compter ~50 min et ~40 Go
  supplémentaires (chaque `_lisse.gpkg` pèse ~1,8× le GPKG d'origine, soit
  ~42 Go pour les 98 reliefs restants), à lancer relief par relief ou en une
  passe.
* **Suppression des ZIP** une fois les tuiles extraites (~6,1 Go) — mais garder
  les ZIP est la solution la plus sûre (source d'origine).
* **Extraction parallèle** des ZIP et **fusion mise en cache par massif** : les
  sommets voisins (Himalaya, Karakoram, Alpes) partagent leurs tuiles.
* **Bascule effective sur le mode bibliothèque** : le magasin est additif ; il
  faudrait supprimer `reliefs_mnt/` après contrôle MD5 (gain théorique jusqu'à
  ~34 Go, cf. §8.6).
* **VRT plutôt que `gdal_merge.py`** quand seul `gdal_contour` consomme le MNT.
* **Rangement** du fichier `deepseek_geojson_*.txt` (artefact externe).
* **Documentation** : l'ancien README listait une option `--cleanup-tiles` qui
  n'existe plus en v3 ; la référence à jour est le §9 du présent document.

### Garde-fous à respecter quelle que soit l'option retenue

* Float32 → Int16 est strictement sans perte ; **jamais** de compression avec
  perte (JPEG) sur l'altitude.
* Ne jamais convertir en nodata l'océan `0 m` d'AW3D30 : seul `-9999` est
  nodata.
* Avant/après toute conversion de masse, contrôler `nodata`, `min`, `max` et
  `valid_percent` via `gdal_utils.get_raster_metadata()`.
* Conserver la note sur le pas angulaire variable selon la latitude (§2.4).

---

## 14. État d'avancement

Au 21/09/2026 :

| Étape | Avancement |
|---|---|
| Étape 1 — MNT | **100 / 100** : 50 / 50 sommets de `montagnes_50.csv` **et** 50 / 50 sites de `reliefs_50.csv` |
| Étape 2 — courbes de niveau | **100 / 100** (densité ×1,5 : pas de 15 à 350 m, ~+46 % de niveaux, 7 238 755 entités) + un `.qml` de style par relief |
| Étape 2 bis — courbes adoucies | Script écrit et validé ; **2 / 100** reliefs lissés (MontTeide, MontBlanc), le reste à la demande via `./smooth_contours.py` |

**Complétion du 21/09/2026.** 66 reliefs ont été ajoutés en une passe : les 16
sommets de `montagnes_50.csv` qui manquaient (K2, Nanga Parbat, Gasherbrum I et
II, Broad Peak, Cervin, Jungfrau, Eiger, Monte Rosa, Grand Paradis, Elbrouz,
Kazbek, Ararat, Damavand, Olympe, Etna) et les 50 sites de `reliefs_50.csv`,
jusque-là jamais traités. Durée totale : **~50 min** (téléchargement de 381
tuiles en ~35 min à 12–17 tuiles/min avec `--jobs 6`, puis 54 fusions et
66 générations de courbes).

| Résultat | Valeur |
|---|---|
| Tuiles téléchargées | 381 (0 échec réseau) |
| Tuiles non publiées découvertes | 34 nouvelles, **40** au total |
| MNT produits | 54 fusions + 17 liens durs + 29 déjà présents = 100 |
| MNT plats | 0 |
| MNT à valeurs non entières | 0 (vérifié sur les 100) |
| GeoPackage illisibles ou vides | 0 |
| Styles `.qml` invalides | 0 |
| Occupation | `reliefs_mnt/` 34 Go, `courbes_niveau/` 24 Go, `aw3d30_data/` 6,1 Go |

Le pilote de cette passe est conservé dans `.run/pipeline.sh` (liste combinée
des 100 cibles dans `.run/toutes_cibles.csv`) : il est **idempotent**, relançable
tel quel, chaque étape ignorant ce qui existe déjà.

---

## 15. Glossaire

| Terme | Définition |
|---|---|
| **MNT** | Modèle numérique de terrain : grille d'altitudes régulière. |
| **DSM** | *Digital Surface Model* : modèle numérique de **surface**, altitude du premier obstacle (neige, bâti, canopée). AW3D30 est un DSM. |
| **DTM** | *Digital Terrain Model* : modèle numérique de **terrain**, sol nu. À ne pas confondre avec le DSM. |
| **nodata** | Valeur sentinelle marquant un pixel sans donnée. Ici `-9999`, exclue des statistiques et des courbes. À ne pas confondre avec l'océan (`0 m`). |
| **Tuile** | Dalle carrée de 1° × 1° de AW3D30 (3600 × 3600 px, `Int16`). |
| **Bloc 5°** | Dossier JAXA regroupant les tuiles de 1° × 1° par pas de 5° (ex. `N045E005`). |
| **GeoTIFF** | Format raster TIFF géoréférencé. Format des MNT du projet. |
| **GPKG** | *GeoPackage* : conteneur SQLite pour données vectorielles et raster. Format des courbes de niveau. |
| **VRT** | *GDAL Virtual Format* : fichier XML décrivant un raster virtuel assemblé à la volée, sans matérialiser les données. |
| **COG** | *Cloud Optimized GeoTIFF* : GeoTIFF tuilé et pyramidal optimisé pour la lecture en flux (HTTP Range). Non utilisé à ce jour. |
| **Lien dur** | Deux noms de fichiers pointant vers le même inode : même contenu, un seul bloc disque. Réécrire l'un casse le lien. |
| **RMSE** | *Root Mean Square Error* : erreur quadratique moyenne, ici ~5 m en vertical pour AW3D30. |
| **EPSG:4326** | Système de coordonnées WGS 84 (latitude/longitude en degrés). |
| **DEFLATE / PREDICTOR=2** | Compression sans perte de GeoTIFF, avec prédiction horizontale adaptée aux données d'élévation. |
| **JAXA / ALOS** | Agence spatiale japonaise / satellite *Advanced Land Observing Satellite*, source de AW3D30. |
| **marching squares** | Algorithme extrayant une isoligne d'une grille de valeurs. Produit des polylignes à peu de sommets (ici ~7 en médiane), d'où l'aspect anguleux corrigé par `smooth_contours.py`. |
| **Chaikin (courbette)** | Algorithme de lissage qui coupe chaque coin : un segment devient deux points aux 1/4 et 3/4. Un passage double les sommets ; la limite est une B-spline quadratique. Utilisé par `smooth_contours.py`. |
| **Douglas-Peucker** | Algorithme de simplification qui retire les sommets quasi alignés sous une tolérance donnée. Utilisé (désactivé par défaut) par `smooth_contours.py`. |
| **QML** | Fichier de style QGIS (symbologie, étiquettes). |
| **`.aux.xml`** | Fichier annexe GDAL (PAM) mettant en cache les statistiques d'un raster. |
