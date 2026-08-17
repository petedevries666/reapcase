# Architecture proposée

## Couches

1. **`stadium.py` — entrée/sortie lossless.** Le document JSON complet et le
   texte source sont conservés. Les champs connus sont éditables, mais aucune
   clé inconnue n'est filtrée. Le payload après `|` reste opaque.
2. **`timeline.py` — représentation intermédiaire.** Les adaptateurs échangent
   des positions musicales et des événements typés. `source` peut référencer
   l'objet Stadium original afin de réutiliser son payload lors de l'export.
3. **Adaptateur REAPER (à venir).** Il écrira/lira le projet REAPER, y compris
   tempo, signatures et markers, sans introduire de logique Stadium.
4. **Mappings (à venir).** Un chargeur validé de `config/aliases.json` traduira
   les noms humains vers des événements. Le fichier est versionné et ne remplace
   jamais le payload source d'un événement non modifié.

## Contrat de round-trip

- Sans édition, `from_json_text(source).to_json_text()` retourne exactement
  `source`, espaces et fin de ligne compris.
- Un flag est découpé une seule fois au premier `|`. Son payload est une chaîne
  opaque, même si son type ou ses paramètres sont inconnus.
- Après déplacement, seule la position rendue change ; le payload est réutilisé.
- Les clés JSON inconnues restent dans le document exporté.
- Les flags Stadium `START` et `TIME` restent chacun un unique événement de
  timeline source contenant à la fois le tempo et la signature rythmique. Ils ne
  sont pas scindés : l'ordre source et la reconstruction lossless un-pour-un sont
  ainsi préservés.
- Les positions observées sont indexées à partir de 1 : une frontière exacte de
  beat utilise `.001`. Avec un PPQN de 240, les ticks valides vont de 1 à 240 ;
  `.000` n'est pas accepté sans fixture Stadium qui en démontre la validité.

Cette stratégie distingue la **fidélité syntaxique** (no-op exact) de la
**fidélité sémantique** après édition (valeurs inconnues intactes, sérialisation
JSON normalisée).

## Trois domaines de contrôle indépendants

Les données d'un **Stadium Song** (`START`, `TIME`, `MARKER`, `PRESETSNAP`,
`LOOPER`, `CYCLE`, `MIDI`…) appartiennent au document Song et à son contrat de
round-trip lossless. Le MIDI contenu dans un flag reste donc interprété comme
syntaxe Song générique ; le parseur Stadium ne lui attribue aucune commande de
rig.

L'**automatisation de rig externe** est une couche de configuration séparée.
Elle contient deux systèmes existants : `second_helix` pour le second Helix et
`video` pour la vidéo.

Le **contrôle distant du Stadium à l'exécution** constitue un troisième système,
`stadium_transport`. Il décrit les commandes externes de transport et de
navigation dans les songs, markers et playlists. Son canal est volontairement
optionnel dans la configuration : la source ne prescrit pas de canal et un canal
pourra être ajouté ultérieurement sans modifier le schéma des commandes. Cette
infrastructure est facultative et n'est ni consultée ni nécessaire pour le
round-trip lossless du JSON Song.

Le décodeur/encodeur MIDI de rig est seul responsable de ces trois systèmes de
contrôle. En particulier, les CC de `stadium_transport` ne sont pas des
sémantiques de `StadiumFlag.semantic_data()`.

## Étapes suivantes

1. Collecter des fixtures anonymisées issues de Stadium.
2. Définir précisément la conversion bar/beat/tick avec changements de mesure.
3. Ajouter le mapper Stadium ↔ timeline, puis tester déplacements et PPQN.
4. Implémenter un adaptateur REAPER minimal et ses golden files.
5. Valider et documenter le schéma versionné des alias.
