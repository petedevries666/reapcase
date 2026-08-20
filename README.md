# Reapcase

**A lossless, semantic show editor for Helix Stadium songs, built around a DAW-style timeline.**

Reapcase transforme les données d'une Song Helix Stadium en un espace de travail lisible, musical et éditable sans sacrifier leur structure native. L'objectif n'est pas de fabriquer un simple éditeur JSON : Reapcase veut permettre de **construire, inspecter, programmer et maintenir un show complet avec le confort d'un DAW**, tout en conservant Stadium comme format de vérité.

Le principe fondamental est simple : **comprendre ce que l'on peut comprendre, préserver exactement ce que l'on ne doit pas réinventer.**

```text
                    REAPCASE

 Stadium JSON ──> modèle lossless ──> timeline sémantique
      ▲                   │                    │
      │                   │                    ├─ STRUCTURE
      │                   │                    ├─ STADIUM
      │                   │                    ├─ SECOND HELIX
      │                   │                    ├─ VIDEO
      │                   │                    ├─ LIGHTS
      │                   │                    ├─ MIDI / OTHER
      │                   │                    ├─ SEQCLICK / instructions
      │                   │                    └─ AUDIO / waveforms
      │                   │
      └──────── sérialisation sûre <── édition / undo
```

## Pourquoi Reapcase existe

Un show Stadium n'est pas seulement une collection de flags. Il contient une structure musicale, des changements de presets et snapshots, des commandes, des cycles, des événements externes et des pistes audio qui doivent rester synchronisés.

Reapcase expose ces informations comme des **objets musicaux manipulables dans une timeline**, plutôt que comme des chaînes opaques. Un événement STADIUM reste un événement Stadium. Un snapshot reste un snapshot. Un marker, un cycle ou une commande Second Helix garde sa signification.

Cette couche sémantique permet d'offrir une ergonomie inspirée de REAPER sans transformer le format Stadium en format propriétaire Reapcase.

## Les règles d'or

- **Stadium reste la source de vérité.**
- Les champs inconnus sont conservés plutôt que devinés.
- Les événements non modifiés doivent pouvoir traverser Reapcase sans perte.
- Les positions sont manipulées en unités musicales canoniques, jamais déduites des pixels.
- Les préférences d'interface restent hors du JSON Stadium.
- Les opérations graphiques réutilisent le même modèle canonique que l'Event List et les éditeurs sémantiques.
- Les opérations destructives importantes sont intégrées à Undo.
- Une sauvegarde ne doit jamais devenir un pari : Reapcase crée des copies de sécurité avant remplacement.
- Toute nouvelle interprétation du format doit être accompagnée de tests de round-trip/régression.

---

# Fonctionnalités actuelles

## Timeline DAW

La vue principale présente la Song sur une timeline musicale avec ruler, grille, snapping et playhead.

Les familles sont séparées en lanes spécialisées :

```text
STRUCTURE
  MARKERS
  PAUSES
  CYCLES

STADIUM
  COMMANDS
  LOOPER

SECOND HELIX
  COMMANDS
  LOOPER

VIDEO
LIGHTS
MIDI / OTHER
SEQCLICK
SEQ INSTRUCTIONS
AUDIO
```

Les lanes composites restent des unités cohérentes : déplacer ou masquer STADIUM ne désolidarise pas ses sous-lanes.

La géométrie visible, le dessin, le hit-testing, la sélection et les menus contextuels utilisent le même layout de lanes.

## Édition sémantique

Reapcase reconnaît les principales familles d'événements et fournit une édition adaptée à leur nature plutôt qu'un simple champ de texte brut.

L'éditeur prend notamment en charge les événements structurels, les presets/snapshots et commandes Stadium, les événements Second Helix, les loopers, VIDEO, LIGHTS et les autres familles déjà décodées par le modèle.

Un double-clic sur un événement ouvre son édition sémantique. Les dialogs sont utilisables au clavier avec validation par **Entrée** et annulation par **Échap** lorsque le contexte le permet.

Un **Inspector** latéral peut également projeter les informations sémantiques de la sélection sans obliger à ouvrir systématiquement une boîte de dialogue.

## Sélection et manipulation

Reapcase possède maintenant une vraie sélection de travail façon DAW :

