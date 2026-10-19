#!/usr/bin/env bash
# Installation sur un Raspberry Pi Zero 2 W vierge (Raspberry Pi OS Lite 64 bits, utilisateur « admin »).
# À lancer depuis une copie du dépôt :   git clone https://github.com/charlescoiffier/slowmovie-inkdrop
#                                        cd slowmovie-inkdrop && ./deploy/install-pi.sh
# Suit la recette validée de la page Notion « slowmovie installation » (partie Zero 2 W), en remplaçant
# le SlowMovie d'origine par ce dépôt. Relançable sans risque : chaque étape vérifie son état.
set -euo pipefail

DATA=/home/admin/SlowMovie
VENV=/home/admin/.SlowMovie
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 3. SPI"
sudo raspi-config nonint do_spi 0
if [ ! -e /dev/spidev0.0 ]; then
  echo "SPI vient d'être activé : redémarrez (sudo reboot), puis relancez ce script."
  exit 0
fi

echo "== 4. Swap à 512 Mo (critique sur le Zero 2 W)"
sudo apt-get update
sudo apt-get install -y dphys-swapfile
if ! grep -q '^CONF_SWAPSIZE=512$' /etc/dphys-swapfile; then
  sudo dphys-swapfile swapoff
  if grep -q '^#\?CONF_SWAPSIZE=' /etc/dphys-swapfile; then
    sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
  else
    echo 'CONF_SWAPSIZE=512' | sudo tee -a /etc/dphys-swapfile >/dev/null
  fi
  sudo dphys-swapfile setup
  sudo dphys-swapfile swapon
fi
free -h | grep -i swap

echo "== 5. Paquets système"
sudo apt-get install -y git python3-pip python3-venv ffmpeg python3-dev libopenjp2-7 libtiff6 \
  build-essential libssl-dev libffi-dev unzip samba avahi-daemon rsync fonts-dejavu-core
sudo apt-get install -y libatlas3-base || echo "(libatlas3-base indisponible sur cette version : ignoré)"

echo "== 6. Environnement Python"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade pip setuptools wheel

echo "== 7-8. SlowMovie-Inkdrop et ses dépendances (ffmpeg-python, Pillow, Textual, httpx)"
"$VENV/bin/pip" install --upgrade "$REPO[video,tui]"

echo "== 9. waveshare-epd (installation manuelle)"
if ! "$VENV/bin/python" -c "import waveshare_epd" 2>/dev/null; then
  WORK="$(mktemp -d)"
  wget -O "$WORK/e-Paper.zip" https://github.com/waveshareteam/e-Paper/archive/refs/heads/master.zip
  unzip -q "$WORK/e-Paper.zip" -d "$WORK"
  "$VENV/bin/pip" install "$WORK/e-Paper-master/RaspberryPi_JetsonNano/python/"
  rm -rf "$WORK"
fi

echo "== 10. omni-epd et ses dépendances (sans résolution automatique)"
"$VENV/bin/pip" install --no-deps "git+https://github.com/robweber/omni-epd.git#egg=omni-epd"
"$VENV/bin/pip" install importlib_resources
"$VENV/bin/pip" install inky --no-deps
"$VENV/bin/pip" install gpiodevice smbus2 spidev numpy

echo "== 11. Vérification"
"$VENV/bin/python" -c "import omni_epd; print('omni_epd OK')"
"$VENV/bin/python" -c "import slowmovie_inkdrop; print('slowmovie_inkdrop', slowmovie_inkdrop.__version__)"
# `ssh admin@... slowmovie` n'a pas le venv dans son PATH : on expose la commande
sudo ln -sf "$VENV/bin/slowmovie" /usr/local/bin/slowmovie

echo "== Dossier de données et réglages"
mkdir -p "$DATA/Videos"
if [ ! -e "$DATA/slowmovie.conf" ]; then
  install -m 600 "$REPO/deploy/slowmovie.conf.example" "$DATA/slowmovie.conf"
fi

echo "== 13. Samba"
if ! grep -q '^\[SlowMovie\]' /etc/samba/smb.conf; then
  sudo tee -a /etc/samba/smb.conf < "$REPO/deploy/smb.conf.snippet" >/dev/null
fi
if ! sudo pdbedit -L | grep '^admin:' >/dev/null; then   # sans -q : pas de SIGPIPE avec pipefail
  echo "Choisissez le mot de passe Samba de l'utilisateur admin :"
  sudo smbpasswd -a admin
fi
sudo systemctl restart smbd avahi-daemon

echo "== 14. Service systemd"
sudo cp "$REPO/deploy/slowmovie.service" /etc/systemd/system/slowmovie.service
sudo systemctl daemon-reload
sudo systemctl enable slowmovie
sudo systemctl restart slowmovie
sleep 3
systemctl --no-pager status slowmovie | head -12

echo
echo "Terminé. Sans film, le service attend (« Aucun film prêt »). Depuis le Mac :"
echo "  slowmovie add un-film.mp4     puis     slowmovie      (TUI : onglet Films, Entrée pour lancer)"
