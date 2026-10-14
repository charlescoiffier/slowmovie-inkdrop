# slowmovie-inkdrop

> Ce projet a été vibecodé avec l'aide de l'intelligence artificielle.

Évolution de [SlowMovie](https://github.com/TomWhitwell/SlowMovie) (un film joué image par image sur un écran e-paper,
Raspberry Pi) avec trois apports :

1. **Envoi d'images à distance** : depuis un téléphone ou un ordinateur, on envoie une image et une durée. Elle remplace
   le film à l'écran pendant ce temps (le film est en pause), puis le film reprend là où il s'était arrêté. Les envois
   forment une file d'attente, protégée par un mot de passe partagé.
2. **Interface terminal** (TUI Textual, au clavier, thème « galaxy » comme [cadav](https://github.com/charlescoiffier/cadav)).
3. **Ajout de films simplifié** : glisser-déposer dans un partage réseau, ou `slowmovie add film.mp4`.

## Architecture

```
téléphone ──HTTPS──▶ relais (VPS, Docker) ◀──HTTPS sortant── lecteur (Pi) ──▶ écran e-paper
                       page d'envoi, file                      FILM ↔ IMAGE        ▲
                                                                  ▲                │
                                          TUI / CLI ──socket──────┘          seul à toucher l'écran
```

Le Pi n'ouvre aucun port : c'est lui qui interroge le relais.

## Installation

Voir [docs/deploiement.md](docs/deploiement.md) (relais, Pi, Mac, sécurité, recette sur le matériel).

Développement :

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[video,tui,relay,dev]'
.venv/bin/pytest -q && .venv/bin/ruff check .
```

Les tests utilisent un écran factice (`SLOWMOVIE_FAKE_DISPLAY=<dossier>` pour lancer le service sans matériel).

## Divers

- `legacy/` : les trois scripts d'origine (`slowmovie.py`, `slowmovie_config.py`, `slowmovie_plus.py`), pour référence.
- Spécification et plan : `docs/superpowers/`.
- **Licence** : `legacy/slowmovie.py` et `framesource.py` dérivent de SlowMovie de Tom Whitwell ; la licence de ce dépôt
  reste à fixer après vérification de celle de l'amont.