- clic pour sélectionner un événement ;
- Ctrl+clic pour basculer son état dans la sélection ;
- Shift pour étendre la sélection ;
- rectangle de sélection depuis une zone vide ;
- sélection multi-lanes ;
- déplacement groupé en conservant les écarts musicaux ;
- Undo des opérations d'édition.

Les événements restent attachés à leur famille sémantique pendant les opérations de groupe.

## Copier, coller et dupliquer

La playhead sert de curseur d'insertion.

**Ctrl+C / Ctrl+V** copie une sélection vers la position de la playhead en conservant lanes et espacements relatifs.

**Ctrl+D** fournit une duplication rapide de type DAW. Les duplications répétées conservent leur décalage musical, ce qui permet de reproduire rapidement une programmation de section.

**Alt+drag** permet de copier une sélection en la déplaçant, avec preview et Snap, sans supprimer les originaux.

Ces comportements reposent sur une primitive de duplication commune afin d'éviter des variantes incompatibles entre Paste, Duplicate et drag-copy.

## Snap et playhead

Le Snap de la timeline est partagé avec le positionnement de la playhead et les opérations d'édition concernées.

Le curseur est exprimé en position musicale et la timeline s'appuie sur le `TimingMap` plutôt que sur une largeur de mesure supposée constante.

Pendant la lecture, la vue peut suivre automatiquement la playhead lorsqu'elle approche du bord du viewport. Un déplacement manuel de la vue suspend temporairement ce suivi pour éviter de lutter contre l'utilisateur.

## Zoom et navigation

Reapcase propose plusieurs niveaux de navigation :

- zoom horizontal ;
- zoom centré sur le contexte musical ;
- **Zoom Entire Song** ;
- **Zoom to Selection** ;
- navigation événement précédent/suivant ;
- navigation marker précédent/suivant ;
- saut vers START / END ;
- Marker / Region Manager pour atteindre rapidement les points structurels.

Les sauts déplacent le même playhead canonique et repositionnent la timeline avec du contexte visuel autour de la destination.

Les raccourcis DAW sont volontairement limités au contexte Timeline : ils ne volent pas Tab, Home, End ou Ctrl+D aux champs texte et aux dialogs.

## Focus Lane et Lane Manager

Le **Lane Manager** permet d'afficher ou masquer les familles nécessaires au travail courant.

Masquer une lane :

- ne supprime aucun événement ;
- ne modifie pas la Song ;
- ne modifie pas la sérialisation Stadium ;
- retire réellement la lane du layout afin de ne pas laisser de trou vertical.

L'ordre des lanes peut être réorganisé de manière déterministe. Cet ordre est une préférence d'interface, pas une propriété de la Song Stadium.

Le mode **Focus Lane** permet d'isoler temporairement une famille de travail, avec STRUCTURE lorsque nécessaire, puis de restaurer exactement la visibilité précédente.

## Event List

La Song peut être consultée sous forme de liste d'événements chronologique, complémentaire à la Timeline.

L'Event List dérive du **même EditorModel**. Elle ne constitue pas une seconde base de données.

Elle expose des informations telles que :

```text
Position | Lane | Type | Nom / Action | Détails
```

La sélection est synchronisée entre Timeline et Event List. Le tri de la table reste purement visuel et ne réordonne jamais les données Stadium.

## Marker / Region Manager

Le Marker / Region Manager fournit une navigation structurelle dédiée : START, markers, pauses, cycles et END selon les données disponibles.

Il permet de sauter rapidement dans le morceau sans parcourir manuellement plusieurs minutes de timeline.

Cette projection structurelle est également réutilisée par les commandes de navigation clavier afin de garder une seule interprétation de la structure du morceau.

## Audio multitrack

Reapcase affiche jusqu'à huit pistes audio Stadium sous les événements.

Les pistes peuvent être :

- résolues depuis leur emplacement connu ;
- relocalisées avec **Locate Audio Folder** ;
- rafraîchies après modification externe ;
- réordonnées depuis leurs headers ;
- détachées sans supprimer le WAV ;
- complétées avec **Add Audio Track...** via copie gérée.

Les modifications structurelles audio sont undoables. Annuler l'ajout d'une piste détache la piste du modèle mais laisse volontairement le WAV copié sur disque pour éviter une suppression dangereuse.

