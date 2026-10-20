# Déploiement

Trois endroits : le **VPS** (relais d'envoi d'images), le **Pi** (lecteur) et le **Mac** (pilotage).
Le VPS utilise Docker et Traefik (voir le dépôt `vps-infra`) : le relais est donc livré sous forme
d'image Docker, construite par GitHub Actions et publiée sur GHCR.

## 1. Relais sur le VPS

1. Depuis `~/dev/vps-infra`, préparer le sous-domaine **sans** `--auth`. Le relais a son propre mot de passe, et
   la protection Traefik bloquerait aussi le Pi, qui interroge le relais avec un jeton :

   ```bash
   scripts/new-site.sh inkdrop ~/dev/slowmovie-inkdrop --port 8077
   ```

   Le script ajoute `deploy/docker-compose.yml` et `.github/workflows/deploy.yml` au dépôt, crée
   `~/srv/inkdrop/` sur le VPS et pose les secrets GitHub. `--dry-run` génère les fichiers sans toucher au VPS.
   Le `Dockerfile` à la racine de ce dépôt construit l'image du relais.

2. Générer le mot de passe haché et le jeton de l'appareil (sur le Mac) :

   ```bash
   .venv/bin/slowmovie-relay hash-password
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. Sur le VPS, créer `~/srv/inkdrop/.env` (droits `600`, jamais dans git) d'après `deploy/relay/relay.env.example`
   avec `SLOWMOVIE_RELAY_PASSWORD_HASH` et `SLOWMOVIE_RELAY_DEVICE_TOKEN`.

4. Commiter et pousser le dépôt : le workflow construit l'image et déploie.

5. Vérifier : `curl -I https://inkdrop.charlescoiffier.fr/` répond `200`.

Notes :
- La file d'attente du relais est dans le conteneur : un nouveau déploiement la vide (les images expirent de toute
  façon en 6 h). Pour la conserver, monter un volume sur `/data` dans `deploy/docker-compose.yml`.
- Pour borner aussi la taille des corps de requête côté Traefik, ajouter aux labels du service :
  `traefik.http.middlewares.slowmovie-limit.buffering.maxRequestBodyBytes=12582912` et l'associer au routeur.
  Le relais refuse déjà tout envoi sans `Content-Length` ou de plus de 10 Mo.
- Le texte sous l'image est limité à 128 caractères côté relais (`SLOWMOVIE_RELAY_CAPTION_MAX` pour changer) ; le Pi
  applique de son côté `caption-max-chars` (128 par défaut) et ne fait pas confiance au relais.
- Le relais lit l'adresse du visiteur dans le dernier élément de `X-Forwarded-For` (ajouté par Traefik) pour limiter
  les essais de mot de passe : c'est correct derrière un seul proxy de confiance.

## 2. Lecteur sur le Pi (installation vierge, Raspberry Pi Zero 2 W)

La procédure reprend la partie « Zero 2 W » de la page Notion « slowmovie installation » ; seul le SlowMovie d'origine
est remplacé par ce dépôt.

### Préparer la carte SD

Avec Raspberry Pi Imager, installer **Raspberry Pi OS Lite (64 bits)** avec les mêmes réglages que dans la page Notion :
nom d'hôte `slomovpi`, utilisateur `admin`, **le Wi-Fi de la nouvelle box**, SSH activé.
Choisir un vrai mot de passe (pas `password`) ou, mieux, une clé SSH : le Pi sera joignable sur le réseau local.
Aucun identifiant n'est écrit dans ce dépôt, qui est public.

Si le Wi-Fi n'est pas encore connu du Pi, deux solutions : un **câble Ethernet** vers la box, ou saisir le Wi-Fi dans
l'Imager (partition `bootfs`).

### Se connecter

```bash
ssh-keygen -R slomovpi.local        # oublie l'ancienne identité de ce nom d'hôte
ssh admin@slomovpi.local            # répondre « yes » à la première connexion
```

### Installer

Sur le Pi :

