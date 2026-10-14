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
- Le relais lit l'adresse du visiteur dans le dernier élément de `X-Forwarded-For` (ajouté par Traefik) pour limiter
  les essais de mot de passe : c'est correct derrière un seul proxy de confiance.

## 2. Lecteur sur le Pi

1. Mettre le Pi sur le réseau (voir plus bas), puis copier ce dépôt sur le Pi et lancer :

   ```bash
   ./deploy/install-pi.sh
   ```

   Le script installe ffmpeg, Samba, avahi et rsync, crée l'environnement Python dans `/home/admin/SlowMovie`,
   publie le partage Samba « SlowMovie » (dossier `Videos`) et active le service `slowmovie`.

2. Pour être joignable sous le nom `slowmovie.local` : `sudo hostnamectl set-hostname slowmovie`.

3. Relier le Pi au relais dans `/home/admin/SlowMovie/slowmovie.conf` (droits `600`), puis redémarrer :

   ```
   relay-url = https://slowmovie.charlescoiffier.fr
   relay-token = <le jeton de l'appareil>
   ```

   ```bash
   sudo systemctl restart slowmovie
   ```

   Le nom de l'écran (`epd`) vaut `waveshare_epd.epd7in5_V2` par défaut. Les fichiers d'état de l'ancien SlowMovie
   (`nowPlaying`, `progress/`, `Videos/`) sont repris tels quels.

### Wi-Fi après un changement de box

Le Pi garde en mémoire le Wi-Fi de l'ancienne box. Deux solutions :
- **Câble Ethernet** vers la nouvelle box : aucune configuration à toucher, le Pi obtient une adresse tout seul.
- **Carte SD** : éteindre le Pi, brancher la carte sur le Mac, et ajouter le nouveau Wi-Fi sur la partition
  `bootfs` (selon la version de Raspberry Pi OS). Ne jamais écrire le mot de passe Wi-Fi dans ce dépôt.

## 3. Mac

```bash
pipx install .          # ou : pip install -e .
slowmovie               # ouvre la TUI du Pi par SSH (ssh -t admin@slowmovie.local slowmovie)
slowmovie status
slowmovie add film.mp4  # copie un film vers le Pi (rsync), ou glisser-déposer dans le partage Samba
```

Variables facultatives : `SLOWMOVIE_HOST` (défaut `slowmovie.local`), `SLOWMOVIE_USER` (`admin`),
`SLOWMOVIE_REMOTE_PATH` (`/home/admin/SlowMovie`).

## 4. Sécurité

- Droits `600` sur `slowmovie.conf` (contient le jeton) et sur `~/srv/slowmovie/.env`.
- Changer le mot de passe d'envoi : `slowmovie-relay hash-password`, mettre à jour `.env`, redéployer.
- Changer le jeton de l'appareil : le modifier dans `.env` du VPS **et** dans `slowmovie.conf` du Pi.
- Le Pi n'ouvre aucun port : il interroge le relais en HTTPS sortant.

## 5. Recette sur le matériel

À faire une fois le Pi joignable et le relais déployé ; cocher seulement ce qui a été observé.

- [ ] `ssh admin@slowmovie.local 'systemctl is-active slowmovie && slowmovie status'` : service actif, film et progression repris.
- [ ] `slowmovie add ~/Movies/un-film.mp4` : le film passe « prêt » dans l'onglet Films en moins de 30 s.
- [ ] Glisser un film dans `smb://slowmovie.local/SlowMovie` : « copie en cours… » puis « prêt », jamais joué à moitié.
- [ ] Depuis un téléphone en 4G, envoyer une photo prise en portrait (1 minute) : elle s'affiche bien orientée, puis le film reprend à la même image.
- [ ] Envoyer deux images depuis deux appareils : affichées dans l'ordre, le film reprend après la seconde.
- [ ] 5 mauvais mots de passe, puis le bon : blocage avec message ; un `.svg` et un GIF animé sont refusés.
- [ ] Couper le relais 2 minutes : le film continue. Redémarrer le Pi pendant une image : elle reste jusqu'à l'échéance, sans boucle de redémarrage.
