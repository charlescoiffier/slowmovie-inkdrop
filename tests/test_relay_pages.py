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


def test_the_page_starts_on_a_login_screen_with_one_password_field(client):
    html = client.get("/").text
    for expected in ('id="login"', 'id="login-password"', 'id="login-eye"', 'id="login-remember"', "Se souvenir de moi",
                     'id="logout"', 'type="password"'):
        assert expected in html, expected
    assert html.count('type="password"') == 2          # connexion + administrateur : plus de champ dans les modes ni les envois
    for gone in ('id="mode-password"', 'id="pi-password"', 'name="password"'):
        assert gone not in html, gone


def test_the_main_page_has_the_mode_and_send_tabs_with_their_sub_tabs(client):
    html = client.get("/").text
    for expected in ('role="tablist"', 'id="tab-mode"', 'id="tab-send"', 'id="panel-mode"', 'id="panel-send"',
                     'id="sub-slowmovie"', 'id="sub-pipiquant"', 'id="sub-photos"', 'id="sub-image"', 'id="sub-pipi"',
                     'role="tabpanel"', 'name="caption"', 'id="pi-title"', 'location.assign("/envoi/"',
                     "/api/send", "/api/send-pipiquant", "c.pipiquant", 'id="home"'):
        assert expected in html, expected


def test_the_send_durations_are_pills(client):
    html = client.get("/").text
    for expected in ('id="duration-pills"', 'id="pi-duration-pills"', 'name="duration" type="hidden"', "durationPills("):
        assert expected in html, expected
    assert "<select" not in html


def test_the_mode_actions_use_the_general_password_routes(client):
    html = client.get("/").text
    for expected in ("/api/mode/", '"state"', '"rhythm"', '"set"', '"title"', '"current"', '"command"', "data-seconds",
                     "activate-", "gear-", "mode-title", "X-Requested-With", "generalPassword", "nowCard(", "slideCard(",
                     "filmCard(", "Réglages en attente"):
        assert expected in html, expected


def test_settings_open_in_a_modal_with_a_close_button(client):
    html = client.get("/").text
    for expected in ('id="modal"', 'role="dialog"', 'aria-modal="true"', 'id="modal-close"', "Fermer", '"Escape"',
                     'id="panel-admin"', 'id="admin-login"', 'id="admin-password"', 'id="admin-eye"',
                     'id="admin-login-button"', 'id="admin-main"', 'id="gear-send"', "/api/admin/login",
                     "/api/admin/state", "/api/admin/commands", "openAdmin(", "PIPIQUANT_SETTINGS", "PHOTOS_SETTINGS",
                     "SEND_SETTINGS", "relay_poll_seconds", "for_mode", "photos_album_url"):
        assert expected in html, expected
    assert 'id="admin-back"' not in html and "← Modes" not in html


def test_the_relay_poll_setting_lives_with_the_send_settings_only(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    send_block = scripts[scripts.index("const SEND_SETTINGS"):scripts.index("const MODE_SETTINGS")]
    other = scripts.replace(send_block, "")
    assert "relay_poll_seconds" in send_block
    assert "relay_poll_seconds" not in other.split("const SLOWMOVIE_SETTINGS")[1].split("const MODE_SETTINGS")[0]


def test_the_album_link_is_never_prefilled(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert "photos_album_url" in scripts
    for forbidden in ('album-link").value =', "values.photos_album_url", "settings.photos_album_url", "drafts.photos.photos_album_url"):
        assert forbidden not in scripts, forbidden
    assert "lien configuré" in scripts


def test_the_page_is_compact_and_works_on_a_phone(client):
    html = client.get("/").text
    assert "width=device-width" in html and "prefers-color-scheme: dark" in html and "#db4c3f" in html
    assert "position:fixed" in html                           # barre du bas et fenêtre modale


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


def test_the_mode_polling_starts_after_a_login_and_stops_at_the_first_refusal(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    for expected in ("r.status === 401 || (r.status === 429", "logout(r.body.detail", "startPolling()", "stopPolling()",
                     "pi_outdated", "actionSeq", "if (!generalPassword) return null"):
        assert expected in scripts, expected
    assert scripts.index("startPolling();") > scripts.index("async function login(")          # jamais avant une connexion


def test_the_header_title_goes_back_to_the_main_screen(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert '$("home").addEventListener("click"' in scripts and 'selectMain($("tab-mode"))' in scripts


def test_only_a_password_refusal_logs_out_and_logout_ends_the_admin_session(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert 'includes("essais")' in scripts                                  # une file de commandes pleine ne déconnecte pas
    logout = scripts[scripts.index("function logout("):scripts.index('$("login-form")')]
    assert "/api/admin/logout" in logout
    assert "loginStatus === 401" in scripts                               # une panne ne fait pas oublier le mot de passe
    assert "clear-draft" in scripts and "/api/admin/draft/clear" in scripts


def test_the_modal_refresh_never_overwrites_what_is_being_typed(client):
    html = client.get("/").text
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert "refresh(true)" in scripts                                      # après une action
    body = scripts[scripts.index("function renderCurrentInModal"):scripts.index("function renderCurrentInModal") + 200]
    assert "refresh(false)" in body and "refresh(true)" not in body
