"""La sortie du kit survit à une console qui ne parle pas UTF-8.

Régression : sur une console Windows en cp1252 — la valeur par défaut — le kit
mourait en `UnicodeEncodeError` dès qu'il imprimait un filet ou une flèche,
c'est-à-dire sur presque toutes ses commandes. Trouvé par la matrice Windows
ajoutée en #191, sur les outils gelés ; vérifié ensuite sur le chemin supporté,
qui avait exactement le même défaut — Rich échoue de la même façon.
"""

from __future__ import annotations

import io
import os
import sys

import pytest

from grimoire.core.console_encoding import enable_utf8_output

#: Un échantillon de ce que le kit imprime réellement, tout hors cp1252.
SAMPLE = "séparateur ─── flèche → coche ✅ garde ⚠"


def _cp1252_stream() -> io.TextIOWrapper:
    """Un flux qui se comporte comme une console Windows par défaut."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


class TestWithoutTheGuard:
    def test_a_cp1252_console_refuses_what_the_kit_prints(self) -> None:
        """Le contrôle négatif : sans correctif, l'écriture lève."""
        stream = _cp1252_stream()
        with pytest.raises(UnicodeEncodeError):
            stream.write(SAMPLE)
            stream.flush()


class TestWithTheGuard:
    def test_reconfigured_stream_degrades_instead_of_raising(self) -> None:
        stream = _cp1252_stream()
        stream.reconfigure(encoding="utf-8", errors="replace")
        stream.write(SAMPLE)
        stream.flush()  # ne lève pas — c'est tout ce qui est promis

    def test_entry_point_helper_reconfigures_the_real_streams(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, err = _cp1252_stream(), _cp1252_stream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)

        enable_utf8_output()

        assert out.encoding.lower().replace("-", "") == "utf8"
        assert err.encoding.lower().replace("-", "") == "utf8"
        # Les sous-processus héritent : les outils de framework/ s'appellent
        # par chemin de fichier, la variable est le seul canal qui passe.
        assert os.environ["PYTHONIOENCODING"] == "utf-8"

    def test_it_never_breaks_a_stream_it_cannot_reconfigure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un flux exotique ne doit pas transformer l'affichage en panne."""

        class _Opaque:
            encoding = "cp1252"

        monkeypatch.setattr(sys, "stdout", _Opaque())
        monkeypatch.setattr(sys, "stderr", _Opaque())
        enable_utf8_output()  # ne lève pas

    def test_calling_it_twice_is_harmless(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = _cp1252_stream()
        monkeypatch.setattr(sys, "stdout", out)
        enable_utf8_output()
        enable_utf8_output()
        assert out.encoding.lower().replace("-", "") == "utf8"
