# Arbres : pourquoi parfois plus verts en hiver qu'en été ? — Angers

Comparaison **A = Hiver 2026** (2 dates) et **B = Été 2026** (7 dates). NDVI moyen des pixels vus dans les deux saisons ; « plus vert en A » = part des pixels dont le NDVI baisse de A à B.

Catégorie « Arbres et bois » entière : NDVI 0,51 → 0,58 (écart B − A +0,07), **33 % des pixels plus verts en A**.

## 1. Par type d'objet OpenStreetMap

Objets : 1138 bois/forêts/fourrés, 907 alignements, 16115 arbres isolés.

| Type | Surface | NDVI A | NDVI B | B − A | Pixels plus verts en A |
|---|---:|---:|---:|---:|---:|
| Cœur des bois (à plus de 20 m de la lisière) | 74 ha | 0,61 | 0,80 | +0,19 | 12 % |
| Lisière des bois (moins de 20 m) | 160 ha | 0,60 | 0,73 | +0,13 | 21 % |
| Alignements d'arbres (natural=tree_row) | 121 ha | 0,46 | 0,53 | +0,07 | 34 % |
| Arbres isolés (natural=tree) | 193 ha | 0,42 | 0,40 | −0,02 | 51 % |

## 2. Selon la part du pixel couverte par la couronne

Part calculée sur une grille de 2 m (couronne de 4 m de rayon par arbre isolé, 8 m de large par alignement).

| Couronne dans le pixel | Pixels | NDVI A | NDVI B | B − A | Pixels plus verts en A |
|---|---:|---:|---:|---:|---:|
| 0–25 % | 16109 | 0,42 | 0,41 | −0,01 | 50 % |
| 25–50 % | 7893 | 0,45 | 0,46 | +0,01 | 44 % |
| 50–75 % | 7699 | 0,50 | 0,56 | +0,06 | 36 % |
| 75–100 % | 6322 | 0,55 | 0,65 | +0,10 | 27 % |
| 100 % | 16872 | 0,61 | 0,78 | +0,18 | 13 % |

## 3. Modèle de mélange

NDVI(pixel) = f · NDVI(couronne) + (1 − f) · NDVI(alentours), ajusté sur tous les pixels « arbres » :

| | Couronne pure (f = 1) | Alentours (f = 0) | R² |
|---|---:|---:|---:|
| A — Hiver 2026 | 0,59 | 0,39 | 0,18 |
| B — Été 2026 | 0,75 | 0,35 | 0,45 |
| B − A | +0,16 | −0,04 | |

Un pixel devient plus vert de A à B quand la couronne en couvre plus de **22 %**.

## 4. Selon le feuillage (quand il est renseigné dans OSM)

| Feuillage | Surface | NDVI A | NDVI B | B − A | Pixels plus verts en A |
|---|---:|---:|---:|---:|---:|
| Feuillus (leaf_type=broadleaved) | 127,6 ha | 0,45 | 0,53 | +0,08 | 31 % |
| Conifères (leaf_type=needleleaved) | 2,6 ha | 0,62 | 0,50 | −0,12 | 83 % |
| Caducs (leaf_cycle=deciduous) | 25,6 ha | 0,54 | 0,67 | +0,13 | 25 % |
| Persistants (leaf_cycle=evergreen) | 0,9 ha | 0,61 | 0,52 | −0,09 | 84 % |

Pour comparaison, les pelouses et les parcs :

| Catégorie | NDVI A | NDVI B | B − A |
|---|---:|---:|---:|
| Autres espaces verts (pelouses…) | 0,59 | 0,40 | −0,18 |
| Parcs et jardins | 0,61 | 0,51 | −0,10 |

## 5. Saison par saison (NDVI moyen, médiane des dates)

| Saison | Dates | Cœur des bois | Lisière des bois | Alignements d'arbres | Arbres isolés |
|---|---:|---:|---:|---:|---:|
| Hiver 2025 | 2 | 0,61 | 0,58 | 0,45 | 0,45 |
| Printemps 2025 | 8 | 0,80 | 0,77 | 0,60 | 0,49 |
| Été 2025 | 9 | 0,84 | 0,77 | 0,57 | 0,42 |
| Automne 2025 | 7 | 0,78 | 0,73 | 0,57 | 0,47 |
| Hiver 2026 | 2 | 0,61 | 0,60 | 0,47 | 0,43 |
| Printemps 2026 | 8 | 0,80 | 0,77 | 0,59 | 0,49 |
| Été 2026 | 7 | 0,81 | 0,74 | 0,54 | 0,41 |
| Automne 2026 | 7 | 0,75 | 0,71 | 0,54 | 0,41 |

## 6. Météo (Open-Meteo, réanalyse ERA5)

Bilan hydrique = pluie − évapotranspiration de référence (ET0). Négatif : le sol s'assèche.

| Saison | Pluie | ET0 | Bilan | T max moyenne |
|---|---:|---:|---:|---:|
| Hiver 2025 | 249 mm | 65 mm | +184 mm | 9,1 °C |
| Printemps 2025 | 128 mm | 268 mm | −139 mm | 17,5 °C |
| Été 2025 | 120 mm | 451 mm | −331 mm | 26,8 °C |
| Automne 2025 | 166 mm | 165 mm | +1 mm | 17,1 °C |
| Hiver 2026 | 271 mm | 70 mm | +201 mm | 10,5 °C |
| Printemps 2026 | 136 mm | 301 mm | −165 mm | 19,1 °C |
| Été 2026 | 115 mm | 524 mm | −408 mm | 29,6 °C |
| Automne 2026 | 103 mm | 172 mm | −69 mm | 20,2 °C |

## Verdict

- Le cœur des bois (canopée pure) change de +0,19 entre A et B, les arbres isolés de −0,02 : le signal « arbres » dépend surtout de ce qui entoure les arbres.
- Plus la couronne couvre le pixel, plus il verdit de A à B (−0,01 → +0,18) : relation régulière, c'est la signature d'un mélange de pixels.
- Modèle de mélange : couronne pure +0,16, alentours −0,04 ; il faut plus de 22 % de couronne pour qu'un pixel soit plus vert en B.
- Conifères −0,12 contre feuillus +0,08 (attention : seulement 2,6 ha de conifères renseignés).
- Été 2026 : bilan hydrique −408 mm — une sécheresse jaunit les pelouses autour des arbres et stresse les arbres eux-mêmes.
