#!/usr/bin/env python3
"""
slowmovie_plus.py — Menu interactif pour SlowMovie
Usage : python3 slowmovie_plus.py
"""

import os
import sys
import glob
import subprocess
import configparser
from datetime import datetime, timedelta

# ── Chemins ────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONF_FILE   = os.path.join(SCRIPT_DIR, "slowmovie.conf")
VIDEOS_DIR  = os.path.join(SCRIPT_DIR, "Videos")
VIDEO_EXTS  = (".mp4", ".mkv", ".avi", ".mov", ".m4v")

# ── Helpers ─────────────────────────────────────────────────────────────────
def clear():
    os.system("clear")

def pause():
    input("\n  Appuie sur Entrée pour continuer…")

def service_running():
    r = subprocess.run(["systemctl", "is-active", "slowmovie"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"

def restart_service():
    if service_running():
        subprocess.run(["sudo", "systemctl", "restart", "slowmovie"])
        print("  ✓ Service redémarré.")
    else:
        print("  ℹ️  Le service n'est pas actif — les changements seront pris en compte au prochain lancement.")

def read_conf():
    """Lit le fichier .conf et retourne un dict des clés/valeurs."""
    conf = {}
    if not os.path.exists(CONF_FILE):
        return conf
    with open(CONF_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    k, _, v = line.partition("=")
                    conf[k.strip()] = v.strip()
    return conf

def write_conf_key(key, value):
    """Met à jour ou ajoute une clé dans slowmovie.conf."""
    lines = []
    found = False
    if os.path.exists(CONF_FILE):
        with open(CONF_FILE) as f:
            lines = f.readlines()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(key + "=") or stripped.startswith(key + " ="):
            new_lines.append(f"{key} = {value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key} = {value}\n")
    with open(CONF_FILE, "w") as f:
        f.writelines(new_lines)

def list_videos():
    videos = []
    for ext in VIDEO_EXTS:
        videos += glob.glob(os.path.join(VIDEOS_DIR, f"*{ext}"))
        videos += glob.glob(os.path.join(VIDEOS_DIR, f"*{ext.upper()}"))
    return sorted(set(videos))

def get_video_info(path):
    """Retourne (durée_secondes, nb_frames, fps) via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=nb_frames,r_frame_rate,duration",
             "-of", "default=noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=10
        )
        info = {}
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip()
        fps_raw = info.get("r_frame_rate", "24/1")
        num, _, den = fps_raw.partition("/")
        fps = float(num) / float(den) if den else float(num)
        duration = float(info.get("duration", 0))
        nb_frames = int(info.get("nb_frames", 0)) or int(duration * fps)
        return duration, nb_frames, fps
    except Exception:
        return 0, 0, 24.0

def estimate_time(nb_frames, current_frame, delay, increment):
    """Estime le temps restant pour finir le film."""
    remaining = nb_frames - current_frame
    if increment <= 0:
        return "?"
    steps = remaining / increment
    seconds = steps * delay
    return str(timedelta(seconds=int(seconds)))

# ── Écran principal ──────────────────────────────────────────────────────────
def header():
    conf = read_conf()
    film = conf.get("file", "—")
    delay = conf.get("delay", "120")
    incr  = conf.get("increment", "4")
    status = "🟢 actif" if service_running() else "🔴 arrêté"

    clear()
    print("╔══════════════════════════════════════════════╗")
    print("║          🎞  SlowMovie — Menu principal        ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  Service   : {status:<32}║")
    print(f"║  Film      : {os.path.basename(film):<32}║")
    print(f"║  Délai     : {delay + ' s':<32}║")
    print(f"║  Incrément : {incr + ' frames':<32}║")
    print("╠══════════════════════════════════════════════╣")
    print("║  1. Choisir un film                          ║")
    print("║  2. Réglage du délai de rafraîchissement     ║")
    print("║  3. Réglage du nombre de frames à sauter     ║")
    print("║  4. Mode aléatoire                           ║")
    print("║  5. Progression & temps restant              ║")
    print("║  6. Plage horaire d'activité                 ║")
    print("║  7. Contraste / rendu                        ║")
    print("║  8. Remettre un film à zéro                  ║")
    print("║  9. Pause / Reprendre le service             ║")
    print("║  0. Quitter                                  ║")
    print("╚══════════════════════════════════════════════╝")

# ── Option 1 : Choisir un film ───────────────────────────────────────────────
def menu_choose_film():
    videos = list_videos()
    if not videos:
        print(f"\n  Aucune vidéo trouvée dans {VIDEOS_DIR}")
        pause(); return
    clear()
    print("  📂 Films disponibles :\n")
    for i, v in enumerate(videos, 1):
        name = os.path.basename(v)
        dur, frames, fps = get_video_info(v)
        size_mb = os.path.getsize(v) / 1_048_576
        print(f"  {i:2}. {name:<35} {size_mb:6.0f} Mo  {int(dur//60)} min")
    print(f"\n   0. Annuler")
    choice = input("\n  Ton choix : ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(videos):
        selected = videos[int(choice) - 1]
        write_conf_key("file", selected)
        print(f"\n  ✓ Film sélectionné : {os.path.basename(selected)}")
        restart_service()
    pause()

# ── Option 2 : Délai ─────────────────────────────────────────────────────────
def menu_delay():
    clear()
    print("  ⏱  Délai entre deux mises à jour de l'écran\n")
    print("  Exemples courants :")
    print("    60   s  → 1 min  (très dynamique)")
    print("    120  s  → 2 min  (défaut SlowMovie)")
    print("    300  s  → 5 min")
    print("    3600 s  → 1 h    (très lent)\n")
    val = input("  Nouveau délai en secondes : ").strip()
    if val.isdigit() and int(val) > 0:
        write_conf_key("delay", val)
        print(f"  ✓ Délai mis à jour : {val} s")
        restart_service()
    else:
        print("  ✗ Valeur invalide.")
    pause()

# ── Option 3 : Incrément ─────────────────────────────────────────────────────
def menu_increment():
    clear()
    print("  🎞  Nombre de frames à sauter entre chaque affichage\n")
    print("  Exemples :")
    print("    1  → très précis, film très long")
    print("    4  → défaut SlowMovie")
    print("    12 → avance rapide (~3×)")
    print("    24 → 1 seconde de film par rafraîchissement\n")
    val = input("  Nombre de frames : ").strip()
    if val.isdigit() and int(val) > 0:
        write_conf_key("increment", val)
        print(f"  ✓ Incrément mis à jour : {val} frames")
        restart_service()
    else:
        print("  ✗ Valeur invalide.")
    pause()

# ── Option 4 : Mode aléatoire ────────────────────────────────────────────────
def menu_random():
    clear()
    conf = read_conf()
    print("  🎲 Mode aléatoire\n")
    print(f"  Frames aléatoires  (random-frames) : {'oui' if 'random-frames' in conf else 'non'}")
    print(f"  Films aléatoires   (random-file)   : {'oui' if 'random-file' in conf else 'non'}\n")
    print("  1. Activer frames aléatoires")
    print("  2. Désactiver frames aléatoires")
    print("  3. Activer ordre de films aléatoire")
    print("  4. Désactiver ordre de films aléatoire")
    print("  0. Retour")
    choice = input("\n  Ton choix : ").strip()
    if choice == "1":
        write_conf_key("random-frames", "true")
        print("  ✓ Frames aléatoires activées.")
        restart_service()
    elif choice == "2":
        write_conf_key("random-frames", "false")
        print("  ✓ Frames aléatoires désactivées.")
        restart_service()
    elif choice == "3":
        write_conf_key("random-file", "true")
        print("  ✓ Ordre aléatoire des films activé.")
        restart_service()
    elif choice == "4":
        write_conf_key("random-file", "false")
        print("  ✓ Ordre aléatoire désactivé.")
        restart_service()
    pause()

# ── Option 5 : Progression ───────────────────────────────────────────────────
def menu_progress():
    clear()
    conf = read_conf()
    film = conf.get("file", "")
    delay = int(conf.get("delay", 120))
    incr  = int(conf.get("increment", 4))
    print("  📊 Progression du film en cours\n")
    if not film or not os.path.exists(film):
        print("  Aucun film configuré ou fichier introuvable.")
        pause(); return
    dur, nb_frames, fps = get_video_info(film)
    # Lire la frame actuelle depuis le fichier de progression si existant
    progress_file = os.path.join(SCRIPT_DIR, "progress.json")
    current = 0
    if os.path.exists(progress_file):
        import json
        try:
            with open(progress_file) as pf:
                data = json.load(pf)
                current = data.get(os.path.basename(film), 0)
        except Exception:
            pass
    pct = (current / nb_frames * 100) if nb_frames else 0
    bar_len = 40
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    remaining = estimate_time(nb_frames, current, delay, incr)
    print(f"  Film     : {os.path.basename(film)}")
    print(f"  Frame    : {current} / {nb_frames}  ({pct:.1f}%)")
    print(f"  [{bar}]")
    print(f"  Durée réelle        : {int(dur//3600)}h{int((dur%3600)//60)}m")
    print(f"  Temps restant (est.): {remaining}")
    print(f"  Délai × incrément   : {delay}s × {incr} frames")
    pause()

# ── Option 6 : Plage horaire ─────────────────────────────────────────────────
def menu_schedule():
    clear()
    print("  🕐 Plage horaire d'activité\n")
    print("  (Nécessite que SlowMovie tourne comme service systemd)\n")
    start = input("  Heure de début (ex: 08:00) : ").strip()
    end   = input("  Heure de fin   (ex: 22:00) : ").strip()
    # Crée deux timers systemd
    try:
        start_h, start_m = start.split(":")
        end_h,   end_m   = end.split(":")
    except ValueError:
        print("  ✗ Format invalide.")
        pause(); return
    start_timer = f"""[Unit]
Description=Démarrer SlowMovie
[Timer]
OnCalendar=*-*-* {start_h}:{start_m}:00
Persistent=true
[Install]
WantedBy=timers.target
"""
    stop_timer = f"""[Unit]
Description=Arrêter SlowMovie
[Timer]
OnCalendar=*-*-* {end_h}:{end_m}:00
Persistent=true
[Install]
WantedBy=timers.target
"""
    # Écriture et activation
    for name, content, action in [
        ("slowmovie-start.timer", start_timer, "start"),
        ("slowmovie-stop.timer",  stop_timer,  "stop"),
    ]:
        path = f"/tmp/{name}"
        with open(path, "w") as f:
            f.write(content)
        # Le service associé doit exister — on crée aussi le .service
        svc_content = f"""[Unit]
Description=SlowMovie {action}
[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl {action} slowmovie
"""
        svc_path = f"/tmp/slowmovie-{action}.service"
        with open(svc_path, "w") as f:
            f.write(svc_content)
        subprocess.run(["sudo", "mv", path, f"/etc/systemd/system/{name}"])
        subprocess.run(["sudo", "mv", svc_path, f"/etc/systemd/system/slowmovie-{action}.service"])
    subprocess.run(["sudo", "systemctl", "daemon-reload"])
    subprocess.run(["sudo", "systemctl", "enable", "--now", "slowmovie-start.timer"])
    subprocess.run(["sudo", "systemctl", "enable", "--now", "slowmovie-stop.timer"])
    print(f"\n  ✓ SlowMovie actif de {start} à {end} tous les jours.")
    pause()

# ── Option 7 : Contraste ─────────────────────────────────────────────────────
def menu_contrast():
    clear()
    conf = read_conf()
    current = conf.get("contrast", "1.0")
    print(f"  🎨 Contraste actuel : {current}\n")
    print("  Valeurs recommandées : 0.8 (doux) | 1.0 (normal) | 1.2 (vif) | 1.5 (fort)\n")
    val = input("  Nouveau contraste : ").strip()
    try:
        f = float(val)
        if 0.1 <= f <= 3.0:
            write_conf_key("contrast", val)
            print(f"  ✓ Contraste mis à jour : {val}")
            restart_service()
        else:
            print("  ✗ Valeur hors plage (0.1 – 3.0).")
    except ValueError:
        print("  ✗ Valeur invalide.")
    pause()

# ── Option 8 : Reset progression ────────────────────────────────────────────
def menu_reset():
    videos = list_videos()
    if not videos:
        print("\n  Aucune vidéo trouvée.")
        pause(); return
    clear()
    print("  🔄 Remettre un film à zéro\n")
    for i, v in enumerate(videos, 1):
        print(f"  {i:2}. {os.path.basename(v)}")
    print("   0. Annuler")
    choice = input("\n  Ton choix : ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(videos):
        selected = videos[int(choice) - 1]
        progress_file = os.path.join(SCRIPT_DIR, "progress.json")
        import json
        data = {}
        if os.path.exists(progress_file):
            try:
                with open(progress_file) as pf:
                    data = json.load(pf)
            except Exception:
                pass
        data[os.path.basename(selected)] = 0
        with open(progress_file, "w") as pf:
            json.dump(data, pf, indent=2)
        print(f"\n  ✓ Progression remise à zéro : {os.path.basename(selected)}")
    pause()

# ── Option 9 : Pause / Reprendre ────────────────────────────────────────────
def menu_pause():
    clear()
    if service_running():
        print("  ⏸  Le service est actif.\n")
        confirm = input("  Mettre en pause ? (o/n) : ").strip().lower()
        if confirm == "o":
            subprocess.run(["sudo", "systemctl", "stop", "slowmovie"])
            print("  ✓ Service mis en pause.")
    else:
        print("  ▶️  Le service est arrêté.\n")
        confirm = input("  Reprendre ? (o/n) : ").strip().lower()
        if confirm == "o":
            subprocess.run(["sudo", "systemctl", "start", "slowmovie"])
            print("  ✓ Service relancé.")
    pause()

# ── Boucle principale ────────────────────────────────────────────────────────
def main():
    actions = {
        "1": menu_choose_film,
        "2": menu_delay,
        "3": menu_increment,
        "4": menu_random,
        "5": menu_progress,
        "6": menu_schedule,
        "7": menu_contrast,
        "8": menu_reset,
        "9": menu_pause,
    }
    while True:
        header()
        choice = input("\n  Ton choix : ").strip()
        if choice == "0":
            clear()
            print("  À bientôt !\n")
            sys.exit(0)
        action = actions.get(choice)
        if action:
            action()
        else:
            print("  ✗ Option invalide.")

if __name__ == "__main__":
    main()