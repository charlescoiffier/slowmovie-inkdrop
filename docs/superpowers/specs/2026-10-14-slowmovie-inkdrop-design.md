# slowmovie-inkdrop — design

Date : 2026-10-14 · Statut : à relire · Dépôt : https://github.com/charlescoiffier/slowmovie-inkdrop

## 1. Objectif

Faire évoluer SlowMovie (film joué image par image sur un écran e-paper, Raspberry Pi) avec trois apports :

1. **Envoi d'images à distance** : n'importe qui, depuis un téléphone ou un ordinateur, envoie une image et une durée. L'image remplace le film à l'écran pendant cette durée, le film étant en pause, puis le film reprend là où il s'était arrêté.
2. **Interface terminal simplifiée** : une TUI (Text User Interface) au clavier, dans l'esprit de [cadav](https://github.com/charlescoiffier/cadav) (Textual, thème « galaxy »).
3. **Ajout de films simplifié** : glisser-déposer dans le Finder (partage Samba) ou commande `slowmovie add` depuis le Mac.

Réussite : envoyer une photo depuis un téléphone sur Internet l'affiche sur l'écran après la fin de la file, pour la durée choisie, sans rien casser de la lecture du film ; gérer le lecteur et ajouter un film ne demande plus d'éditer de fichier ni de retenir d'options.

## 2. Existant (dans ce dossier)

| Fichier | Rôle | Devenir |
|---|---|---|
| `slowmovie.py` | SlowMovie amont (Tom Whitwell) : `omni_epd`, `configargparse`, `ffmpeg-python`. Boucle unique, état dans `nowPlaying`, `progress/<film>.progress`, `slowmovie.conf`, dossier `Videos/`. | Base du lecteur, refactorée en module (voir §5). **Licence amont à vérifier et conserver.** |
| `slowmovie_config.py` | Outil Mac → Pi par SSH/scp, menus à flèches, édite `slowmovie.conf`. Hôte `admin@…`, chemin `/home/admin/SlowMovie`, écran `waveshare_epd.epd7in5_V2`. | Remplacé par la TUI. Sert de référence pour les réglages à couvrir. |
| `slowmovie_plus.py` | Menu numéroté local sur le Pi (film, délai, incrément, aléatoire, progression, plage horaire par timers systemd, contraste, remise à zéro, pause). | Remplacé par la TUI. Il lit `progress.json`, alors que le lecteur écrit `progress/*.progress` : sa progression est donc fausse. À ne pas reprendre tel quel. |

Les trois fichiers seront déplacés dans `legacy/` quand le dépôt sera initialisé, pour référence.

## 3. Décisions prises

- Envoi ouvert depuis Internet, via un **relais sur le VPS** : le Pi n'a que des connexions sortantes, aucun port ouvert.
- Accès : **un mot de passe partagé**, avec limitation des essais.
- Plusieurs envois : **file d'attente**, chaque image affichée pendant sa durée, le film reprend quand la file est vide.
- « Paramètres supplémentaires » = surtout la simplification de l'interface et de l'ajout de films, pas de nouvelles options de rendu.
- Films : partage Samba (Finder) **et** `slowmovie add` (rsync).
- Interface : TUI Textual au clavier.

## 4. Architecture

Trois blocs qui ne communiquent que par des interfaces simples.

1. **Relais** (VPS, Python) : page d'envoi, validation des fichiers, file des envois. Ignore tout de l'écran.
2. **Lecteur** (Pi, service systemd `slowmovie`) : joue le film, interroge le relais, gère les états `FILM` / `IMAGE`. **Seul propriétaire de l'écran.**
3. **TUI / CLI** (Pi) : lit l'état du lecteur et lui envoie des commandes. Elle ne touche jamais à l'écran.

Depuis le Mac, la commande `slowmovie` ouvre la TUI du Pi par `ssh -t slowmovie.local slowmovie`. On garde ainsi un seul code d'interface, et l'usage à distance de l'ancien `slowmovie_config.py`.

