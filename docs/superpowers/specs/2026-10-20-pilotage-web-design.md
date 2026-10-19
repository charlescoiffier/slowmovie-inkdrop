# Pilotage depuis la passerelle, confirmation d'envoi, améliorations de la TUI — design

Date : 2026-10-20 · Statut : à relire · Complète [2026-10-14-slowmovie-inkdrop-design.md](2026-10-14-slowmovie-inkdrop-design.md)

## 1. Objectif

Quatre apports, validés par l'utilisateur :

1. **Onglet « Réglages » sur la passerelle** (`https://inkdrop.charlescoiffier.fr`) : voir l'état du Pi, choisir le film, régler la position de départ, les sous-titres et les autres réglages de la TUI, à distance, derrière un mot de passe **administrateur** distinct du mot de passe d'envoi.
2. **Page de confirmation d'envoi** : quand le formulaire d'envoi d'image est accepté, la même fenêtre passe sur une page dédiée qui suit l'image.
3. **Sous-titres intégrés au film** : lecture de la piste de sous-titres contenue dans le fichier (mkv, mp4), en plus des fichiers `.srt` voisins.
4. **TUI** : (a) navigation par `↓`/`↑` entre la barre d'onglets et leur contenu ; (b) **point de départ par film**, réglable en `hh:mm:ss`.

Réussite : depuis un téléphone, après connexion administrateur, changer de film, déplacer la position, activer les sous-titres ou modifier le délai s'applique sur le Pi en moins d'une minute, sans SSH ; aucun visiteur n'ayant que le mot de passe d'envoi ne peut modifier quoi que ce soit.

## 2. Décisions validées

- Mot de passe administrateur **séparé** du mot de passe d'envoi.
- Le Pi interroge le relais **toutes les 20 s** ; un changement met donc jusqu'à ~20 s à s'appliquer.
- Réglages modifiables à distance : **les mêmes que dans la TUI**, sauf `relay_url` (voir §5).
- Sous-titres intégrés pris en charge. Page de confirmation **dans le même onglet**.
- Décisions de l'implémenteur, à relire : `relay_url` jamais modifiable à distance ; `relay_poll_seconds` borné à 5–300 s à distance ; sémantique du point de départ décrite au §7.

## 3. Architecture du pilotage

Le Pi n'ouvre toujours aucun port et reste la seule source de vérité (`slowmovie.conf`). Le relais ne garde qu'un instantané et des commandes en attente.