## Waveforms

Les WAV résolus affichent leur waveform. Le calcul des enveloppes est effectué en arrière-plan et mis en cache pour la session afin de garder l'éditeur utilisable.

Les fichiers `.peak` Stadium ne sont pas nécessaires au rendu interne de la waveform Reapcase.

Un WAV manquant reste représenté explicitement comme fichier non résolu plutôt que d'être remplacé silencieusement par un autre fichier ambigu.

## Recherche audio

**Locate Audio Folder** recherche les WAV manquants en privilégiant leur suffixe relatif puis leur nom de fichier lorsqu'une correspondance unique existe.

Reapcase évite volontairement de choisir arbitrairement entre plusieurs candidats portant le même nom.

Un `manual_audio_root` peut être utilisé pour résoudre les pistes depuis un emplacement choisi.

## Transport audio

La barre de transport pilote un flux de lecture commun :

```text
|<<   Play / Pause   Stop
```

L'interface affiche le temps et la position musicale de la lecture.

Les contrôles **M** et **S** des pistes sont du monitoring local Reapcase et ne modifient jamais le JSON Stadium.

Si `sounddevice`, PortAudio ou le périphérique audio n'est pas disponible, l'éditeur reste utilisable et expose le diagnostic au lieu de rendre l'ensemble de l'application inutilisable.

## Ouverture responsive

L'ouverture d'une grosse Song est découpée en phases et exécutée hors du thread Tk lorsque le travail est coûteux.

Une fenêtre de progression indique l'état du chargement pendant que l'interface reste responsive.

Le chargement suit une logique transactionnelle :

```text
charger candidat
→ résoudre / préparer
→ valider
→ seulement ensuite remplacer la Song courante
```

Une erreur d'ouverture ne doit donc pas sacrifier une Song valide déjà chargée.

La résolution via un dossier audio manuel est également effectuée dans le worker et non pendant le commit UI.

## Sauvegarde sûre

**Save** met à jour la Song courante.

**Save As** permet de choisir une autre destination.

Avant remplacement d'un fichier existant, Reapcase crée une copie de sécurité dans :

```text
.reapcase-backups
```

La Song et son sidecar sont protégés lorsque nécessaire.

Le but est que l'expérimentation dans l'éditeur reste réversible, y compris lorsque l'on travaille sur des données Stadium réelles.

## Show / Setlist

L'éditeur comprend également une couche Show / Setlist permettant de travailler avec plusieurs Songs et leurs chemins associés sans confondre cette organisation avec le contenu lossless de chaque Song.

---

# Architecture

Le projet maintient volontairement une séparation nette entre format natif, représentation éditable et interface :

```text
Stadium JSON
    │
    ▼
StadiumSong / modèle lossless
    │
    ▼
EditorModel + TimelineEvent + TimingMap
    │
    ├── Timeline
    ├── Event List
    ├── Inspector
    ├── Marker / Region Manager
    ├── Lane Manager
    └── Transport / Audio
```

Cette architecture est essentielle : **l'interface ne doit jamais devenir la source de vérité du morceau.**

Une coordonnée X n'est pas une position musicale. Une ligne de TreeView n'est pas un événement. Un rectangle graphique n'est pas un snapshot.

Ce sont seulement différentes projections du même modèle.

## Lossless d'abord

Le backend Stadium vise un aller-retour sûr :

```python
from stadium_reaper_bridge.stadium import StadiumSong

song = StadiumSong.from_json_text(source)
assert song.to_json_text() == source
```

Les parties inconnues du format ne doivent pas être normalisées juste parce que Reapcase ne les comprend pas encore.

## TimingMap

Les opérations musicales importantes utilisent une représentation temporelle canonique.

Le TimingMap sert de frontière entre :

```text
position musicale
secondes
pixels de timeline
```

Le déplacement, le Snap, le zoom et les duplications ne doivent donc pas dépendre d'une hypothèse graphique fragile sur la largeur d'une mesure.

## Préférences Reapcase ≠ données Stadium

Des éléments tels que :

```text
visibilité des lanes
ordre des lanes
Focus Lane
Inspector
zoom
position des fenêtres utilitaires
préférences d'interface
```

appartiennent à Reapcase et ne doivent pas contaminer la Song Stadium.

