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

Cette stratégie distingue la **fidélité syntaxique** (no-op exact) de la
**fidélité sémantique** après édition (valeurs inconnues intactes, sérialisation
JSON normalisée).

## Étapes suivantes

1. Collecter des fixtures anonymisées issues de Stadium.
2. Définir précisément la conversion bar/beat/tick avec changements de mesure.
3. Ajouter le mapper Stadium ↔ timeline, puis tester déplacements et PPQN.
4. Implémenter un adaptateur REAPER minimal et ses golden files.
5. Valider et documenter le schéma versionné des alias.
