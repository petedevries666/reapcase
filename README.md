# Stadium Reaper Bridge

Socle minimal pour une conversion **sans perte** des Songs Helix Stadium vers
REAPER, puis de REAPER vers Stadium.

Le dépôt ne contient volontairement pas encore le convertisseur complet. Il
fixe les frontières du futur système :

```text
Stadium JSON -> modèle Stadium lossless -> timeline neutre -> adaptateur REAPER
             <- modèle Stadium lossless <- timeline neutre <- adaptateur REAPER
```

## Principes

- le JSON Stadium est la source de vérité ; les champs inconnus sont conservés ;
- un flag non modifié est réémis caractère pour caractère ;
- la position musicale est structurée sans interpréter le payload du flag ;
- la timeline neutre sépare le domaine Stadium du format de projet REAPER ;
- les alias humains vivent dans `config/aliases.json`, hors du code ;
- chaque ajout de parser doit être couvert par un test aller-retour.

## Démarrage

Python 3.11+ suffit, sans dépendance d'exécution :

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Exemple d'utilisation du modèle lossless :

```python
from stadium_reaper_bridge.stadium import StadiumSong

song = StadiumSong.from_json_text(source)
assert song.to_json_text() == source
```

La feuille de route et les décisions d'architecture sont détaillées dans
[`docs/architecture.md`](docs/architecture.md).