```bash
git clone https://github.com/charlescoiffier/slowmovie-inkdrop
cd slowmovie-inkdrop
./deploy/install-pi.sh
```

Le script suit les étapes 3 à 14 de la recette et peut être relancé sans risque :

| Étape de la recette | Ce que fait le script |
|---|---|
| 3. SPI | l'active ; si un redémarrage est nécessaire, il le dit : `sudo reboot`, puis relancer le script |
| 4. Swap | `CONF_SWAPSIZE=512` (critique sur le Zero 2 W) ; sur Raspberry Pi OS récent (Debian 13 « Trixie »), `rpi-swap` fournit déjà une swap zram et entre en conflit avec `dphys-swapfile` : il est alors conservé |
| 5. Paquets | git, pip, venv, ffmpeg, libs de compilation, Samba, avahi, rsync, unzip, police DejaVu (accents du texte sous l'image) |
| 6. Environnement | `python3 -m venv --system-site-packages /home/admin/.SlowMovie` |
| 7-8. Code et dépendances | `pip install` de ce dépôt (`[video,tui]` : ffmpeg-python, Pillow, Textual, httpx) |
| 9. waveshare-epd | téléchargé depuis GitHub (waveshareteam/e-Paper, plus de 120 Mo) dans le dossier personnel (`/tmp` est en mémoire vive et trop petit sur un Zero 2 W), puis installé à la main |
| 10. omni-epd | `--no-deps`, puis `importlib_resources`, `inky --no-deps`, `gpiodevice smbus2 spidev numpy` |
| 11. Vérification | `import omni_epd` et `import slowmovie_inkdrop` |
| 13. Samba | partage « SlowMovie » (dossier `Videos`, utilisateur `admin` seulement) ; demande un mot de passe Samba. Lancé sans terminal (par ex. via `ssh admin@... ./deploy/install-pi.sh`), le script ne le demande pas : faire ensuite `sudo smbpasswd -a admin` |
| 14. Service | `slowmovie.service` (attente de 10 s au démarrage, redémarrage automatique) |

Il crée aussi `/home/admin/SlowMovie/slowmovie.conf` (droits `600`, jamais écrasé) avec les options de l'ancien service :
`delay = 40`, `increment = 4`, `fullscreen = true`, et la commande `/usr/local/bin/slowmovie`.
Le film et l'image de départ de l'ancien service ne sont pas repris : voir ci-dessous.

### Premier film

Sans film, le service attend (« Aucun film prêt »). Depuis le Mac :

```bash
slowmovie add ~/Movies/un-film.mp4     # ou glisser-déposer dans smb://slomovpi.local/SlowMovie
slowmovie                              # TUI : onglet Films, Entrée pour lancer
```

Commandes utiles sur le Pi : `systemctl status slowmovie`, `journalctl -u slowmovie -f`,
`tail -f ~/SlowMovie/slowmovie.log`, `slowmovie status`.

### Relier le Pi au relais

Dans `/home/admin/SlowMovie/slowmovie.conf` :

```
relay-url = https://inkdrop.charlescoiffier.fr
relay-token = <le jeton de l'appareil>
```

puis `sudo systemctl restart slowmovie`. Le nom de l'écran (`epd`) vaut `waveshare_epd.epd7in5_V2` par défaut.

### Mettre à jour le Pi

La première fois, le Pi doit avoir un vrai clone du dépôt (et non une copie) :

```bash
rm -rf ~/slowmovie-inkdrop && git clone https://github.com/charlescoiffier/slowmovie-inkdrop
cd slowmovie-inkdrop && ./deploy/install-pi.sh      # idempotent : ne refait que ce qui manque
```

Ensuite, pour chaque nouvelle version :

```bash
cd ~/slowmovie-inkdrop && ./deploy/update-pi.sh      # git pull, pip install --upgrade, redémarrage du service
```

## 2 bis. Pilotage depuis la passerelle (onglet « Réglages »)

L'onglet « Réglages » de `https://inkdrop.charlescoiffier.fr` permet, après une connexion **administrateur**, de voir
l'état du Pi et de le piloter : pause, passer, choix du film, point de départ et « aller à » en `hh:mm:ss`, sous-titres,
et les mêmes réglages que la TUI. Un changement met jusqu'à ~20 s à arriver (le Pi interroge le relais toutes les 20 s),
puis la page indique « appliqué » ou le motif du refus.

**Mise en place**

1. Choisir un mot de passe administrateur **différent** de celui des envois et calculer son haché :

   ```bash
   cd ~/dev/slowmovie-inkdrop && .venv/bin/slowmovie-relay hash-password
   ```

2. L'ajouter dans `~/srv/inkdrop/.env` sur le VPS (`SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH=scrypt:…`, voir
   `deploy/relay/relay.env.example`) puis recréer le conteneur :

   ```bash
   cd ~/srv/inkdrop && docker compose up -d --force-recreate
   ```

   Sans ce haché, l'onglet n'apparaît pas et les routes d'administration répondent 503.
