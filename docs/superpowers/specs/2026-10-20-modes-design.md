# Modes de défilement (SlowMovie, π-piquants, photos) et interface compacte — design

Date : 2026-10-20 · Statut : à relire · Complète [2026-10-14-slowmovie-inkdrop-design.md](2026-10-14-slowmovie-inkdrop-design.md) et [2026-10-20-pilotage-web-design.md](2026-10-20-pilotage-web-design.md)

## 1. Objectif et raison d'être

L'écran e-paper ne montre aujourd'hui qu'un film, interrompu par les images envoyées. L'utilisateur veut pouvoir en faire aussi un cadre qui change tout seul : soit avec des π-piquants tirés au hasard, soit avec des photos tirées au hasard d'un album Google Photos qu'il désigne. Il veut basculer d'un mode à l'autre depuis son téléphone, régler le rythme de rafraîchissement, et continuer à envoyer ponctuellement une image ou un π-piquant quel que soit le mode.

L'écran est partiellement masqué en bas par une marquise en carton : le texte reste petit et laisse une marge basse (déjà en place, voir § 9).

Réussite : depuis un téléphone, en quelques secondes et sans SSH, l'utilisateur passe de SlowMovie à « π-piquants » ou « Photos », règle le rythme, et l'écran suit au plus tard à la prochaine interrogation du Pi (≈ 20 s). Les envois ponctuels fonctionnent dans les trois modes.

## 2. Décisions de l'utilisateur

- **Trois modes, un seul actif à la fois** : SlowMovie, π-piquants au hasard, photos d'un album Google Photos au hasard.
- **Rythme de rafraîchissement à choisir** pour chaque mode. Plages voulues : SlowMovie, de quelques dizaines de secondes à quelques minutes ; π-piquants et photos, de quelques minutes à plusieurs heures.
- **« Envoyer une image » et « Envoyer un π-piquant » existent quel que soit le mode** : ils interrompent le défilement, qui reprend ensuite.
- **Photos : un album précis, désigné par son lien de partage** (méthode non officielle, choisie en connaissance de ses limites, § 5).
- **Légende des π-piquants : 14 px**, avec la case « afficher le titre » déjà en place.
- **Interface** (maquette A validée, § 7) : un onglet « Mode » (mot de passe général) avec trois sous-onglets ; un onglet « Envoyer » (mot de passe général) avec deux sous-onglets ; les paramètres de chaque mode derrière un bouton ⚙ dans son sous-onglet (mot de passe administrateur) ; le rythme est réglable aux **deux** endroits, avec chacun son niveau d'accès ; style compact, dans l'esprit de Todoist.

## 3. Hors périmètre

Sélecteur officiel de Google (Picker) ou tout accès OAuth à Google ; plusieurs albums ou une photothèque entière ; choix photo par photo ; suppression ou modification de photos ; plusieurs modes actifs en même temps ; programmation horaire des modes (la plage horaire existante `active_hours` s'applique à tous les modes comme aujourd'hui) ; changement du comportement de SlowMovie.

## 4. Architecture

Le Pi n'ouvre toujours aucun port et reste la source de vérité de ses réglages (`slowmovie.conf`). **La passerelle prépare les images** de défilement ; le Pi les demande.

**Mode actif et rythmes** : nouveaux réglages du Pi, modifiables à distance (liste blanche étendue) : `mode` (`slowmovie` | `pipiquant` | `photos`, défaut `slowmovie`), `pipiquant_interval` et `photos_interval` (secondes), `pipiquant_title` (booléen, défaut vrai), `photos_album_url` (texte). Le rythme de SlowMovie est le réglage existant `delay`. Valeurs et bornes validées par le Pi (`Settings.problems()`), qui ne fait pas confiance à la passerelle.

**État du lecteur** : s'ajoute à FILM et IMAGE un état **DIAPORAMA**, actif dans les modes `pipiquant` et `photos`. Dans cet état, à chaque échéance du rythme, le Pi demande la diapositive suivante à la passerelle (`GET /api/device/slide?source=pipiquant|photos&…`, jeton d'appareil). La réponse est un PNG plus ses métadonnées (légende éventuelle). Le Pi l'affiche avec le même traitement que les images envoyées (réduction à la taille de l'écran, noir et blanc, contraste, légende). L'image qui reste à l'écran est conservée jusqu'à l'échéance suivante.

**Interruptions** : une image envoyée (file existante) a priorité sur le diaporama comme sur le film. À la fin de son affichage, le diaporama reprend et tire une nouvelle diapositive ; le compte à rebours du rythme repart de zéro. Un changement de mode prend effet à la prochaine boucle du lecteur, pas au milieu d'une image envoyée. Les films gardent leur progression quand on quitte le mode SlowMovie et reprennent là où ils étaient en y revenant.

