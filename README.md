# AW3D30 — Reliefs du monde en 3D et courbes de niveau

## À quoi sert ce projet ?

Ce projet récupère automatiquement les **données d'altitude du monde entier**
fournies par l'agence spatiale japonaise (JAXA, programme ALOS AW3D30) autour de
**100 sites remarquables** : les 50 plus grands sommets (Everest, Mont Blanc,
Kilimandjaro, Aconcagua, Denali, Mont Fuji…) et 50 curiosités géologiques
(Grand Canyon, falaises d'Étretat, Vésuve, Ayers Rock, Tsingy de Bemaraha…).

Il assemble ces données pour fabriquer, pour chaque site, une **carte
d'altitude** (un « modèle numérique de terrain ») couvrant une fenêtre de
3° sur 3° — soit environ 330 km de côté. Il peut ensuite en tirer des **courbes
de niveau** prêtes à être ouvertes dans QGIS, le logiciel de cartographie
libre.

En résumé : **des données brutes téléchargées → des cartes de relief
utilisables**.

## Ce que le projet produit

* **Un fichier d'altitude par relief** : `<Nom>_mnt.tif` (format GeoTIFF),
  lisible par QGIS, GDAL et la plupart des logiciels cartographiques.
  Chaque pixel indique une altitude en mètres.
* **Des courbes de niveau** : `<Nom>_contours.gpkg` (format GeoPackage), à
  afficher par-dessus le relief.
* **Des courbes adoucies** (facultatif) : `<Nom>_contours_lisse.gpkg`, la même
  chose avec les angles arrondis. Les courbes brutes sortent d'une grille de
  pixels : elles sont anguleuses et donnent un aspect en facettes. Le script
  `smooth_contours.py` en produit une version lissée, **sans jamais toucher
  aux fichiers d'origine**.
* **Un style QGIS prêt pour chaque relief** : `<Nom>_contours.qml`, posé à côté
  du GeoPackage. Il dessine les courbes avec trois épaisseurs et étiquette les
  courbes maîtresses. `QGIS_style/style_contours.qml` reste le style générique
  (utile si vous régénérez les courbes aux pas d'origine).

## Comment l'utiliser simplement

Le projet a deux étapes : d'abord télécharger et assembler les reliefs, ensuite
tracer les courbes de niveau.

```bash
# Étape 1 — récupérer et assembler tous les reliefs
./download_reliefs.py

# Étape 1 — la liste des sites remarquables (canyons, falaises, volcans…)
./download_reliefs.py --liste reliefs_50.csv

# Étape 1 — un seul relief (nom exact)
./download_reliefs.py --single MontBlanc

# Étape 2 — générer les courbes de niveau de tous les reliefs
./generate_contours.py

# Étape 2 — un seul relief
./generate_contours.py --single MontBlanc

# Étape 3 (facultative) — adoucir les courbes déjà générées
./smooth_contours.py

# Étape 3 — un seul relief, ou vérifier l'effet sans rien écrire
./smooth_contours.py --single MontBlanc
./smooth_contours.py --check
```

Le travail déjà fait n'est jamais refait : si un relief existe déjà, le
programme l'ignore et passe au suivant. On peut donc interrompre puis relancer
sans rien perdre.

Pour utiliser les résultats dans QGIS : ouvrez le fichier
`reliefs_mnt/<Nom>_mnt.tif`, puis les courbes de niveau
`courbes_niveau/<Nom>/<Nom>_contours.gpkg`, et chargez le style
`courbes_niveau/<Nom>/<Nom>_contours.qml` (Propriétés de la couche →
Symbologie → Style → Charger le style… → Fichier QML). Le style est propre à
chaque relief car le pas des courbes varie de 15 à 350 m selon le sommet.

Ce qu'il faut avoir installé : **Python 3**, les **outils GDAL** et **wget**
(en ligne de commande), et **QGIS 3.22 ou plus récent** pour l'affichage.

## Bon à savoir

* **Les données montrent la surface, pas le sol.** La JAXA mesure le premier
  obstacle rencontré : neige, glace, forêts, bâtiments. Les sommets enneigés
  sont donc souvent mesurés un peu trop bas (l'Everest ressort à 8 767 m au
  lieu de 8 848,86 m). Ces cartes sont excellentes pour visualiser un relief
  ou tracer des courbes de niveau, mais ce ne sont pas des altitudes
  officielles.
* **La mer est à 0 m.** Les courbes de niveau qui longent les côtes sont
  normales, ce n'est pas une erreur.
* **Certaines zones ne sont pas couvertes.** 40 tuiles ne sont pas publiées par
  la JAXA (presque toutes en pleine mer ou aux hautes latitudes). Sur 26 reliefs
  sur 100, cela se traduit soit par un trou « sans donnée » (aucune courbe de
  niveau n'y est tracée), soit par une carte un peu moins large que 3° sur 3° :
  c'est le cas de Puncak Jaya, du Mont Cook, du Mont Tasman, du Vésuve, du
  Krakatoa, des Mauna Kea / Mauna Loa / Kīlauea, de la Montagne Pelée, etc.
* **La finesse est d'environ 30 m par pixel.** On voit bien les grandes formes
  du terrain, pas les petits détails. Aux hautes latitudes (Denali, Hornelen,
  Eyjafjallajökull, Vinson…), les tuiles JAXA sont échantillonnées plus large
  **en longitude** tout en restant à ~30 m au sol.
* Chaque carte couvre en principe 3° sur 3° (parfois un peu moins lorsque des
  données manquent) : elle peut donc contenir plusieurs sommets voisins, et son
  point culminant n'est pas forcément celui du sommet visé.

## Où en est le projet ?

* **Les 100 reliefs sont préparés** (données téléchargées et assemblées) : les
  50 sommets de `montagnes_50.csv` **et** les 50 sites remarquables de
  `reliefs_50.csv` (canyons, falaises, volcans…).
* Les courbes de niveau sont générées pour ces **100 reliefs**, avec un pas
  resserré (15 à 350 m selon le relief) pour un rendu plus détaillé, chacune
  accompagnée de son style QGIS (`<Nom>_contours.qml`). Le pas est réglable :
  `./generate_contours.py --density 1.5` (défaut, ~+50 % de courbes) ou
  `--density 1.0` pour revenir aux pas d'origine.
* Les courbes peuvent être **adoucies** (angles arrondis) sans toucher aux
  fichiers d'origine : `./smooth_contours.py` écrit `<Nom>_contours_lisse.gpkg`
  à côté. Compter ~30 s par relief et un fichier ~1,8 fois plus lourd. Sur le
  MontTeide, la part d'angles cassants (> 45°) passe de 5,7 % à 1,2 %. C'est une
  étape **facultative, à la demande**, lancée sur 2 des 100 reliefs à ce jour.

## Envie de détails ?

Ce fichier est volontairement simple. Toute la documentation technique
(architecture, commandes complètes, choix de formats, performances, limites
précises et pistes d'amélioration) se trouve dans **[`TECHNIQUE.md`](TECHNIQUE.md)**.
