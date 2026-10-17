"""Génère la page du storyboard (HTML artefact) à partir des images clés rendues par shots.mjs.

Usage : python3 -I storyboard.py [<dossier teaser> [<images clés> [<sortie>]]]
Les images clés sont <frames dir>/f-<t>.png ; elles sont converties en JPEG 960 px dans <out dir>/sb/.
"""
import html
import json
import pathlib
import subprocess
import sys

V = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
F = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else V / "shots"
OUT = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else V / "storyboard"
(OUT / "sb").mkdir(parents=True, exist_ok=True)

BEAT = 60 / 90
BAR = 4 * BEAT


def tc(t):
    m = int(t // BAR) + 1
    b = (t % BAR) / BEAT + 1
    return f"{t:0.1f} s · M{m}.{round(b)}"


# plan, mouvement de caméra, technique de montage, son, ce qui est réel
PLANS = [
    ("1.2", "Acte 1 · Le carnet de notes", "Noir, puis une note vide. Le titre « Idées » se tape. C'est l'ouverture à froid : aucun logo, aucun mot de marque.",
     "Fixe. Légère vignette.", "Cold open ; letterbox 2,39 pour les actes 1 et 3.", "Ton de pièce, un clic de clavier par lettre.", "Le texte tapé est celui d'un carnet d'idées fictif (démonstration)."),
    ("2.6", "Première idée", "« un petit jeu vidéo » se tape ; une fenêtre surgit à droite avec un rush : des mains sur une manette, image froide et désaturée.",
     "La fenêtre entre avec un léger rebond (back-out, 0,25 s).", "Image dans l'image (PiP) : l'idée appelle sa scène.", "Un « pop » grave, un souffle d'ambiance.", "Rush libre de droits (source dans le tableau en bas)."),
    ("4.6", "Trois idées", "La boutique, le site de l'asso. Une fenêtre par idée : colis, maquette de site. Le rythme est encore calme : une idée par demi-mesure.",
     "Fixe.", "Montage au tempo (90 BPM) : chaque idée tombe sur un temps.", "Clics, pops, le bourdon grave entre à M3.", "Rushes."),
    ("6.9", "Cinq idées", "Le piano, le script qui range les photos. La note se décale à gauche et rétrécit : les idées prennent la place.",
     "Recadrage lent de la note (move, 1 mesure).", "Compression de l'espace : le cadre se remplit.", "La pulsation (kick) sur chaque noire.", "Rushes."),
    ("9.0", "L'avalanche", "Les idées s'enchaînent à chaque temps puis à chaque croche : bot, portfolio, dépenses, API… Les fenêtres se chevauchent.",
     "Fixe, puis premier whip pan à M4.3.", "Accélération des coupes (1 → ½ → ¼ de temps) ; whip pan avec flou directionnel.", "Charleston en croches, riser qui commence.", "—"),
    ("10.3", "Whip pan", "La caméra fouette vers la droite et revient : on ne sait plus où regarder.",
     "Whip pan 260 px en 0,14 s, flou 26 px.", "Whip pan (Edgar Wright, bandes-annonces de jeux).", "Le riser monte, double-croches.", "—"),
    ("11.5", "Grille 3×3", "Toutes les fenêtres se rangent d'un coup en grille : neuf scènes en même temps. La note disparaît.",
     "Snap en 0,18 s (move).", "Split-screen à la « You Can't Stop Us » : plusieurs vies à la fois.", "Triple-croches, le riser sature.", "—"),
    ("12.3", "Grille 4×4 et glitch", "Seize fenêtres. Deux secousses de séparation des canaux rouge/bleu : l'image ne tient plus.",
     "Zoom expo 1,02 → 1,05.", "Glitch RVB (feOffset par canal), 3 images.", "Dernière triple-croche.", "—"),
    ("12.8", "Smash cut au noir", "Tout s'arrête. Noir, 0,66 s.",
     "—", "Smash cut : le silence est le climax de l'acte 1.", "Coupure nette du son, un « boom » grave qui s'éteint.", "—"),
    ("15.3", "Acte 2 · Le terminal", "Une fenêtre de terminal, calme, plein cadre (les bandes du letterbox se retirent). On tape `uv tool install grimoire-kit`.",
     "Fixe.", "Rupture de registre : du cinéma à l'écran, de la nuit au froid technique.", "Clavier mécanique, un pad discret en ré.", "Commande réelle ; la sortie qui suit est celle capturée le 17/10 (grimoire-kit 3.62.1)."),
    ("16.6", "Chargement", "« Resolved 9 packages », « Downloading grimoire-kit (4.4MiB) », une barre se remplit.",
     "Fixe.", "Le chargement comme ellipse : c'est le temps qui passe entre l'idée et la chose.", "Tics réguliers qui montent, carillon à la fin.", "Lignes verbatim de `uv` ; la barre est l'affichage d'avancement (ni chiffre ni texte inventé)."),
    ("19.3", "Deuxième et troisième commandes", "`grimoire init . --lite -y` : 54 lignes tombent en 0,75 s. Puis `grimoire standard init . --profile governed` : les [OK] défilent.",
     "Fixe, défilement du terminal.", "Speed ramp sur la sortie : l'installation est un souffle.", "Whoosh + micro-tics, un par ligne.", "Sorties verbatim (extrait : l'avertissement PATH propre à l'installation isolée est retiré)."),
    ("22.3", "Le cockpit, en un souffle", "Trois coupes sur trois temps : le tableau de toutes les idées en chantier, zoom sur « En cours » et ses sessions vivantes, puis « À traiter ».",
     "Coupes franches sur le temps, zooms fixes.", "Flash-forward sur le temps : le chargement devient l'atelier.", "Le kick revient, trois notes pincées.", "Maquette de la refonte (légendée « maquette »), données de démonstration."),
    ("23.9", "La fusion", "Le curseur touche la PR « Écran de pause du jeu » : les deux branches du glyphe se rejoignent. Puis un balayage de lumière emporte tout.",
     "Fixe, puis wipe lumineux gauche → droite.", "Le geste humain comme pivot ; light wipe comme transition d'acte.", "Clic franc, carillon.", "La fusion d'une PR est le geste humain réel ; l'écran est une maquette."),
    ("24.8", "Acte 3 · On joue", "Plein cadre : notre jeu. Un petit coureur bleu saute les obstacles et ramasse des pièces. Letterbox, image chaude.",
     "Poussée lente (1,0 → 1,045).", "Match thématique avec la première idée ; le jeu est rendu par nous (canvas pixel art).", "Le motif mélodique se termine enfin (ré la mi fa#) ; sons du jeu en onde carrée sur les pièces.", "Jeu fictif dessiné pour le film."),
    ("26.2", "L'écran dans le salon", "Le même jeu, sur la télé d'un salon. Le jeu est incrusté dans l'écran vert du rush ; la pièce, les lampes et la plante sont réelles.",
     "Fixe (rush), le jeu vit dans l'écran.", "Remplacement d'écran : homographie (4 coins détectés) + clé verte dans un shader, ce qui garde les reflets et les occultations.", "Arpège doux, sons du jeu.", "Rush + jeu incrusté."),
    ("27.0", "On joue", "Les mains sur la manette, lumière de jour. Rime avec la première fenêtre de l'acte 1, qui montrait les mêmes mains dans la pénombre.",
     "Fixe (rush), ralenti léger.", "Rime acte 1 ↔ acte 3 : même sujet, autre lumière ; la personne joue, l'outil n'est pas à l'image.", "Arpège.", "Rush."),
    ("28.3", "La boutique", "Par-dessus l'épaule : la boutique « Atelier Mirelle » sur le portable, quatre céramiques. Une notification tombe : « Nouvelle commande ».",
     "Fixe (rush).", "Remplacement d'écran par clé verte (la tête passe devant l'écran, l'interface reste derrière) ; fuite de lumière chaude au raccord.", "Petit carillon de notification.", "Interface fictive de démonstration."),
    ("29.4", "Le colis", "Des mains ferment le colis étiqueté : la commande part.",
     "Fixe (rush).", "Rime avec le carton scotché de l'acte 1 ; coupe sur le temps.", "Arpège, pulsation douce.", "Rush."),
    ("31.4", "Le site", "Un portable ouvert : « Les Jardins Partagés », le site de l'asso, avec sa photo.",
     "Fixe (rush), poussée lente.", "Remplacement d'écran ; rime avec la fenêtre « maquette de site » de l'acte 1.", "Arpège.", "Interface fictive."),
    ("33.8", "Le piano", "Des mains sur un clavier ; une carte flotte : « Joue un Ré majeur », les touches s'allument.",
     "Fixe (rush).", "Rime avec l'acte 1 (le piano), carte d'interface flottante.", "L'accord de ré majeur, vraiment joué (piano synthétisé), sur le temps.", "Interface fictive."),
    ("36.5", "Les photos rangées", "Sur une table de photos imprimées, une fenêtre : la grille se vide dans trois dossiers, 2024, 2025, Vacances.",
     "Fixe (rush), fenêtre flottante.", "Chorégraphie d'objets (stagger de 40 ms), demi-mesure.", "Tics et un souffle.", "Les photos sont des images des rushes."),
    ("38.6", "Le carnet, le jour", "La même note, en clair : chaque idée a maintenant sa vignette. Une puce vide attend la suivante.",
     "Fixe, zoom imperceptible.", "Rime d'ouverture et de fin (même objet, autre lumière) ; la liste n'est pas « cochée », elle continue.", "Le pad se résout.", "—"),
    ("41.0", "Signature", "Fond Douce. « › Grimoire ». Aucune phrase.",
     "Fixe.", "Carton de fin à la façon des films de lancement : le nom, puis le geste.", "Canon à trois voix sur le motif, qui se rejoignent sur fa#.", "—"),
    ("45.0", "Le geste", "`$ uv tool install grimoire-kit` se tape, puis « Open source · Claude Code · Copilot ». Fin sur l'image tenue.",
     "Fixe.", "La commande est la seule ligne : c'est l'appel à l'action et la preuve que c'est réel.", "Clavier, silence final.", "Commande réelle."),
]

cards = []
for t, titre, plan, cam, tech, son, vrai in PLANS:
    src = F / f"f-{t}.png"
    dst = OUT / "sb" / f"f-{t}.jpg"
    if src.exists():
        subprocess.run(["magick", str(src), "-resize", "960x540", "-depth", "8", "-quality", "86", str(dst)], check=True)
    cards.append(f'''
    <article class="shot">
      <img src="sb/f-{t}.jpg" alt="" width="960" height="540" loading="lazy">
      <div class="meta">
        <div class="tc">{tc(float(t))}</div>
        <h3>{html.escape(titre)}</h3>
        <p>{html.escape(plan)}</p>
        <dl>
          <dt>Caméra</dt><dd>{html.escape(cam)}</dd>
          <dt>Technique</dt><dd>{html.escape(tech)}</dd>
          <dt>Son</dt><dd>{html.escape(son)}</dd>
          <dt>Réel</dt><dd>{html.escape(vrai)}</dd>
        </dl>
      </div>
    </article>''')

sources = []
sp = V / "footage-sources.json"
if sp.exists():
    try:
        for s in json.loads(sp.read_text()):
            sources.append(f"<tr><td>{html.escape(str(s.get('plan','')))}</td><td>{html.escape(str(s.get('description','')))}</td><td>{html.escape(str(s.get('source','')))}</td><td>{html.escape(str(s.get('licence','')))}</td><td><a href=\"{html.escape(str(s.get('url_page','')))}\" rel=\"noopener\">page</a></td></tr>")
    except Exception as e:
        sources.append(f"<tr><td colspan=5>sources.json illisible : {html.escape(str(e))}</td></tr>")

page = f'''<title>Storyboard · Teaser Grimoire</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
:root{{--bg:#eef0f4;--card:#fff;--line:#e3e6eb;--ink:#14171d;--ink2:#4a515c;--ink3:#606874;--acc:#3451d1;--rub:#b4441d;--mono:ui-monospace,"JetBrains Mono",Menlo,monospace;--sans:"Instrument Sans",ui-sans-serif,system-ui,sans-serif}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f1115;--card:#1c2027;--line:#2a2f38;--ink:#eef0f4;--ink2:#b9c0cb;--ink3:#9099a6;--acc:#8ea2ff;--rub:#f08a5d;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#0f1115;--card:#1c2027;--line:#2a2f38;--ink:#eef0f4;--ink2:#b9c0cb;--ink3:#9099a6;--acc:#8ea2ff;--rub:#f08a5d;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.5;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:32px 64px}}
.w{{max-width:1240px;margin:0 auto}}
h1{{font-size:clamp(30px,4.5vw,52px);letter-spacing:-.035em;line-height:1.02;margin:0 0 8px;font-weight:600}}
.sub{{color:var(--ink2);font-size:18px;margin:0 0 28px;max-width:70ch}}
h2{{font-size:24px;letter-spacing:-.02em;margin:48px 0 14px;font-weight:600}}
.acts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:0 0 8px}}
.act{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}}
.act b{{display:block;font-size:14px;color:var(--ink3);font-weight:500;margin-bottom:4px}}
.act span{{font-weight:600}}
.video{{background:#000;border-radius:16px;overflow:hidden;margin:18px 0 8px}}
.video video{{display:block;width:100%;height:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}}
.shot{{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;display:grid;grid-template-rows:auto 1fr}}
.shot img{{display:block;width:100%;height:auto;background:#000}}
.meta{{padding:14px 16px 16px}}
.tc{{font-family:var(--mono);font-size:12.5px;color:var(--ink3);margin-bottom:4px}}
.shot h3{{margin:0 0 6px;font-size:17px;letter-spacing:-.01em;font-weight:600}}
.shot p{{margin:0 0 10px;color:var(--ink2);font-size:14.5px}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;margin:0;font-size:13.5px}}
dt{{color:var(--ink3);font-weight:500}}dd{{margin:0;color:var(--ink2)}}
.opts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
.opt{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}}
.opt b{{display:block;margin-bottom:6px}}
.opt .ex{{font-family:var(--mono);font-size:14px;background:var(--bg);border-radius:8px;padding:8px 10px;margin:8px 0}}
.tech{{columns:2;column-gap:28px;font-size:15px;color:var(--ink2)}}
.tech li{{break-inside:avoid;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{text-align:left;padding:8px 10px;border-top:1px solid var(--line);vertical-align:top}}
thead th{{border-top:0;color:var(--ink3);font-weight:500}}
a{{color:var(--acc)}}
.tbl{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:14px}}
.note{{font-size:14px;color:var(--ink3)}}
@media (max-width:620px){{.tech{{columns:1}}}}
</style>
<div class="w">
  <h1>Storyboard du teaser</h1>
  <p class="sub">48 secondes, 90 BPM, trois actes. Des idées plein un carnet, trois commandes avec leur chargement, puis les idées devenues des choses. Aucune phrase de marque : le nom, puis la commande. L'explication vient plus bas sur la page du produit.</p>
  <div class="acts">
    <div class="act"><b>0 → 12,7 s</b><span>Acte 1 · le carnet déborde</span><br><small>montée, split-screen, smash cut</small></div>
    <div class="act"><b>13,3 → 24 s</b><span>Acte 2 · trois commandes</span><br><small>terminal réel, chargement, cockpit en un souffle</small></div>
    <div class="act"><b>24 → 40 s</b><span>Acte 3 · les idées en vrai</span><br><small>rimes avec l'acte 1, écrans incrustés, image chaude</small></div>
    <div class="act"><b>40 → 48 s</b><span>Signature</span><br><small>le nom, la commande</small></div>
  </div>
  <div class="video"><video src="teaser-rough.mp4" controls playsinline preload="metadata" poster="sb/f-24.8.jpg"></video></div>
  <p class="note">Premier montage (rough cut) : les plans et le rythme sont là ; les rushes, l'étalonnage et le mixage restent à affiner avec tes retours.</p>

  <h2>Plan par plan</h2>
  <div class="grid">{''.join(cards)}
  </div>

  <h2>Cartons de fin : trois options</h2>
  <div class="opts">
    <div class="opt"><b>A · Le nom, puis le geste (montée)</b>« › Grimoire », puis la commande qui se tape, puis « Open source · Claude Code · Copilot ». Pas de phrase : c'est ce que font les films de lancement qui ne veulent rien expliquer.<div class="ex">› Grimoire<br>$ uv tool install grimoire-kit</div></div>
    <div class="opt"><b>A' · Le terminal figé</b>Pas de logo du tout : le terminal de l'acte 2 revient, les trois commandes passées, le prompt revenu, le curseur immobile. Le nom est dans la commande (modèle Homebrew, Bun).<div class="ex">$ uv tool install grimoire-kit<br>$ grimoire init . --lite -y<br>$ grimoire standard init . --profile governed<br>$ ▮</div></div>
    <div class="opt"><b>B · Une ligne qui désigne l'image</b>Sur le dernier plan du jeu, avant le nom : une ligne qui parle de ce qu'on vient de voir, pas du spectateur.<div class="ex">Celui-là, c'est le tien.</div><span class="note">Test du mémo : vraie dans l'image, pas collable sur un autre produit, sans « avant » ni « maintenant ».</span></div>
    <div class="opt"><b>C · Un seul mot</b>Le nom seul, en très grand, la commande en petit dessous.<div class="ex">Grimoire</div></div>
  </div>

  <h2>Ce qu'on a pris aux pubs</h2>
  <ul class="tech">
    <li><b>Nike, « You Can't Stop Us »</b> : la rime par split-screen, même geste d'un côté et de l'autre de la couture. Chez nous : les scènes de l'acte 1 reviennent à l'acte 3, autre lumière, autre vitesse.</li>
    <li><b>Apple, « Hello » (2007)</b> : 30 s sur un seul mot, le produit n'est jamais nommé ; le nom arrive à la fin avec un fait (« Coming in June »). Chez nous : rien n'est expliqué, le nom arrive à 40 s.</li>
    <li><b>Microsoft Copilot, « Watch Me »</b> : idée → prompt → résultat, c'est notre structure. On laisse de côté son prologue de sceptiques et son carton-promesse.</li>
    <li><b>Anthropic, « Keep thinking »</b> : le même motif, polarité inversée. Chez nous : la mélodie coupée trois fois à l'acte 1 va au bout à l'acte 3.</li>
    <li><b>Derek Lieu (bandes-annonces de jeux)</b> : jamais de logo en ouverture, le hook dans le premier tiers, « bring it down for a beat » avant la chute. Chez nous : coupe au noir à 12,7 s, respiration après la fusion, chute à 24 s.</li>
    <li><b>Apple, « Scary Fast »</b> : le carton de fin est un fait vérifiable (« shot on iPhone, edited on Mac »). Chez nous : la commande réelle, et les sorties de terminal verbatim.</li>
    <li><b>Contre-exemples, « Crush! » et « Dear Sydney »</b> : l'outil qui écrase ou remplace l'humain. Chez nous : la personne joue, emballe, répète ses accords ; les idées sont réalisées, pas remplacées.</li>
    <li><b>Figma Config 2025</b> : cadence abaissée à 15 i/s pour un rendu fait main. Chez nous : les rushes de l'acte 1 tournent à 15 i/s, ceux de l'acte 3 à 30.</li>
  </ul>
  <p class="note">Mémo complet, avec les liens et ce qui n'a pas pu être vérifié : <code>refs-pub.md</code> (joint aux preuves de la tâche).</p>

  <h2>Techniques de montage et de motion design employées</h2>
  <ul class="tech">
    <li><b>Cold open</b> sans logo : la marque n'apparaît qu'à 40 s.</li>
    <li><b>Montage au tempo</b> : toutes les coupes tombent sur la grille à 90 BPM ; l'acte 1 accélère de la noire à la triple-croche.</li>
    <li><b>Image dans l'image</b> qui prolifère, puis <b>split-screen</b> 3×3 et 4×4.</li>
    <li><b>Whip pan</b> avec flou directionnel (filtre SVG anisotrope).</li>
    <li><b>Glitch RVB</b> par séparation des canaux, trois images.</li>
    <li><b>Smash cut au noir</b> et coupure du son : le climax de la montée est un silence.</li>
    <li><b>Letterbox 2,39</b> pour le « film » (actes 1 et 3), plein cadre pour le « vrai » (le terminal).</li>
    <li><b>Étalonnage</b> froid et désaturé avant, chaud après ; <b>fuites de lumière</b> aux raccords de l'acte 3.</li>
    <li><b>Speed ramp</b> sur les sorties du terminal : 54 lignes en 0,75 s.</li>
    <li><b>Le chargement comme ellipse</b> : la barre, puis trois coupes de cockpit, puis le <b>light wipe</b>.</li>
    <li><b>Remplacement d'écran</b> par homographie (matrice 3D calculée depuis 4 coins) sur la télé, le téléphone, le portable.</li>
    <li><b>Rimes</b> acte 1 ↔ acte 3 : même sujet, autre vitesse, autre lumière ; même carnet au début et à la fin.</li>
    <li><b>Grain</b> indexé sur le numéro d'image, vignette, rendu déterministe image par image sur GPU.</li>
    <li><b>Son synchronisé</b> à chaque geste : clics de clavier, pops, kick, riser, carillons, le jeu en onde carrée, l'accord de piano réellement joué sur le temps.</li>
  </ul>

  <h2>Rushes</h2>
  <div class="tbl"><table><thead><tr><th>Plan</th><th>Description</th><th>Source</th><th>Licence</th><th></th></tr></thead><tbody>{''.join(sources) or '<tr><td colspan="5">Rushes en cours de sélection : les fenêtres grises sont des images de remplacement.</td></tr>'}</tbody></table></div>
  <p class="note">Les interfaces (jeu, boutique, site, leçon de piano, photos) sont dessinées pour le film et n'imitent aucun produit réel. Les sorties de terminal sont celles capturées le 17/10/2026 avec grimoire-kit 3.62.1. Le cockpit est la maquette de la refonte, légendée « maquette ».</p>
</div>
'''
(OUT / "index.html").write_text(page, encoding="utf-8")
print("ok", len(cards), "plans")