3. Mettre le Pi à jour (section précédente).

**Sécurité**
- Le mot de passe d'envoi n'ouvre aucune route d'administration, et inversement. Session : cookie fermé
  (`HttpOnly`, `Secure`, `SameSite=Strict`), 8 h ; 5 mots de passe faux bloquent l'adresse 15 min.
- Le Pi **revalide chaque commande** : il ne peut être commandé que pour les réglages de la liste ci-dessus. Jamais
  modifiables à distance : l'adresse du relais, le jeton, l'écran, les dossiers. Pas de suppression ni d'envoi de film.
- Une commande que le Pi n'a pas récupérée en 10 minutes expire ; une commande n'est jamais appliquée deux fois.

**Sous-titres** : `subtitles = true` lit le fichier `.srt/.ass/.ssa` voisin s'il existe, sinon la piste intégrée au film
(`subtitle-track`, numéro parmi tous les flux de sous-titres, 0 par défaut). Seuls les sous-titres **texte** sont pris en
charge ; les sous-titres image (PGS, VobSub) sont ignorés.
La piste intégrée est **extraite une seule fois** dans `~/SlowMovie/subtitles/` (en tâche de fond : sur un gros film
sur un Pi Zero 2 W, comptez quelques minutes) ; les images affichées pendant ce temps partent sans sous-titres. Sans
cette extraction, chaque image mettrait près de 2 minutes (mesuré : 22 s sans sous-titres, 1 min 52 avec, pour un tiers du
film Kaguya).

**Point de départ** : il s'applique à la première lecture d'un film, à « Revenir au départ » et quand le film recommence
en boucle ; il ne déplace pas un film déjà commencé. « Aller à » saute à un timecode tout de suite.

**Point de fin** (pour sauter le générique de fin) : quand le film l'atteint, il passe au suivant, ou recommence en
boucle à son point de départ si la boucle est activée. Le point de fin doit suivre le point de départ et ne pas dépasser la
durée du film ; `0` l'efface. Avec « images au hasard », les images sont tirées entre le départ et la fin. Dans la TUI :
`e` ; sur la passerelle : le champ « fin » puis « Régler la fin ». Dans la TUI :
`t` (point de départ), `e` (point de fin), `g` (aller à), `z` (revenir au départ), `↓` depuis les onglets pour entrer dans la liste.

**Page de confirmation** : après un envoi accepté, la page passe sur `/envoi/<identifiant>`, qui suit l'image
(reçue, position, affichée) et peut être rechargée ou mise en favori.

## Onglet π-piquant (dessin au hasard)