Communication TUI ↔ lecteur : le lecteur écrit atomiquement un `state.json` (état, film, image courante, fin de l'image affichée, file) ; les commandes (pause, reprise, passer, lancer un film, recharger la configuration) passent par un socket Unix local. Le détail est laissé au plan d'implémentation.

## 5. Lecteur

**Compatibilité** : mêmes fichiers d'état que l'amont (`slowmovie.conf`, `nowPlaying`, `progress/<film>.progress`, `Videos/`), pour que la mise à jour n'efface pas la progression des films en cours.

**Refactor** : `slowmovie.py` utilise des variables globales et une boucle unique. Il est découpé en un module testable (sélection du film, extraction d'image, état), sans changer son comportement tant qu'aucun envoi n'est en attente.

**États**
- `FILM` : avance comme aujourd'hui, position enregistrée à chaque rafraîchissement.
- `IMAGE` : atteint au prochain moment sûr, jamais au milieu d'un rafraîchissement. Un rafraîchissement unique, puis l'écran est mis en veille (l'e-paper conserve l'image sans courant). À l'échéance : image suivante de la file, ou retour à `FILM` à la position enregistrée.

**Robustesse**
- L'heure de fin d'affichage est écrite sur disque : un redémarrage du Pi pendant une image reprend jusqu'à l'échéance.
- Relais injoignable : le film continue. Image illisible : écartée, signalée dans le journal.
- Une image qui attend plus de 6 h dans la file expire.

**Garde-fous (réglables)** : durée minimale 1 min ; durée maximale 1 h ; file de 10 images au plus (au-delà, l'envoi est refusé avec un message clair).

**Image** : le relais valide et réencode ; le Pi fait la conversion finale pour son écran (dimensions, orientation, niveaux de gris ou tramage). Écran actuel supposé : `waveshare_epd.epd7in5_V2`.

## 6. Relais

Python, derrière le reverse proxy déjà utilisé pour cadav (à confirmer en lisant `vps-infra`), sur un sous-domaine du type `slowmovie.charlescoiffier.fr`.

- `GET /` : page d'envoi pour téléphone (choisir ou prendre une photo, durée, mot de passe, envoyer), en HTML sans framework.
- `POST /api/send` : image, durée, mot de passe → identifiant, position dans la file, heure estimée.
- `GET /api/status/{id}` : suivi sur la page.
- Routes du Pi, avec un **jeton d'appareil** distinct du mot de passe : liste des envois en attente, téléchargement d'une image, accusé (« affichée » ou « échec »). Le Pi interroge toutes les 15 à 30 s.

**Sécurité**
- Mot de passe stocké haché ; 5 échecs depuis une IP → blocage 15 min ; limite d'envois par IP et par heure.
- 10 Mo maximum ; type réel vérifié ; SVG et images animées refusés ; réencodage Pillow qui retire métadonnées et contenus piégés.
- Jamais de mot de passe dans les journaux. HTTPS obligatoire.
- Images supprimées du VPS dès l'accusé du Pi ou à l'expiration.

## 7. TUI et CLI

Textual, thème « galaxy » de cadav. `slowmovie` ouvre la TUI ; `?` aide, `Ctrl+P` palette de commandes.

1. **Lecteur** : état, film en cours, image, pourcentage, temps restant estimé, prochain rafraîchissement, résumé de la file. `Espace` pause/reprise, `s` image suivante de la file.
2. **Films** : liste avec progression. `Entrée` lance, `r` au hasard, `z` remet à zéro, `d` supprime (confirmation), `a` aide à l'ajout.
3. **File d'attente** : `x` retire, `c` vide.
4. **Réglages** : délai, incrément, aléatoire (images / films), boucle, plein écran, contraste, sous-titres / timecode, durées min/max, taille de la file. Écrits dans `slowmovie.conf`, appliqués par une commande de rechargement.
5. **Journal**.

Commandes directes : `slowmovie status | pause | resume | play <film> | skip | add <fichier>` ; `slowmovie run` lance le service.

## 8. Ajout de films

- **Finder** : partage Samba « SlowMovie » sur le dossier `Videos/`.
- **Mac** : `slowmovie add film.mp4` copie par rsync vers `slowmovie.local` (mDNS, avahi sur le Pi) : plus de dépendance à l'IP, qui a changé avec la box.
- **Pi** : surveillance du dossier ; quand la taille d'un fichier cesse de changer, vérification `ffprobe`, enregistrement, signalement dans la TUI si illisible. Pas de transcodage par défaut.

## 9. Tests

- Pilote d'écran abstrait : version Waveshare (`omni_epd`) sur le Pi, version « fichier PNG » sur le Mac.
- Logique (états, file, reprise, expirations) testée avec une horloge simulée.
- API du relais testée avec le client de test du framework ; traitement d'image testé avec des fichiers réels, y compris malformés.
- Intégration continue GitHub Actions, comme cadav.

## 10. Organisation du dépôt

Python ≥ 3.11 : `slowmovie_inkdrop/{player,tui,cli,relay,imaging}`, `deploy/` (service systemd, Samba, relais), `tests/`, `docs/`, `legacy/`.

## 11. Points ouverts

1. **Plage horaire d'activité** (présente dans `slowmovie_plus.py`) : reprise **en option**, désactivée par défaut. Réglage `active-hours` (début-fin) géré dans le lecteur, sans timers systemd ; hors plage, l'écran reste sur sa dernière image et le lecteur attend. Les images envoyées suivent la même règle. Réglable dans la TUI.
2. **Mise à jour du Pi** : le code actuellement installé sur le Pi n'a pas pu être consulté (réseau changé). Les fichiers de ce dossier font foi jusqu'à vérification. *(Validé.)*
3. **Licence** du SlowMovie amont à confirmer avant publication. *(Validé : à vérifier avant de publier.)*
4. **Reverse proxy et domaine** du VPS à confirmer dans `vps-infra`. *(Validé : à vérifier au moment du déploiement.)*
