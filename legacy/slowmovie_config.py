#!/usr/bin/env python3
"""
SlowMovie Remote Configuration Tool
Interface CLI interactive avec navigation par flèches
Mac → Raspberry Pi via SSH
"""

import os
import sys
import argparse
import subprocess
import tempfile
import tty
import termios


class RemoteSlowMovieConfig:
    def __init__(self, host, user='admin', port=22, slowmovie_path='/home/admin/SlowMovie'):
        self.host = host
        self.user = user
        self.port = port
        self.slowmovie_path = slowmovie_path
        self.config_file = f"{slowmovie_path}/slowmovie.conf"
        self.ssh_base = f"{user}@{host}"
        
        self.defaults = {
            'file': '',
            'directory': 'Videos',
            'loop': 'False',
            'random-file': 'False',
            'random-frames': 'False',
            'delay': '60',
            'increment': '6',
            'start': '0',
            'fullscreen': 'False',
            'subtitles': 'False',
            'timecode': 'False',
            'epd': 'waveshare_epd.epd7in5_V2',
            'contrast': '1.0',
            'clear': 'False',
            'loglevel': 'INFO'
        }
    
    def test_connection(self):
        try:
            result = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5', '-p', str(self.port), 
                 self.ssh_base, 'echo "OK"'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except:
            return False
    
    def ssh_command(self, command):
        try:
            result = subprocess.run(
                ['ssh', '-p', str(self.port), self.ssh_base, command],
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0, result.stdout, result.stderr
        except Exception as e:
            return False, "", str(e)
    
    def download_config(self):
        temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.conf')
        temp_path = temp_file.name
        temp_file.close()
        
        try:
            result = subprocess.run(
                ['scp', '-P', str(self.port), 
                 f"{self.ssh_base}:{self.config_file}", temp_path],
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                with open(temp_path, 'r') as f:
                    config_content = f.read()
                os.unlink(temp_path)
                return True, config_content
            else:
                os.unlink(temp_path)
                return False, "Fichier introuvable"
        except Exception as e:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False, str(e)
    
    def upload_config(self, config_content):
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf')
        temp_file.write(config_content)
        temp_file.close()
        temp_path = temp_file.name
        
        try:
            result = subprocess.run(
                ['scp', '-P', str(self.port), temp_path,
                 f"{self.ssh_base}:{self.config_file}"],
                capture_output=True,
                timeout=30
            )
            os.unlink(temp_path)
            return result.returncode == 0
        except:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False
    
    def parse_config(self, config_content):
        config = {}
        for line in config_content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
        return config
    
    def generate_config(self, config_dict):
        lines = ["# SlowMovie Configuration", ""]
        for key, value in config_dict.items():
            lines.append(f"{key}={value}")
        return '\n'.join(lines)
    
    def list_videos(self):
        success, stdout, _ = self.ssh_command(
            f"find {self.slowmovie_path}/Videos -type f \\( -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.m4v' \\) -exec basename {{}} \\;"
        )
        if success:
            return [v.strip() for v in stdout.split('\n') if v.strip()]
        return []
    
    def get_service_status(self):
        success, stdout, _ = self.ssh_command(
            "systemctl is-active slowmovie 2>/dev/null || echo 'inactive'"
        )
        return stdout.strip() if success else "unknown"
    
    def restart_service(self):
        success, _, _ = self.ssh_command("sudo systemctl restart slowmovie")
        return success
    
    def stop_service(self):
        success, _, _ = self.ssh_command("sudo systemctl stop slowmovie")
        return success
    
    def start_service(self):
        success, _, _ = self.ssh_command("sudo systemctl start slowmovie")
        return success


class InteractiveMenu:
    def __init__(self):
        self.selected = 0
    
    def get_key(self):
        """Lit une touche du clavier sans attendre Entrée"""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            # Gestion des séquences d'échappement (flèches)
            if ch == '\x1b':
                ch = sys.stdin.read(2)
                if ch == '[A':
                    return 'up'
                elif ch == '[B':
                    return 'down'
                elif ch == '[C':
                    return 'right'
                elif ch == '[D':
                    return 'left'
            elif ch == '\r' or ch == '\n':
                return 'enter'
            elif ch == '\x03':  # Ctrl+C
                return 'quit'
            elif ch == 'q' or ch == 'Q':
                return 'quit'
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    
    def clear_screen(self):
        os.system('clear')
    
    def show_menu(self, title, options, selected=0):
        """Affiche un menu avec navigation par flèches"""
        self.selected = selected
        
        while True:
            self.clear_screen()
            print(f"\n{title}\n")
            
            for i, option in enumerate(options):
                if i == self.selected:
                    print(f"  → \033[7m{option}\033[0m")  # Inversé
                else:
                    print(f"    {option}")
            
            print("\n\033[90m[↑↓] Navigation  [Entrée] Valider  [Q] Retour\033[0m")
            
            key = self.get_key()
            
            if key == 'up':
                self.selected = (self.selected - 1) % len(options)
            elif key == 'down':
                self.selected = (self.selected + 1) % len(options)
            elif key == 'enter':
                return self.selected
            elif key == 'quit':
                return -1
    
    def input_text(self, prompt, default=""):
        """Demande une saisie de texte"""
        self.clear_screen()
        print(f"\n{prompt}")
        if default:
            print(f"Valeur actuelle: {default}")
        print("\n(Entrée vide pour annuler)")
        
        # Rétablir le mode normal pour la saisie
        value = input("\n> ").strip()
        return value if value else None
    
    def show_message(self, message, wait=True):
        """Affiche un message"""
        self.clear_screen()
        print(f"\n{message}\n")
        if wait:
            print("\033[90m[Appuyez sur une touche pour continuer]\033[0m")
            self.get_key()


def interactive_mode(remote):
    menu = InteractiveMenu()
    
    menu.clear_screen()
    print("\n╔═══════════════════════════════════════════╗")
    print("║  SlowMovie Remote Configuration          ║")
    print("║  Mac → Raspberry Pi via SSH               ║")
    print("╚═══════════════════════════════════════════╝\n")
    
    print(f"🔗 Connexion à {remote.host}...")
    if not remote.test_connection():
        print("\n❌ Impossible de se connecter au Raspberry Pi")
        print("\nAppuyez sur une touche...")
        menu.get_key()
        return
    
    print("✓ Connexion établie")
    print("\nChargement de la configuration...")
    
    success, config_content = remote.download_config()
    if not success:
        config = remote.defaults.copy()
    else:
        config = remote.parse_config(config_content)
        for key, value in remote.defaults.items():
            if key not in config:
                config[key] = value
    
    print("✓ Configuration chargée\n")
    print("Appuyez sur une touche pour continuer...")
    menu.get_key()
    
    modified = False
    
    while True:
        status = remote.get_service_status()
        status_text = f"🟢 Actif" if status == "active" else f"🔴 Inactif"
        
        main_options = [
            f"📊 Configuration actuelle (Service: {status_text})",
            "🎬 Choisir un film",
            "⏱️  Configurer le timing",
            "🖼️  Configurer l'affichage",
            "⚙️  Gérer le service",
            "💾 Sauvegarder et redémarrer",
            "💾 Sauvegarder sans redémarrer",
            "❌ Quitter sans sauvegarder"
        ]
        
        choice = menu.show_menu(
            "╔═══════════════════════════════════════════╗\n"
            "║         SlowMovie Configuration           ║\n"
            f"║         {remote.host:^33s} ║\n"
            "╚═══════════════════════════════════════════╝",
            main_options
        )
        
        if choice == -1 or choice == 7:
            if modified:
                confirm = menu.show_menu(
                    "⚠️  Modifications non sauvegardées",
                    ["Quitter quand même", "Annuler"]
                )
                if confirm == 0:
                    break
            else:
                break
        
        elif choice == 0:  # Afficher config
            info = f"""
╔═══════════════════════════════════════════╗
║  Configuration SlowMovie                  ║
╚═══════════════════════════════════════════╝

📁 Vidéo et lecture:
  Fichier: {config.get('file', 'Auto')}
  Répertoire: {config.get('directory', 'Videos')}
  Lecture en boucle: {config.get('loop', 'False')}
  Ordre aléatoire: {config.get('random-file', 'False')}

⏱️  Timing et frames:
  Délai: {config.get('delay', '120')}s
  Frames à sauter: {config.get('increment', '4')}
  Frame de départ: {config.get('start', '0')}
  Frames aléatoires: {config.get('random-frames', 'False')}

🖼️  Affichage:
  Plein écran: {config.get('fullscreen', 'False')}
  Contraste: {config.get('contrast', '1.0')}
  Sous-titres: {config.get('subtitles', 'False')}
  Timecode: {config.get('timecode', 'False')}

🖥️  Matériel:
  Driver EPD: {config.get('epd', 'waveshare_epd.epd7in5_V2')}
"""
            menu.show_message(info)
        
        elif choice == 1:  # Choisir film
            videos = remote.list_videos()
            if not videos:
                menu.show_message("⚠️  Aucune vidéo trouvée sur le Pi")
                continue
            
            video_options = ["🔄 Mode automatique (premier du dossier)"] + [f"🎬 {v}" for v in sorted(videos)]
            
            current_file = config.get('file', '')
            default_selected = 0
            if current_file:
                try:
                    default_selected = sorted(videos).index(current_file) + 1
                except ValueError:
                    pass
            
            selected = menu.show_menu(
                f"📹 Vidéos disponibles ({len(videos)})",
                video_options,
                default_selected
            )
            
            if selected >= 0:
                if selected == 0:
                    config['file'] = ''
                else:
                    config['file'] = sorted(videos)[selected - 1]
                modified = True
                menu.show_message(f"✓ Film sélectionné: {config['file'] if config['file'] else 'Mode auto'}", wait=False)
        
        elif choice == 2:  # Timing
            timing_options = [
                f"⏱️  Délai de rafraîchissement: {config.get('delay', '60')}s (actuellement: {config.get('delay', '60')}s)",
                f"🎬 Frames à sauter: {config.get('increment', '6')} (actuellement: {config.get('increment', '6')})",
                f"▶️  Frame de départ: {config.get('start', '0')} (actuellement: {config.get('start', '0')})",
                f"🎲 Frames aléatoires: {config.get('random-frames', 'False')} (actuellement: {config.get('random-frames', 'False')})",
                "← Retour"
            ]
            
            timing_choice = menu.show_menu("⏱️  Configuration du Timing", timing_options)
            
            if timing_choice == 0:  # Délai
                value = menu.input_text(
                    f"⏱️  Délai de rafraîchissement (en secondes)\nActuel: {config.get('delay', '60')}s",
                    config.get('delay', '60')
                )
                if value and value.isdigit():
                    config['delay'] = value
                    modified = True
                    menu.show_message(f"✓ Délai défini: {value}s (précédent: {config.get('delay', '60')}s)")
            
            elif timing_choice == 1:  # Frames
                value = menu.input_text(
                    f"🎬 Nombre de frames à sauter\nActuel: {config.get('increment', '6')}",
                    config.get('increment', '6')
                )
                if value and value.isdigit():
                    config['increment'] = value
                    modified = True
                    menu.show_message(f"✓ Frames à sauter: {value} (précédent: {config.get('increment', '6')})")
            
            elif timing_choice == 2:  # Start frame
                value = menu.input_text(
                    f"▶️  Frame de départ (0 = début)\nActuel: {config.get('start', '0')}",
                    config.get('start', '0')
                )
                if value and value.isdigit():
                    config['start'] = value
                    modified = True
                    menu.show_message(f"✓ Frame de départ: {value} (précédent: {config.get('start', '0')})")
            
            elif timing_choice == 3:  # Random frames
                current = config.get('random-frames', 'False') == 'True'
                config['random-frames'] = str(not current)
                modified = True
                menu.show_message(f"✓ Frames aléatoires: {'Activées' if not current else 'Désactivées'}")
        
        elif choice == 3:  # Affichage
            display_options = [
                f"🎨 Contraste: {config.get('contrast', '1.0')} (actuellement: {config.get('contrast', '1.0')})",
                f"🖼️  Plein écran: {config.get('fullscreen', 'False')} (actuellement: {config.get('fullscreen', 'False')})",
                f"📝 Sous-titres: {config.get('subtitles', 'False')} (actuellement: {config.get('subtitles', 'False')})",
                f"🕐 Timecode: {config.get('timecode', 'False')} (actuellement: {config.get('timecode', 'False')})",
                f"🔄 Lecture en boucle: {config.get('loop', 'False')} (actuellement: {config.get('loop', 'False')})",
                "← Retour"
            ]
            
            display_choice = menu.show_menu("🖼️  Configuration de l'Affichage", display_options)
            
            if display_choice == 0:  # Contraste
                current_contrast = config.get('contrast', '1.0')
                value = menu.input_text(
                    f"🎨 Contraste (0.5 à 2.0, normal = 1.0)\nActuel: {current_contrast}",
                    current_contrast
                )
                if value:
                    try:
                        val = float(value)
                        if 0.5 <= val <= 2.0:
                            config['contrast'] = str(val)
                            modified = True
                            menu.show_message(f"✓ Contraste défini: {val} (précédent: {current_contrast})")
                        else:
                            menu.show_message("⚠️  Valeur hors limites (0.5-2.0)")
                    except ValueError:
                        menu.show_message("⚠️  Valeur invalide")
            
            elif display_choice in [1, 2, 3, 4]:  # Toggles
                keys = ['fullscreen', 'subtitles', 'timecode', 'loop']
                labels = ['Plein écran', 'Sous-titres', 'Timecode', 'Lecture en boucle']
                key = keys[display_choice - 1]
                label = labels[display_choice - 1]
                
                current = config.get(key, 'False') == 'True'
                previous_value = 'Activé' if current else 'Désactivé'
                config[key] = str(not current)
                new_value = 'Activé' if not current else 'Désactivé'
                modified = True
                menu.show_message(f"✓ {label}: {new_value} (précédent: {previous_value})")
        
        elif choice == 4:  # Service
            service_options = [
                "▶️  Démarrer le service",
                "⏸️  Arrêter le service",
                "🔄 Redémarrer le service",
                "← Retour"
            ]
            
            service_choice = menu.show_menu(
                f"⚙️  Gestion du service (Statut: {status})",
                service_options
            )
            
            if service_choice == 0:
                menu.show_message("⏳ Démarrage du service...", wait=False)
                if remote.start_service():
                    menu.show_message("✓ Service démarré")
                else:
                    menu.show_message("❌ Erreur lors du démarrage")
            
            elif service_choice == 1:
                menu.show_message("⏳ Arrêt du service...", wait=False)
                if remote.stop_service():
                    menu.show_message("✓ Service arrêté")
                else:
                    menu.show_message("❌ Erreur lors de l'arrêt")
            
            elif service_choice == 2:
                menu.show_message("⏳ Redémarrage du service...", wait=False)
                if remote.restart_service():
                    menu.show_message("✓ Service redémarré")
                else:
                    menu.show_message("❌ Erreur lors du redémarrage")
        
        elif choice == 5 or choice == 6:  # Sauvegarder
            config_content = remote.generate_config(config)
            menu.show_message("📤 Envoi de la configuration...", wait=False)
            
            if remote.upload_config(config_content):
                if choice == 5:
                    menu.show_message("⏳ Redémarrage du service...", wait=False)
                    if remote.restart_service():
                        menu.show_message("✓ Configuration sauvegardée et service redémarré!")
                    else:
                        menu.show_message("⚠️  Configuration sauvegardée mais erreur au redémarrage")
                else:
                    menu.show_message("✓ Configuration sauvegardée (service non redémarré)")
                modified = False
            else:
                menu.show_message("❌ Erreur lors de l'envoi de la configuration")


def main():
    parser = argparse.ArgumentParser(
        description='Configuration à distance de SlowMovie avec interface interactive',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  %(prog)s 192.168.1.100
  %(prog)s pi@raspberrypi.local
  %(prog)s 192.168.1.100 --user pi
        """
    )
    
    parser.add_argument('host', nargs='?',
                       help='Adresse du Raspberry Pi')
    parser.add_argument('--user', default='admin',
                       help='Utilisateur SSH')
    parser.add_argument('--port', type=int, default=22,
                       help='Port SSH')
    parser.add_argument('--path', default='/home/admin/SlowMovie',
                       help='Chemin SlowMovie sur le Pi')
    
    args = parser.parse_args()
    
    if not args.host:
        print("❌ Erreur: Spécifiez l'adresse du Raspberry Pi")
        print("\nExemple: python3 slowmovie_config.py 192.168.1.100")
        sys.exit(1)
    
    if '@' in args.host:
        args.user, args.host = args.host.split('@')
    
    remote = RemoteSlowMovieConfig(args.host, args.user, args.port, args.path)
    
    interactive_mode(remote)


if __name__ == '__main__':
    main()