from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_service_runs_the_installed_command():
    unit = (ROOT / "deploy/slowmovie.service").read_text()
    assert "ExecStart=/home/admin/.SlowMovie/bin/slowmovie run" in unit
    assert "ExecStartPre=/bin/sleep 10" in unit
    assert "Restart=always" in unit and "User=admin" in unit
    assert "SLOWMOVIE_HOME=/home/admin/SlowMovie" in unit


def test_relay_dockerfile_runs_unprivileged_relay():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER slowmovie" in dockerfile and ".[relay]" in dockerfile
    assert "8077" in dockerfile and "slowmovie-relay" in dockerfile
    assert "SLOWMOVIE_RELAY_TRUST_PROXY=1" in dockerfile


def test_dockerignore_keeps_secrets_and_tests_out_of_the_image():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for name in (".git", ".venv", "tests", ".superpowers"):
        assert name in ignored


def test_relay_env_example_lists_required_secrets():
    env = (ROOT / "deploy/relay/relay.env.example").read_text()
    for name in ("SLOWMOVIE_RELAY_PASSWORD_HASH", "SLOWMOVIE_RELAY_DEVICE_TOKEN"):
        assert name in env


def test_samba_share_points_to_videos():
    smb = (ROOT / "deploy/smb.conf.snippet").read_text()
    assert "[SlowMovie]" in smb and "/home/admin/SlowMovie/Videos" in smb


def test_install_script_is_executable_and_strict():
    script = ROOT / "deploy/install-pi.sh"
    assert script.stat().st_mode & 0o111
    assert "set -euo pipefail" in script.read_text()


def test_install_script_is_valid_bash():
    import subprocess
    result = subprocess.run(["bash", "-n", str(ROOT / "deploy/install-pi.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_script_follows_the_validated_zero_2_w_recipe():
    script = (ROOT / "deploy/install-pi.sh").read_text()
    for step in (
        "CONF_SWAPSIZE=512",                                   # swap : critique sur le Zero 2 W
        "python3 -m venv --system-site-packages",              # même venv que la recette
        "/home/admin/.SlowMovie",
        "waveshareteam/e-Paper",                               # waveshare-epd installé à la main
        "install --no-deps \"git+https://github.com/robweber/omni-epd.git",   # omni-epd sans ses dépendances
        "robweber/omni-epd",
        "gpiodevice smbus2 spidev numpy",
        "import omni_epd",                                     # vérification
        "do_spi 0",                                            # SPI
        "/usr/local/bin/slowmovie",                            # `ssh admin@... slowmovie` sans PATH du venv
        "fonts-dejavu-core",
        "slowmovie.conf.example",
    ):
        assert step in script, step
    assert ".[epd]" not in script and "[video,tui,epd]" not in script


def test_example_settings_reproduce_the_old_service_options():
    from slowmovie_inkdrop.config import Settings
    s = Settings.load(ROOT / "deploy/slowmovie.conf.example")
    assert (s.delay, s.increment, s.fullscreen) == (40, 4, True)
    assert s.file == "" and s.start == 0          # le film et la frame 336 dépendaient de l'ancienne installation


def test_install_script_keeps_rpi_swap_on_trixie_instead_of_replacing_it():
    script = (ROOT / "deploy/install-pi.sh").read_text()
    assert "dpkg -s rpi-swap" in script                    # rpi-swap (zram) entre en conflit avec dphys-swapfile
    check = script.index("dpkg -s rpi-swap")
    assert check < script.index("apt-get install -y dphys-swapfile")


def test_install_script_does_not_block_when_run_without_a_terminal():
    script = (ROOT / "deploy/install-pi.sh").read_text()
    assert "[ -t 0 ]" in script and "sudo smbpasswd -a admin" in script


def test_install_script_downloads_to_disk_not_to_the_ram_backed_tmp():
    # /tmp est un tmpfs de ~200 Mo sur un Zero 2 W : l'archive waveshare n'y tient pas
    script = (ROOT / "deploy/install-pi.sh").read_text()
    assert 'mktemp -d -p "$HOME"' in script
    assert "mktemp -d)" not in script


def test_relay_env_example_documents_the_admin_variables():
    env = (ROOT / "deploy/relay/relay.env.example").read_text()
    assert "SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH" in env and "SLOWMOVIE_RELAY_SESSION_SECRET" in env


def test_update_script_upgrades_the_package_and_restarts_the_service():
    import subprocess
    path = ROOT / "deploy/update-pi.sh"
    assert path.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    script = path.read_text()
    assert "set -euo pipefail" in script and "git pull --ff-only" in script
    assert "install --upgrade" in script and "systemctl restart slowmovie" in script
    assert "/home/admin/.SlowMovie" in script


def test_deployment_guide_covers_the_admin_tab_and_the_update():
    guide = (ROOT / "docs/deploiement.md").read_text()
    for expected in ("SLOWMOVIE_RELAY_ADMIN_PASSWORD_HASH", "update-pi.sh", "Réglages", "/envoi/"):
        assert expected in guide, expected