**Pi → relais** : à chaque tour de synchronisation (20 s), `PUT /api/device/state` (jeton d'appareil) avec un instantané JSON (64 Ko au plus) :

```
{ "sent_at": <epoch>, "mode": "FILM|IMAGE", "paused": bool,
  "video": nom|null, "position_seconds": n, "duration_seconds": n,
  "error": "…", "queue": [{id, duration, caption}],
  "settings": {<réglages modifiables, voir §5>},
  "films": [{name, status, duration_seconds, fps, position_seconds, start_seconds, error, subtitles}] }
```

Le relais garde le dernier instantané et l'heure de réception (le Pi est « en ligne » si < 60 s).

**Relais → Pi** : l'administrateur crée des **commandes** ; le Pi les récupère avec `GET /api/device/commands` (celles en attente et de moins de 10 min), les applique dans le thread du lecteur (`Player.submit`), puis répond `POST /api/device/commands/{id}/ack` avec `{status: "applied"|"failed", message}`. Une commande déjà traitée n'est jamais rejouée (identifiants mémorisés, comme pour les images). Une commande non récupérée dans les 10 min expire.

**Commandes** (liste fermée) : `pause`, `resume`, `skip`, `play {video}`, `seek {video, seconds}`, `set_start {video, seconds}`, `reset {video}` (retour au point de départ), `settings {changes: {nom: valeur}}`.

Le relais valide la forme (type, taille, film présent dans le dernier instantané, réglage dans la liste blanche) ; **le Pi revalide tout** : types, bornes via `Settings.problems()`, existence du film, durée. Une commande invalide est refusée en bloc (rien n'est modifié) avec un message lisible.

## 4. Authentification administrateur

- `SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH` (même format scrypt `scrypt:…`). **Absent : l'onglet et les routes `/api/admin/*` sont désactivés** (503, onglet masqué).
- `POST /api/admin/login {password}` : comparaison scrypt à temps constant ; limitation propre aux connexions administrateur (5 échecs → blocage 15 min par IP) ; jamais de mot de passe dans les journaux. Succès : cookie `sm_admin` = jeton signé (HMAC-SHA256 sur expiration + aléa), `HttpOnly; Secure; SameSite=Strict; Path=/api/admin; Max-Age=8 h`. Clé de signature : `SLOWMOVIE_RELAY_SESSION_SECRET`, sinon aléatoire à chaque démarrage (les sessions sautent au redéploiement).
- Toutes les routes `/api/admin/*` (hors login) exigent le cookie valide **et** l'en-tête `X-Requested-With: slowmovie` (défense CSRF en plus de `SameSite`). `POST /api/admin/logout` efface le cookie.
- Le mot de passe d'envoi n'ouvre **aucune** route administrateur, et inversement.

## 5. Réglages modifiables à distance

`delay`, `increment`, `random_file`, `random_frames`, `loop`, `fullscreen`, `subtitles`, `timecode`, `subtitle_track` (nouveau, §6), `contrast`, `min_image_seconds`, `max_image_seconds`, `queue_max`, `queue_expiry_seconds`, `caption_max_chars`, `active_hours`, `relay_poll_seconds` (5–300 s à distance).

**Jamais à distance** : `relay_url`, `relay_token`, `epd`, `directory`, `file`, `clear`, `loglevel`, `start` — pour qu'une session administrateur volée ne puisse ni rediriger le Pi vers un autre relais, ni le déconnecter, ni changer son matériel. Aucune suppression ni envoi de film à distance.

## 6. Sous-titres intégrés

- Réglage `subtitle_track: int = 0` (numéro de piste de sous-titres, à partir de 0).
- `subtitles = true` : fichier `.srt/.ssa/.ass` voisin s'il existe (comportement actuel) ; **sinon** la piste intégrée `subtitle_track` via le filtre ffmpeg `subtitles=<fichier>:si=<n>`.
- Seuls les sous-titres **texte** (`subrip`, `ass`, `ssa`, `mov_text`, `webvtt`) sont pris en charge ; les sous-titres image (PGS, VobSub) sont ignorés avec un message dans le journal et dans l'état, jamais une erreur qui bloque l'affichage. `VideoInfo` gagne `subtitle_codecs` (sondage de tous les flux, plus seulement la vidéo).
- L'instantané indique pour chaque film le nombre de pistes texte, pour que la page propose le bon choix.

## 7. Point de départ par film et position (Pi, TUI et web)

- Stockage : `films.json` dans le dossier de données, `{nom: {"start_seconds": n}}`. La `Library` mémorise `fps` et `duration` ; conversion secondes ↔ image par `fps`.
- Le point de départ s'applique : à la **première lecture** (film sans progression), à **`reset`** (« revenir au départ », remplace la remise à zéro), et quand le film **recommence en boucle**.
- `set_start` **ne déplace pas** un film déjà commencé : sa progression reste intacte. `seek` déplace la position **maintenant** (saut dans le film), borné à `[0, durée]`.
- `play {video}` reprend à la position enregistrée.

## 8. Page de confirmation d'envoi

Après `POST /api/send` accepté, le formulaire fait `location.assign("/envoi/<id>")`. `GET /envoi/{id}` sert une page statique qui lit l'identifiant dans l'URL et interroge `/api/status/{id}` toutes les 5 s : « Image reçue », position, heure estimée, puis « affichée » ou « échec / expirée », et un bouton « Envoyer une autre image ». Un identifiant invalide donne une page « Envoi introuvable ». La page ne montre ni image, ni texte, ni mot de passe ; rechargement et favori sont sûrs (aucun renvoi de formulaire).

## 9. Page d'envoi et de réglages

Une seule page, deux onglets accessibles au clavier (`role=tablist`) : « Envoyer une image » (inchangé) et « Réglages ». L'onglet Réglages demande la connexion, puis montre : état du Pi (en ligne/hors ligne, dernier contact), film et avancement, pause/reprise/passer, liste des films (état, position, départ) avec « Lancer », « Aller à », « Départ » en `hh:mm:ss`, et les réglages groupés avec un bouton « Appliquer ». Chaque commande affiche « en attente du Pi… » puis « appliqué » ou le message d'échec. Tout texte venant du Pi (noms de films, légendes, messages) est inséré avec `textContent`, jamais en HTML.

## 10. TUI

- **Navigation** : `↓` depuis la barre d'onglets entre dans le contenu de l'onglet ; `↑` sur la première ligne y revient ; `←`/`→` changent d'onglet ; les touches `1`–`5` restent.
- **Films** : colonnes « Position » et « Départ » en `hh:mm:ss` ; `t` règle le point de départ, `g` saute à un timecode, `z` revient au point de départ ; `subtitle_track` apparaît dans Réglages.

## 11. Robustesse

Relais redéployé (instantané perdu) : le Pi renvoie le sien sous 20 s ; Pi hors ligne : boutons désactivés et message ; commande expirée : marquée « expirée » ; deux administrateurs : la dernière commande l'emporte, chacune est tracée ; film supprimé entre-temps : refus clair ; réglage invalide : refus en bloc.

## 12. Tests

Relais : connexion (bon, mauvais, blocage, cookie sans les bons attributs refusé, CSRF), routes administrateur sans cookie, validation des commandes, expiration, instantané trop gros, XSS (noms hostiles). Pi : chaque commande (succès, refus, bornes), `films.json`, positions, sous-titres (piste texte, image, absente, externe prioritaire). Bout en bout : commande web → Pi (lecteur simulé) → accusé → page. TUI : navigation `↓`/`↑`, colonnes, `t`/`g`/`z`. Page de confirmation : redirection, suivi, identifiant invalide.

## 13. Hors périmètre

Suppression/envoi de films à distance, plusieurs administrateurs avec des droits différents, push temps réel (WebSocket/long-polling), sous-titres image, choix de la langue audio.

## 14. Déploiement

Nouvelle variable `SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH` (et, facultative, `SLOWMOVIE_RELAY_SESSION_SECRET`) dans `~/srv/inkdrop/.env` ; l'utilisateur calcule le haché de son mot de passe administrateur (`slowmovie-relay hash-password`). Le Pi doit être mis à jour avec la nouvelle version.