---

# Démarrage

Reapcase maintient la compatibilité **Python 3.9+**.

Installation du projet et de l'audio desktop :

```bash
python -m pip install -e '.[desktop-audio]'
```

Tests :

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Lancement de l'éditeur :

```bash
PYTHONPATH=src python -m stadium_reaper_bridge.editor.app
```

Sous PowerShell :

```powershell
$env:PYTHONPATH="src"
python -m stadium_reaper_bridge.editor.app
```

L'interface desktop utilise Tkinter et la lecture audio optionnelle utilise `sounddevice` / PortAudio.

---

# Compatibilité Python 3.9

Python 3.9 est une cible réelle du projet, pas seulement une mention documentaire.

Le code source évite donc les syntaxes de typing qui nécessitent Python 3.10+, notamment les unions PEP 604 de type :

```python
int | None
```

au profit de formes compatibles :

```python
Optional[int]
```

La suite de tests contient un garde-fou afin d'éviter qu'une syntaxe incompatible rende de nouveau l'application impossible à importer sur la machine cible.

---

# Philosophie UX

Reapcase emprunte à REAPER ses idées de confort, pas son apparence ni toute sa complexité.

L'objectif est de réduire la programmation d'un show à quelques gestes naturels :

```text
sélectionner
placer
snapper
déplacer
dupliquer
inspecter
écouter
sauvegarder
```

Mais Reapcase possède un avantage qu'un DAW généraliste n'a pas : **il connaît la signification des objets qu'il affiche.**

Il peut donc proposer une ergonomie spécifique à Stadium, Second Helix, aux snapshots, aux loopers, aux markers, aux cycles, aux lights et aux autres événements du show plutôt que de tout réduire à des items génériques.

La direction du projet est ainsi :

> **la puissance d'une timeline de production, avec la conscience sémantique d'un éditeur de show.**

---

# Ce que Reapcase ne cherche pas à devenir

Reapcase n'est pas destiné à remplacer REAPER pour l'enregistrement ou le mixage audio.

Le projet ne cherche pas actuellement à fournir :

- enregistrement audio ;
- édition destructive de WAV ;
- time-stretch ;
- console de mixage complète ;
- automation audio de DAW ;
- hébergement de plugins ;
- réécriture arbitraire des données Stadium inconnues.

L'audio sert avant tout de **référence synchronisée au show** et de support de programmation.

---

# Limites et zones encore en évolution

Le format Stadium contient des familles et comportements qui restent partiellement documentés ou vendor-specific. Reapcase préfère signaler ces zones plutôt que d'inventer leur signification.

Certaines fonctions DAW récentes constituent encore une première tranche ergonomique : l'Inspector projette déjà les données sémantiques mais l'édition inline complète peut continuer à évoluer ; la création directe, les tooltips sémantiques et les opérations avancées sur régions sont des surfaces destinées à être progressivement enrichies autour des primitives déjà présentes.

La lecture audio reste volontairement plus simple qu'un moteur DAW complet. Les contraintes de formats, fréquences d'échantillonnage et offsets doivent continuer à être traitées explicitement plutôt que masquées par des conversions implicites.

---

# Tests et discipline de développement

Les changements importants doivent préserver trois propriétés :

1. **compatibilité native Stadium** ;
2. **cohérence musicale du modèle** ;
3. **réactivité de l'interface**.

Avant intégration, les PR doivent viser au minimum :

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
git diff --check
```

Les tests couvrent notamment le parsing/sérialisation, l'éditeur, le TimingMap, l'audio, la navigation, la sélection, les opérations ergonomiques et les régressions de compatibilité Python.

---

# Direction

Le cœur de Reapcase est maintenant suffisamment riche pour que la priorité ne soit plus seulement de « lire le format ».

La suite consiste à rendre la **création d'un show entier agréable** : moins de clics, davantage de manipulation directe, meilleure inspection, duplication musicale rapide, navigation instantanée et outils de construction de sections, tout en maintenant la frontière lossless qui protège les données Stadium.

À terme, l'architecture conserve également la possibilité d'adaptateurs externes, notamment REAPER, sans imposer le format REAPER au cœur du modèle.

Pour les détails techniques et les décisions d'architecture, voir [`docs/architecture.md`](docs/architecture.md).
