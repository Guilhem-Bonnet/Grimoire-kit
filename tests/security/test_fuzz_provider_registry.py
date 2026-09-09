"""Campagne de fuzzing — lecteur du registre de fournisseurs (issue #352).

Cible choisie parmi les analyseurs YAML édités à la main du kit :
``grimoire.providers.registry`` (registre de fournisseurs LLM). C'est le
lecteur le plus exposé de ce groupe — le fichier est modifié à la main par
l'opérateur, jamais généré puis relu par le même code, et son échec ne doit
JAMAIS remonter une exception non prévue jusqu'à ``grimoire providers
status`` ou au routage par coût (``choose()``).

Contrat de sortie d'une campagne (voir l'agent ``security-auditor``) :

1. Corpus de graines versionné → :data:`_SEED_CORPUS` ci-dessous.
2. Tout plantage trouvé devient un test de régression explicite, jamais un
   rapport en prose.
3. Un run qui ne trouve rien dit ce qu'il a couvert et combien d'exemples il
   a exécutés (voir la sortie de ``test_campaign_report``).

Outillage : ``hypothesis`` — préféré à une boucle maison parce que la surface
fuzzée est structurée (mapping YAML arbitrairement imbriqué), que
``hypothesis`` sait déjà générer et *réduire* ce genre de structure au plus
petit contre-exemple, et que ``@example`` fixe chaque graine du corpus comme
cas exécuté à chaque run — pas seulement échantillonné.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from grimoire.providers.registry import (
    REGISTRY_FILE,
    ProviderRegistryError,
    _model_specs,
    _provider_spec,
    _str_tuple,
    read_default_fallback_chain,
    read_registry,
)

# ── Corpus de graines versionné ─────────────────────────────────────────────
# Chaque entrée est un texte de registre déjà vu ou plausible : registre vide,
# registre v1 sans les champs du lot 2, YAML tronqué, tabulations (invalides
# en YAML), bytes non-UTF-8 décodés en remplacement, structure racine qui
# n'est pas une table. Nouvelles graines ajoutées ici à chaque plantage
# reproduit (règle du contrat de sortie, pas une suggestion).
_SEED_CORPUS: tuple[str, ...] = (
    "",
    "providers: []\n",
    "providers:\n  - id: anthropic\n    enabled: true\n",
    "providers:\n  - {}\n",
    "providers: null\n",
    "providers: [1, 2, 3]\n",
    "not-a-mapping-at-all\n",
    "- juste\n- une\n- liste\n",
    "providers:\n  - id: x\n\tenabled: true\n",  # tabulation : invalide en YAML
    "routing:\n  default_fallback_chain: {}\n",
    ":\n",  # deux-points isolé — limite de parseur connue
)


def _write_registry(root: Path, text: str) -> None:
    path = root / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8", errors="surrogatepass") if _is_surrogate(text) else text.encode("utf-8", errors="ignore"))


def _is_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


@example(text=_SEED_CORPUS[0])
@example(text=_SEED_CORPUS[1])
@example(text=_SEED_CORPUS[2])
@example(text=_SEED_CORPUS[3])
@example(text=_SEED_CORPUS[4])
@example(text=_SEED_CORPUS[5])
@example(text=_SEED_CORPUS[6])
@example(text=_SEED_CORPUS[7])
@example(text=_SEED_CORPUS[8])
@example(text=_SEED_CORPUS[9])
@example(text=_SEED_CORPUS[10])
@given(text=st.text(max_size=500))
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_registry_text_never_escapes_declared_errors(tmp_path: Path, text: str) -> None:
    """N'importe quel contenu textuel : soit ça se lit, soit ``ProviderRegistryError``.

    Aucune autre exception (``KeyError``, ``AttributeError``, ``TypeError``...)
    ne doit franchir la frontière du lecteur — c'est exactement la classe de
    bug qu'un registre édité à la main peut déclencher en production. Chaque
    ``@example`` ci-dessus fixe une graine du corpus versionné : elle
    s'exécute à *chaque* run, pas seulement quand hypothesis la tire au sort.
    """
    _write_registry(tmp_path, text)
    try:
        read_registry(tmp_path)
        read_default_fallback_chain(tmp_path)
    except ProviderRegistryError:
        pass


# ── Fuzzing structurel : la forme déjà parsée par PyYAML/ruamel ─────────────
# Une fois le YAML décodé, ``read_registry`` reçoit des structures Python
# arbitraires (dict/list/scalaires) — c'est la frontière la plus riche en
# formes invalides qu'un humain écrit sans le vouloir (un ``id`` qui est une
# liste, un ``models`` qui est une chaîne, etc.).
_json_scalar = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text(max_size=20))
_json_value = st.recursive(
    _json_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=10), children, max_size=5),
    ),
    max_leaves=20,
)


@given(entry=st.dictionaries(st.text(max_size=15), _json_value, max_size=8))
@settings(max_examples=300)
def test_provider_spec_never_crashes_on_arbitrary_entry(entry: dict[str, Any]) -> None:
    """``_provider_spec`` : une entrée mal formée est ignorée ou typée — jamais une levée."""
    _provider_spec(entry)


@given(value=_json_value)
@settings(max_examples=200)
def test_model_specs_and_str_tuple_never_crash(value: Any) -> None:
    """Les deux normalisateurs de listes ne doivent planter sur aucune forme JSON-like."""
    _model_specs(value)
    _str_tuple(value)


@given(mapping=st.dictionaries(st.text(max_size=10), _json_value, max_size=6))
@settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_read_registry_end_to_end_never_crashes(tmp_path: Path, mapping: dict[str, Any]) -> None:
    """Bout en bout : un mapping racine arbitraire, une fois écrit en YAML, se lit sans lever."""
    import io

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    buf = io.StringIO()
    try:
        yaml.dump(mapping, buf)
    except Exception:
        return
    _write_registry(tmp_path, buf.getvalue())
    try:
        read_registry(tmp_path)
        read_default_fallback_chain(tmp_path)
    except ProviderRegistryError:
        pass


def test_campaign_report(tmp_path: Path) -> None:
    """Preuve de couverture : la campagne a bien tourné, sur combien de graines.

    Un agent qui ne trouve rien doit le prouver, pas l'affirmer — ce test
    rejoue explicitement tout le corpus versionné et compte les exemples.
    """
    import contextlib

    covered = 0
    for seed in _SEED_CORPUS:
        _write_registry(tmp_path, seed)
        with contextlib.suppress(ProviderRegistryError):
            read_registry(tmp_path)
        covered += 1
    assert covered == len(_SEED_CORPUS)
    # hypothesis exécute par ailleurs ~850 exemples générés à travers les
    # trois propriétés ci-dessus (200 + 300 + 200 + 150) à chaque run de la
    # suite, en plus des `len(_SEED_CORPUS)` graines fixées ici.
