# Reapcase

> This is the first user-facing Reapcase milestone. Preserve the existing backend architecture and treat the GUI as a thin layer over it.

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

Python 3.11+ suffit pour le modèle et les tests. La lecture desktop utilise la
petite API callback `sounddevice` (PortAudio) :

```bash
python -m pip install -e '.[desktop-audio]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

Lancer l'éditeur desktop (bibliothèque standard Tkinter uniquement) :

```bash
PYTHONPATH=src python -m stadium_reaper_bridge.editor.app
```

Audio lanes can be refreshed after external file changes, reordered from their
fixed headers, detached without deleting their WAVs, and supplemented with a
managed copy through **Add Audio Track...**. Structural audio edits are undoable;
undoing an add detaches the track but deliberately leaves the safely copied WAV
on disk for recovery.

Utilisez **Open JSON** pour charger une Song, sélectionnez les flags dans les
lanes STRUCTURE, STADIUM, SECOND HELIX, VIDEO et MIDI / OTHER, puis déplacez-les
par glisser-déposer ou avec **Shift Selected**. **Save As JSON** exige une
nouvelle destination et affiche le bilan lossless avant export. Le panneau du
bas indique sélection, curseur, modifications et types non pris en charge.

Glisser depuis un espace vide trace une sélection rectangulaire : sans
modificateur elle remplace la sélection, **Ctrl** bascule les flags touchés et
**Shift** les ajoute. Glisser un flag déjà sélectionné continue de déplacer le
groupe. La molette fait défiler le temps horizontalement (Shift est également
accepté), Ctrl+molette zoome et le bouton central déplace toujours la vue.

Jusqu'à huit pistes audio JSON sont affichées, en lecture seule, sous les flags.
**Locate Audio Folder** recherche les WAV manquants par suffixe relatif puis par
nom de fichier uniquement lorsque le résultat est unique. Un WAV résolu affiche
sa durée réelle à partir de son en-tête ; un WAV absent reste affiché comme
`FILE NOT FOUND`. `Fit Song` inclut la fin des WAV résolus via une tempo map
dérivée des flags START/TIME.

La barre transport **|<< / Play-Pause / Stop** pilote un unique flux de mixage
et affiche simultanément `MM:SS.mmm` et `BAR-BEAT.TICK`. Cliquez dans la règle
supérieure pour déplacer la tête de lecture. Les boutons **M** et **S** de chaque
lane sont des contrôles de monitoring locaux : ils ne changent jamais le JSON.
Les enveloppes sont calculées par deux workers, par petits blocs, puis conservées
en mémoire pour la session. L'éditeur reste utilisable si `sounddevice` ou le
périphérique audio est indisponible ; le diagnostic en bas en donne la raison.

### Limites du MVP

- la géométrie des déplacements utilise la signature initiale (les changements
  de signature restent visibles mais ne redéfinissent pas encore la grille) ;
- les changements de tempo START/TIME pilotent exactement la conversion entre
  secondes et ticks. Les changements de signature rythmique ne sont en revanche
  pas encore intégrés à l'affichage BAR-BEAT.TICK ; celui-ci utilise la signature
  START, sans modifier les événements TIME ;
- toutes les fixtures connues ont un offset audio nul. Les offsets non nuls sont
  conservés et signalés, mais restent dessinés au début tant que leur unité
  Stadium n'est pas établie ; une recherche audio ambiguë reste non résolue ;
- la lecture accepte des WAV PCM mono/stéréo 16 ou 24 bits de même fréquence
  d'échantillonnage, sans rééchantillonnage. Une piste à offset non nul rend le
  jeu résolu impropre à la lecture plutôt que d'en deviner l'unité ;
- huit pistes 48 kHz sont lues bloc par bloc : seuls un buffer par piste et au
  plus 2 000 couples de pics par waveform restent en mémoire. Le mixage PCM pur
  Python privilégie cette étape MVP lisible ; une extension native/vectorisée
  serait la prochaine optimisation à mesurer sur les machines Windows cibles ;
- pas de suppression ni d'édition de payload, d'audio ou de fichiers `.peak` ;
- pas d'intégration REAPER ni de reconstruction de backup Stadium ;
- zoom et édition détaillée des familles vendor-only sont reportés.

Exemple d'utilisation du modèle lossless :

```python
from stadium_reaper_bridge.stadium import StadiumSong

song = StadiumSong.from_json_text(source)
assert song.to_json_text() == source
```

La feuille de route et les décisions d'architecture sont détaillées dans
[`docs/architecture.md`](docs/architecture.md).