**Pannes** : si la passerelle ne peut pas fournir la diapositive (service π-piquant injoignable, album illisible, Pi hors ligne…), le Pi garde l'image affichée, signale l'erreur dans son état et dans la page, et réessaie à l'échéance suivante (jamais de boucle serrée).

**Commandes** : les nouveaux réglages passent par les commandes existantes (`settings`), avec une nouvelle commande `set_mode` pour la bascule. Même principe qu'aujourd'hui : liste fermée, validée par la passerelle (forme) et le Pi (tout), jamais rejouée, accusée.

**Deux niveaux d'accès sur les mêmes réglages** : la passerelle expose, avec le **mot de passe général** (le même que l'envoi d'images, avec la même limitation de débit et le même blocage après échecs), deux actions restreintes : changer le mode actif et changer le rythme d'un mode, parmi des valeurs prédéfinies. Elle les convertit en commandes pour le Pi. **La case « titre » du π-piquant suit la même règle que le rythme** : réglable aux deux endroits, donc une troisième action restreinte avec le mot de passe général (booléen seulement). Les lectures d'état et ces actions ont leur propre limiteur de débit (échecs de mot de passe bloquants, comme l'envoi), et **ne comptent pas dans la limite de 20 envois par heure**. Avec la **session administrateur** (cookie existant), tous les réglages des modes sont modifiables, dont les rythmes libres dans les bornes, la case « titre » et le lien de l'album. L'état courant du mode (mode actif, rythmes, prochaine échéance, dernière erreur) est lu avec le mot de passe général.

## 5. Photos : lecture de l'album par son lien de partage

