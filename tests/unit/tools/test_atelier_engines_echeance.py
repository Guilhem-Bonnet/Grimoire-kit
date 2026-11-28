"""L'échéance totale du transport tient quel que soit le rythme du serveur : VRAI petit serveur sur 127.0.0.1, lancé et arrêté ici.

Quatre rythmes hostiles (corps, en-têtes, ligne de statut au compte-gouttes ; connecté puis muet), délai total court : l'appel
entier se termine à l'échéance plus une petite marge, en ``EngineDelai``, sans fil ni connexion laissés.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from grimoire.tools.atelier_engines import transport as tr

DELAI = 0.5
PAS = 0.03
MARGE = 2.0


class Serveur:
    """Un serveur brut : lit la requête, puis répond selon ``mode`` à un octet par ``PAS`` secondes (ou se tait)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.threads: list[threading.Thread] = []
        self.accepteur = threading.Thread(target=self._accepter, daemon=True)
        self.accepteur.start()

    def _accepter(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            t = threading.Thread(target=self._servir, args=(conn,), daemon=True)
            self.threads.append(t)
            t.start()

    def _servir(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(0.1)
            lu = b""
            while b"\r\n\r\n" not in lu and not self.stop.is_set():
                try:
                    bloc = conn.recv(4096)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not bloc:
                    return
                lu += bloc
            if self.mode == "muet":
                self.stop.wait()
                return
            statut, entetes = b"HTTP/1.1 200 OK\r\n", b"Content-Type: application/json\r\nContent-Length: 100000\r\n\r\n"
            tout = {"corps": statut + entetes + b"x" * 100_000, "entetes": statut + b"X-Lent: " + b"a" * 100_000,
                    "statut": b"HTTP/1.1 200 OK " + b"a" * 100_000}[self.mode]
            envoye = 0
            if self.mode == "corps":  # statut et en-têtes d'un coup, puis le corps goutte à goutte
                conn.sendall(statut + entetes)
                tout, envoye = b"x" * 100_000, 0
            while not self.stop.is_set() and envoye < len(tout):
                try:
                    conn.sendall(tout[envoye:envoye + 1])
                except OSError:
                    return  # le client a fermé : le serveur s'arrête
                envoye += 1
                time.sleep(PAS)

    def fermer(self) -> None:
        self.stop.set()
        self.listener.close()
        self.accepteur.join(5)
        for t in self.threads:
            t.join(5)


@pytest.fixture
def serveur(request: pytest.FixtureRequest) -> Iterator[Serveur]:
    avant = threading.active_count()
    s = Serveur(request.param)
    yield s
    s.fermer()
    assert not any(t.is_alive() for t in s.threads) and not s.accepteur.is_alive()
    time.sleep(0.2)
    assert threading.active_count() <= avant, "aucun fil ne survit à l'appel"


@pytest.mark.parametrize("serveur", ["corps", "entetes", "statut", "muet"], indirect=True)
def test_l_appel_entier_se_termine_a_l_echeance_quel_que_soit_le_rythme_du_serveur(serveur: Serveur) -> None:
    debut = time.monotonic()
    with pytest.raises(tr.EngineDelai):
        tr.default_transport(f"http://127.0.0.1:{serveur.port}/v1", {"content-type": "application/json"}, b"{}", DELAI)
    duree = time.monotonic() - debut
    assert duree < MARGE, f"{serveur.mode} : {duree:.1f} s pour un délai total de {DELAI} s"
    assert duree >= DELAI * 0.8
    # La connexion est fermée : le serveur le voit et sa boucle de goutte-à-goutte s'arrête d'elle-même.
    fin = time.monotonic() + 3
    while time.monotonic() < fin and serveur.mode != "muet" and any(t.is_alive() for t in serveur.threads):
        time.sleep(0.05)
    if serveur.mode != "muet":
        assert not any(t.is_alive() for t in serveur.threads), "le client a laissé sa connexion ouverte"


@pytest.mark.parametrize("serveur", ["muet"], indirect=True)
def test_aucun_fil_de_surveillance_ne_survit_a_un_appel_reussi_ou_en_erreur(serveur: Serveur) -> None:
    avant = {t.ident for t in threading.enumerate()}
    with pytest.raises(tr.EngineDelai):
        tr.default_transport(f"http://127.0.0.1:{serveur.port}/", {}, b"{}", 0.3)
    assert {t.ident for t in threading.enumerate() if t.name.startswith("Thread-") or "timer" in t.name.lower()} <= avant | {
        t.ident for t in serveur.threads} | {serveur.accepteur.ident}


def _tls_au_compte_gouttes(stop: threading.Event, ecouteur: socket.socket, apres: float) -> threading.Thread:
    """Accepte après ``apres`` s, puis répond à la poignée de main TLS par un enregistrement géant, un octet par ``PAS``."""

    def servir() -> None:
        stop.wait(apres)
        ecouteur.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = ecouteur.accept()
                break
            except TimeoutError:
                continue
            except OSError:
                return
        else:
            return
        with conn:
            conn.settimeout(0.1)
            with contextlib.suppress(OSError):
                conn.recv(4096)
            # Un ServerHello de 1024 octets dans un enregistrement valide, livré au compte-gouttes : jamais complet à temps.
            tout = b"\x16\x03\x03\x04\x04" + b"\x02\x00\x04\x00" + b"\x00" * 1024
            for i in range(len(tout)):
                if stop.is_set():
                    return
                try:
                    conn.sendall(tout[i:i + 1])
                except OSError:
                    return
                time.sleep(PAS)

    t = threading.Thread(target=servir, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize("accepte_apres", [0.0, 0.3])
def test_la_poignee_de_main_tls_au_compte_gouttes_tient_l_echeance_totale(accepte_apres: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """Connexion lente (résolution retardée, accept tardif) PUIS poignée de main lente : le tout reste sous l'échéance."""
    ecouteur = socket.socket()
    ecouteur.bind(("127.0.0.1", 0))
    ecouteur.listen(1)
    port = ecouteur.getsockname()[1]
    reel = socket.getaddrinfo

    def lent(*args: Any, **kw: Any) -> Any:
        time.sleep(0.4)
        return reel(*args, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", lent)
    stop = threading.Event()
    t = _tls_au_compte_gouttes(stop, ecouteur, accepte_apres)
    debut = time.monotonic()
    try:
        with pytest.raises(tr.EngineDelai) as info:
            tr.default_transport(f"https://127.0.0.1:{port}/v1", {}, b"{}", DELAI)
        duree = time.monotonic() - debut
        assert duree < DELAI + 0.25, f"{duree:.2f} s pour un délai total de {DELAI} s"  # avant : 0,4 s de connexion + 0,5 s de poignée de main
        assert info.value.__context__ is None and info.value.__cause__ is None
    finally:
        stop.set()
        t.join(5)
        ecouteur.close()
    assert not t.is_alive()


def test_une_file_d_ecoute_pleine_borne_la_connexion_tcp_a_l_echeance() -> None:
    """File d'écoute de 1 déjà occupée, jamais d'``accept`` : le SYN suivant reste sans réponse, la connexion TCP est bornée."""
    ecouteur = socket.socket()
    ecouteur.bind(("127.0.0.1", 0))
    ecouteur.listen(1)
    port = ecouteur.getsockname()[1]
    occupants: list[socket.socket] = []
    try:
        for _ in range(8):  # remplit la file jusqu'à ce qu'une connexion reste en suspens
            c = socket.socket()
            c.setblocking(False)
            with contextlib.suppress(BlockingIOError):
                c.connect(("127.0.0.1", port))
            occupants.append(c)
        time.sleep(0.2)
        debut = time.monotonic()
        with pytest.raises(tr.EngineDelai):
            tr.default_transport(f"https://127.0.0.1:{port}/v1", {}, b"{}", DELAI)
        duree = time.monotonic() - debut
        assert DELAI * 0.8 <= duree < DELAI + 0.6, f"{duree:.1f} s pour un délai total de {DELAI} s"
    finally:
        for c in occupants:
            c.close()
        ecouteur.close()


def test_une_reponse_rapide_passe_telle_quelle() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]

    def repondre() -> None:
        conn, _ = s.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    t = threading.Thread(target=repondre, daemon=True)
    t.start()
    try:
        assert tr.default_transport(f"http://127.0.0.1:{port}/", {}, b"{}", 2.0) == (200, b"ok")
    finally:
        t.join(5)
        s.close()


def test_une_redirection_n_est_pas_suivie_et_le_corps_est_borne() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    gros: Any = b"x" * (tr.MAX_REPONSE + 10)

    def repondre() -> None:
        conn, _ = s.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://127.0.0.1:1/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        conn, _ = s.accept()
        with conn:
            conn.recv(65536)
            conn.settimeout(3)
            try:
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(gros) + gros)
                while conn.recv(4096):  # attend que le client ferme : pas de RST qui masquerait la réponse
                    pass
            except OSError:
                pass

    t = threading.Thread(target=repondre, daemon=True)
    t.start()
    try:
        assert tr.default_transport(f"http://127.0.0.1:{port}/", {}, b"{}", 2.0) == (307, b"")
        with pytest.raises(tr.EngineSortie):
            tr.default_transport(f"http://127.0.0.1:{port}/", {}, b"{}", 5.0)
    finally:
        t.join(5)
        s.close()


def test_un_port_ferme_est_une_erreur_reseau_sans_citer_l_adresse() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(tr.EngineReseau) as info:
        tr.default_transport(f"http://127.0.0.1:{port}/", {}, b"{}", 1.0)
    assert str(port) not in str(info.value) and info.value.__context__ is None and info.value.__cause__ is None
