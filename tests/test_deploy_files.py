from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_service_runs_the_installed_command():
    unit = (ROOT / "deploy/slowmovie.service").read_text()
    assert "ExecStart=/home/admin/SlowMovie/.venv/bin/slowmovie run" in unit
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
