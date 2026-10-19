# Déploiement

Trois endroits : le **VPS** (relais d'envoi d'images), le **Pi** (lecteur) et le **Mac** (pilotage).
Le VPS utilise Docker et Traefik (voir le dépôt `vps-infra`) : le relais est donc livré sous forme
d'image Docker, construite par GitHub Actions et publiée sur GHCR.

## 1. Relais sur le VPS

1. Depuis `~/dev/vps-infra`, préparer le sous-domaine **sans** `--auth`. Le relais a son propre mot de passe, et
   la protection Traefik bloquerait aussi le Pi, qui interroge le relais avec un jeton :

   ```bash
   scripts/new-site.sh slowmovie ~/dev/slowmovie-inkdrop --port 8077
   ```

   Le script ajoute `deploy/docker-compose.yml` et `.github/workflows/deploy.yml` au dépôt, crée
   `~/srv/slowmovie/` sur le VPS et pose les secrets GitHub. `--dry-run` génère les fichiers sans toucher au VPS.
   Le `Dockerfile` à la racine de ce dépôt construit l'image du relais.

2. Générer le mot de passe haché et le jeton de l'appareil (sur le Mac) :

   ```bash
   .venv/bin/slowmovie-relay hash-password
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. Sur le VPS, créer `~/srv/slowmovie/.env` (droits `600`, jamais dans git) d'après `deploy/relay/relay.env.example`
   avec `SLOWMOVIE_RELAY_PASSWORD_HASH` et `SLOWMOVIE_RELAY_DEVICE_TOKEN`.

4. Commiter et pousser le dépôt : le workflow construit l'image et déploie.

5. Vérifier : `curl -I https://slowmovie.charlescoiffier.fr/` répond `200`.

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
| 4. Swap | `CONF_SWAPSIZE=512` (critique sur le Zero 2 W) |
| 5. Paquets | git, pip, venv, ffmpeg, libs de compilation, Samba, avahi, rsync, unzip, police DejaVu (accents du texte sous l'image) |
| 6. Environnement | `python3 -m venv --system-site-packages /home/admin/.SlowMovie` |
| 7-8. Code et dépendances | `pip install` de ce dépôt (`[video,tui]` : ffmpeg-python, Pillow, Textual, httpx) |
| 9. waveshare-epd | téléchargé depuis GitHub (waveshareteam/e-Paper) puis installé à la main |
| 10. omni-epd | `--no-deps`, puis `importlib_resources`, `inky --no-deps`, `gpiodevice smbus2 spidev numpy` |
| 11. Vérification | `import omni_epd` et `import slowmovie_inkdrop` |
| 13. Samba | partage « SlowMovie » (dossier `Videos`, utilisateur `admin` seulement) ; demande un mot de passe Samba |
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
relay-url = https://slowmovie.charlescoiffier.fr
relay-token = <le jeton de l'appareil>
```

puis `sudo systemctl restart slowmovie`. Le nom de l'écran (`epd`) vaut `waveshare_epd.epd7in5_V2` par défaut.

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

- Droits `600` sur `slowmovie.conf` (contient le jeton) et sur `~/srv/slowmovie/.env`.
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
- [ ] Couper le relais 2 minutes : le film continue. Redémarrer le Pi pendant une image : elle reste jusqu'à l'échéance, sans boucle de redémarrage.
