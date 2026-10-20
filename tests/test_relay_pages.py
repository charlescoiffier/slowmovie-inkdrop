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


def test_the_main_page_has_two_tabs_and_the_admin_hooks(client):
    html = client.get("/").text
    for expected in ('role="tablist"', 'id="tab-send"', 'id="tab-admin"', 'role="tabpanel"',
                     "/api/admin/login", "/api/admin/state", "/api/admin/commands", "X-Requested-With",
                     'name="caption"', 'location.assign("/envoi/"'):
        assert expected in html, expected


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
    for expected in ("/api/status/", "location.pathname", "Envoyer une autre image", "introuvable"):
        assert expected in html, expected


def test_the_admin_tab_is_hidden_until_the_relay_says_it_is_enabled(client):
    html = client.get("/").text
    assert re.search(r'id="tab-admin"[^>]*hidden', html)
    assert "c.admin" in html


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