Un troisième onglet tire au hasard 1 à 200 décimales de π et un angle entier de 1° à 359°, demande le PNG (800 px) au
service [pi-piquant](https://github.com/charlescoiffier/pi-piquant) et le met dans la file comme une image envoyée
(même mot de passe d'envoi, mêmes limites). Les autres réglages du dessin gardent leurs valeurs de base, mais sans la légende
du service : elle est écrite par le Pi comme texte de l'image, en gros, avec la marge réglable `caption_margin` (Réglages ou TUI,
40 px par défaut) pour tenir compte d'un cadre qui masque le bas de l'écran. **Le Pi doit être mis à jour** pour cela.

1. Récupérer la clé de l'API, celle de `API_KEY` dans `~/srv/pi-piquant/api.env` sur le VPS.
2. L'ajouter dans `~/srv/inkdrop/.env` : `SLOWMOVIE_RELAY_PIPIQUANT_API_KEY=<la clé>` (jamais dans git), puis :

   ```bash
   cd ~/srv/inkdrop && docker compose up -d --force-recreate
   ```

   Sans cette clé, l'onglet n'apparaît pas et la route répond 503.
3. Essai : ouvrir l'onglet, choisir une durée, « Tirer un pi-piquant » ; la page de suivi rappelle les décimales et l'angle tirés.

## 3. Mac

```bash
pipx install .          # ou : pip install -e .
slowmovie               # ouvre la TUI du Pi par SSH (ssh -t admin@slomovpi.local slowmovie)
slowmovie status
slowmovie add film.mp4  # copie un film vers le Pi (rsync), ou glisser-déposer dans le partage Samba
```

Variables facultatives : `SLOWMOVIE_HOST` (défaut `slomovpi.local`, nom d'hôte du Pi), `SLOWMOVIE_USER` (`admin`),
`SLOWMOVIE_REMOTE_PATH` (`/home/admin/SlowMovie`).

## 4. Sécurité

- Droits `600` sur `slowmovie.conf` (contient le jeton) et sur `~/srv/inkdrop/.env`.
- Changer le mot de passe d'envoi : `slowmovie-relay hash-password`, mettre à jour `.env`, redéployer.
- Changer le jeton de l'appareil : le modifier dans `.env` du VPS **et** dans `slowmovie.conf` du Pi.
- Le Pi n'ouvre aucun port : il interroge le relais en HTTPS sortant.

## 5. Recette sur le matériel

À faire une fois le Pi joignable et le relais déployé ; cocher seulement ce qui a été observé.

- [ ] `ssh admin@slomovpi.local 'systemctl is-active slowmovie && slowmovie status'` : service actif, film et progression repris.
- [ ] `slowmovie add ~/Movies/un-film.mp4` : le film passe « prêt » dans l'onglet Films en moins d'une minute (délai de stabilisation de 30 s).
- [ ] Glisser un film dans `smb://slomovpi.local/SlowMovie` : « copie en cours… » puis « prêt », jamais joué à moitié.
- [ ] Depuis un téléphone en 4G, envoyer une photo prise en portrait (1 minute) : elle s'affiche bien orientée, puis le film reprend à la même image.
- [ ] Envoyer une photo avec un texte de 128 caractères accentués (« Été à Paris… ») : il est lisible, centré en bas, sur 3 lignes au plus, sans carrés à la place des accents.
- [ ] Envoyer deux images depuis deux appareils : affichées dans l'ordre, le film reprend après la seconde.
- [ ] 5 mauvais mots de passe, puis le bon : blocage avec message ; un `.svg` et un GIF animé sont refusés.
- [ ] Onglet « Réglages » : mauvais mot de passe refusé, bon mot de passe accepté ; l'état du Pi s'affiche en moins de 40 s.
- [ ] Changer de film depuis le téléphone : le Pi bascule en moins de 40 s, le statut passe à « appliqué ».
- [ ] Régler un point de départ, puis « Aller à » un timecode : l'écran affiche l'image attendue.
- [ ] Activer les sous-titres sur le film Kaguya (ou voir le message si ce sont des sous-titres image).
- [ ] Un réglage interdit (par exemple `relay_url`, via `curl`) est refusé ; « Se déconnecter » redemande le mot de passe.
- [ ] Après un envoi d'image, la page `/envoi/<identifiant>` suit l'image jusqu'à « affichée ».
- [ ] Couper le relais 2 minutes : le film continue. Redémarrer le Pi pendant une image : elle reste jusqu'à l'échéance, sans boucle de redémarrage.
