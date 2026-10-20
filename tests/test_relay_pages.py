import re

import pytest
from fastapi.testclient import TestClient

from slowmovie_inkdrop.relay.app import create_app
from slowmovie_inkdrop.relay.config import RelayConfig
from slowmovie_inkdrop.relay.security import hash_password


@pytest.fixture
def client(tmp_path):
    config = RelayConfig(data_dir=tmp_path, password_hash=hash_password("x"), device_token="t" * 40,
                         admin_password_hash=hash_password("y"))
    return TestClient(create_app(config), base_url="https://testserver")


def test_the_main_page_has_the_mode_and_send_tabs_with_their_sub_tabs(client):
    html = client.get("/").text
    for expected in ('role="tablist"', 'id="tab-mode"', 'id="tab-send"', 'id="panel-mode"', 'id="panel-send"',
                     'id="sub-slowmovie"', 'id="sub-pipiquant"', 'id="sub-photos"', 'id="sub-image"', 'id="sub-pipi"',
                     'role="tabpanel"', 'name="caption"', 'id="pi-title"', 'location.assign("/envoi/"',
                     "/api/send", "/api/send-pipiquant", "c.pipiquant"):
        assert expected in html, expected


def test_the_mode_actions_use_the_general_password_routes(client):
    html = client.get("/").text
    for expected in ("/api/mode/", '"state"', '"rhythm"', '"set"', '"title"', "data-seconds",
                     "activate-", "gear-", "mode-password", "mode-title", "X-Requested-With"):
        assert expected in html, expected


def test_each_mode_has_a_settings_button_behind_the_admin_password(client):
    html = client.get("/").text
    for expected in ('id="panel-admin"', 'id="admin-login"', 'id="admin-password"', 'id="admin-login-button"',
                     'id="admin-main"', 'id="admin-back"', "/api/admin/login", "/api/admin/state",
                     "/api/admin/commands", "openAdmin(source)", "PIPIQUANT_SETTINGS", "PHOTOS_SETTINGS",
                     "pipiquant_interval", "photos_interval", "photos_album_url"):
        assert expected in html, expected
    assert 'id="tab-admin"' not in html                      # l'ancien onglet Réglages a disparu


def test_the_album_link_is_never_prefilled(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert "photos_album_url" in scripts
    for forbidden in ('album-link").value =', "values.photos_album_url", "settings.photos_album_url"):
        assert forbidden not in scripts, forbidden
    assert "lien configuré" in scripts


def test_the_page_is_compact_and_works_on_a_phone(client):
    html = client.get("/").text
    assert "width=device-width" in html and "prefers-color-scheme: dark" in html and "#db4c3f" in html
    assert "position:fixed" in html                           # barre du bas


@pytest.mark.parametrize("path", ["/", "/envoi/" + "a" * 32, "/envoi/nimporte-quoi"])
def test_pages_never_inject_data_as_html(client, path):
    html = client.get(path).text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert scripts, "aucun script trouvé : le test ne vérifierait rien"
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in scripts, forbidden


def test_the_confirmation_page_follows_the_upload(client):
    for ident in ("a" * 32, "0" * 32, "pas-un-identifiant"):
        r = client.get("/envoi/" + ident)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = client.get("/envoi/" + "a" * 32).text
    for expected in ("/api/status/", "location.pathname", "Retour à l'accueil", "introuvable"):
        assert expected in html, expected


def test_the_settings_buttons_follow_the_relay_admin_flag(client):
    html = client.get("/").text
    assert "c.admin" in html and "hidden: !adminEnabled" in html


def test_the_admin_refresh_does_not_wipe_what_the_user_is_typing(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert 'id="admin-status"' in html                     # la barre d'état se rafraîchit seule
    assert "dirty" in scripts and "document.activeElement" in scripts


def test_the_admin_page_can_set_the_end_point(client):
    html = client.get("/").text
    assert "set_end" in html and "Régler la fin" in html


def test_film_states_are_shown_in_french_not_as_raw_codes(client):
    html = client.get("/").text
    for expected in ('ready: "prêt"', 'pending: "copie en cours…"', 'error: "illisible"'):
        assert expected in html, expected