- Le lien (`https://photos.app.goo.gl/…` ou `https://photos.google.com/share/…`) est saisi dans les réglages du mode Photos (administrateur). Il est validé strictement (schéma https, noms d'hôte autorisés, longueur jusqu'à 500 caractères) : jamais d'adresse arbitraire (pas de SSRF). **Le lien est un secret** (quiconque l'a voit l'album) et reste hors des journaux et des historiques : stocké sur le Pi ; transmis à la passerelle dans le **corps d'une requête POST** (jamais dans l'adresse, que les journaux d'accès enregistrent) ; **absent de l'instantané** renvoyé par le Pi (la passerelle n'y voit qu'un indicateur « lien configuré » et le nom d'hôte) ; dans l'historique des commandes de la passerelle, la valeur est **effacée dès que le Pi a accusé la commande** ; l'écran d'administration n'affiche jamais le lien, seulement « lien configuré » avec un bouton pour le remplacer (mes choix).
- La passerelle lit la page publique de l'album, en extrait la liste des photos, garde cette liste en mémoire un certain temps (de l'ordre de plusieurs heures), tire une photo au hasard et télécharge une version réduite (≈ la taille de l'écran) avec une taille maximale et un délai maximal. Les photos ne sont pas stockées durablement ; seules les adresses de la liste sont gardées.
- **Limite assumée** : ce n'est pas une méthode officielle de Google. Elle peut cesser de fonctionner si Google change sa page, et elle repose sur le fait que l'album est partagé par lien (quiconque a le lien le voit). Le nombre de photos que la page expose est **à vérifier avec un vrai album** (un lien de test sera demandé à l'utilisateur) ; si la page n'en expose qu'une partie, on tire dans cette partie et on l'indique. Quand l'album est illisible, l'écran garde sa dernière image et la page affiche « album illisible » (jamais une erreur technique brute).
- Pas de légende sur les photos.

## 6. π-piquants au hasard

Même tirage que l'onglet d'envoi : 1 à 200 décimales de π et un angle entier de 1° à 359°, via l'API du service π-piquant (clé déjà configurée, `SLOWMOVIE_RELAY_PIPIQUANT_API_KEY`), autres paramètres du dessin à leurs valeurs de base, image de 800 px sans légende dessinée par le service. La légende « π-piquant • 1 = 214° • 87 décimales » est écrite par le Pi si `pipiquant_title` est vrai. **Corps 14 px** pour les légendes de π-piquant, y compris pour « Envoyer un π-piquant » ; les légendes des images envoyées gardent le corps actuel (20 px). La marge basse `caption_margin` s'applique aux deux. Sans clé configurée, le mode π-piquants est indisponible (grisé), comme l'onglet d'envoi aujourd'hui.

## 7. Interface (maquette A, validée)

Une seule page, style compact « à la Todoist » : fond blanc, rouge discret comme couleur d'accent, texte petit, lignes fines, pastilles pour les choix, thème sombre respecté. Adaptée au téléphone d'abord (16 px de marge latérale, pas de défilement horizontal).

- **Barre du bas** : deux onglets, **Mode** et **Envoyer**.
- **Mode** : trois sous-onglets (SlowMovie, π-piquant, Photos). Chacun montre : l'état (mode actif ou non, prochaine échéance), le **rythme** en pastilles (par exemple 5 min · 10 min · 30 min · 1 h · 3 h pour π-piquant et Photos ; des pastilles en secondes et minutes pour SlowMovie), la case « afficher le titre » pour π-piquant, le bouton **« Activer ce mode »** et le bouton **« ⚙ Réglages (admin) »**. Le bouton Réglages demande le mot de passe administrateur puis ouvre les paramètres complets du mode : pour SlowMovie, tout ce que contient aujourd'hui l'onglet Réglages (état du Pi, films, départ et fin, sous-titres, réglages) ; pour π-piquant, rythme libre dans les bornes et titre ; pour Photos, lien de l'album et rythme libre. L'onglet « Réglages » actuel disparaît : son contenu devient le ⚙ du sous-onglet SlowMovie.
- **Envoyer** : deux sous-onglets, **Image** et **π-piquant**, avec les formulaires actuels (durée, texte, case titre pour le π-piquant, mot de passe), en plus compact. La page de suivi `/envoi/<id>` est inchangée.
- Accessibilité conservée : onglets au clavier (`role=tablist`), texte inséré en `textContent` jamais en HTML, états annoncés (`aria-live`).

## 8. Bornes à retenir (proposées, à valider par le Pi)

SlowMovie : le réglage `delay` existant. π-piquant et Photos : de 2 minutes à 24 heures (le rafraîchissement d'un écran e-paper ne doit pas être trop fréquent). Pastilles proposées : 5 min, 10 min, 30 min, 1 h, 3 h (adaptables).

## 9. Déjà en place, à ne pas refaire

Onglet d'envoi d'un π-piquant (tirage, API, file, page de suivi, case titre) ; légendes à 20 px avec marge basse `caption_margin` ; accès administrateur et commandes distantes ; mot de passe général et limitation de débit ; TUI des réglages ; bouton « Retour à l'accueil » de la page de suivi.

## 10. Tests

Pi : bascule de mode et reprise du film ; échéance du diaporama ; priorité d'une image envoyée et reprise ; erreur de la passerelle (image conservée, nouvel essai à l'échéance) ; bornes des rythmes ; réglages inconnus refusés. Passerelle : actions restreintes au mot de passe général (mauvais mot de passe, blocage, valeurs prédéfinies seulement) ; validation stricte du lien d'album ; tirage π-piquant et photo avec un faux serveur ; album illisible, trop lourd, trop lent ; liste en cache ; lien jamais journalisé. Interface : sous-onglets, ⚙ qui demande l'administrateur, pastilles, `textContent` seulement. Essai réel avec un lien d'album fourni par l'utilisateur.

## 11. Déploiement

Pas de nouveau secret : le lien d'album est un réglage, pas une variable d'environnement. Passerelle redéployée au push ; Pi mis à jour avec la nouvelle version (le diaporama tourne sur le Pi).

## 12. Décisions de l'implémenteur (à relire)

- La case « titre » du π-piquant est réglable avec le mot de passe général **et** dans les réglages administrateur (§ 4).
- Traitement du lien d'album comme un secret partout (§ 5).
- Lectures d'état et actions de mode : limiteur propre, hors des 20 envois par heure.
- Corps de la légende : chaque diapositive ou image envoyée porte son propre corps (14 px pour un π-piquant, 20 px sinon) ; le Pi n'a pas à deviner.
- Pause et « passer » pendant un diaporama : la pause suspend le compte à rebours, « passer » tire tout de suite la diapositive suivante.
- Compatibilité : une nouvelle passerelle avec un Pi pas encore mis à jour ne doit rien casser (le Pi ignore ce qu'il ne connaît pas et la page indique que le Pi doit être mis à jour).
- Les fichiers d'essai de l'album sont des **données synthétiques** qui imitent la structure de la page ; une copie de la vraie page n'est jamais commitée (elle contient les adresses des photos).

## 13. Laissé à celui qui construit

Découpage en étapes (passerelle, Pi, interface), format exact des réponses et des paramètres, façon de lire la page de l'album, gestion du cache, forme des pastilles et de la mise en page, nom des états.
