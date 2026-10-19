#!/usr/bin/env bash
# Met à jour SlowMovie-Inkdrop sur le Pi sans tout réinstaller.
# À lancer depuis le dossier cloné :  cd ~/slowmovie-inkdrop && ./deploy/update-pi.sh
set -euo pipefail

VENV=/home/admin/.SlowMovie
REPO="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO"
git pull --ff-only
"$VENV/bin/pip" install --upgrade "$REPO[video,tui]"
sudo ln -sf "$VENV/bin/slowmovie" /usr/local/bin/slowmovie
sudo systemctl restart slowmovie
sleep 5
systemctl --no-pager status slowmovie | head -8
slowmovie status
