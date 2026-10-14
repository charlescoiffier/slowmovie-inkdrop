#!/usr/bin/env bash
# À lancer sur le Pi, depuis une copie du dépôt : ./deploy/install-pi.sh
set -euo pipefail
TARGET=/home/admin/SlowMovie
REPO="$(cd "$(dirname "$0")/.." && pwd)"

sudo apt-get update
sudo apt-get install -y ffmpeg samba avahi-daemon rsync python3-venv python3-dev libopenjp2-7 fonts-dejavu-core

mkdir -p "$TARGET/Videos"
python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install --upgrade pip
"$TARGET/.venv/bin/pip" install "$REPO[video,tui,epd]"

if ! grep -q '^\[SlowMovie\]' /etc/samba/smb.conf; then
  sudo tee -a /etc/samba/smb.conf < "$REPO/deploy/smb.conf.snippet" >/dev/null
fi
echo "Définissez le mot de passe Samba de l'utilisateur admin :"
sudo smbpasswd -a admin
sudo systemctl restart smbd avahi-daemon

sudo cp "$REPO/deploy/slowmovie.service" /etc/systemd/system/slowmovie.service
sudo systemctl daemon-reload
sudo systemctl enable --now slowmovie.service

echo "Terminé. Nom d'hôte actuel : $(hostname). Pour joindre le Pi par slowmovie.local :"
echo "  sudo hostnamectl set-hostname slowmovie"
