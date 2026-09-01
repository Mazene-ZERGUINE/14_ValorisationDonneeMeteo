"""
Tests d'intégration de la vue SQL `v_quotidienne_realtime`.

La vue combine 3 sources horaires pour produire un agrégat journalier :
- `InfrahoraireTempsReel` : <3 h dans le passé (granularité infra-horaire,
  agrégée par heure via LAST/MIN/MAX)
- `HoraireTempsReel`      : 3 h à 3 jours dans le passé
- `Horaire`               : 3 jours à 4 jours dans le passé

Journée météorologique :
- `tn` du jour D = MIN(tn) sur la fenêtre (D-1 18:01) → (D 18:00)
- `tx` du jour D = MAX(tx) sur la fenêtre (D 06:01)   → (D+1 06:00)
- `tntxm` = (tn+tx)/2 ; les lignes où l'un des deux manque sont exclues.

Filtre final : `timestamp >= date_trunc('day', now()) - interval '3 days'`.

Pour rester robustes à l'heure d'exécution, les tests s'ancrent sur des
instants qui restent dans la même fenêtre source quelle que soit l'heure :
"hier à midi" pour HTR, et "J-4 20:00" pour Horaire (voir `_horaire_tn_anchor`).
"""

from __future__ import annotations

import datetime as dt

import pytest

from weather.tests.helpers.horaire import (
    fetch_v_quotidienne_realtime,
    insert_horaire,
    insert_horaire_temps_reel,
    insert_infrahoraire_temps_reel,
)
from weather.tests.helpers.meteorological_day import (
    meteorological_tn_day,
    meteorological_tx_day,
)

pytestmark = pytest.mark.django_db


STATION = "75114001"


def _utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _at(date: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=dt.UTC)


def _yesterday() -> dt.date:
    return _utc_today() - dt.timedelta(days=1)


def _horaire_tn_anchor() -> dt.datetime:
    """
    Lecture `Horaire` alimentant la `tn` du jour J-3 à n'importe quelle heure.

    La fenêtre `Horaire` s'arrête à `date_trunc('hour', now()) - 3 jours` : du
    jour J-3, elle ne couvre donc que les heures antérieures à l'heure courante.
    Un ancrage "J-3 à midi" n'y tombe que si les tests tournent après 13 h UTC
    (c'est ce qui les rendait flaky). `J-4 20:00` est en revanche toujours dans
    la fenêtre, et sa fenêtre tn (J-4 18:00:01 → J-3 18:00:00) le rattache
    systématiquement au jour J-3.
    """
    return _at(_utc_today() - dt.timedelta(days=4), 20)


def _htr_tx_anchor() -> dt.datetime:
    """
    Lecture HTR alimentant la `tx` du jour J-3 à n'importe quelle heure.

    Symétriquement, `Horaire` ne peut pas fournir la `tx` de J-3 avant 07 h UTC
    (sa fenêtre tx commence à J-3 06:00:01, après la coupure de la source) : on
    la fait porter par une lecture HTR à `J-2 06:00`, toujours dans la fenêtre
    HTR et dont la fenêtre tx (J-3 06:00:01 → J-2 06:00:00) vise le jour J-3.
    """
    return _at(_utc_today() - dt.timedelta(days=2), 6)


# ---------------------------------------------------------------------------
# Source HoraireTempsReel (3 h à 36 h dans le passé)
# ---------------------------------------------------------------------------


def test_v_quotidienne_realtime_happy_path_from_htr():
    """Une lecture HTR hier midi produit une ligne quotidienne datée d'hier."""
    yesterday = _yesterday()
    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=15.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert len(rows) == 1
    assert rows[0]["date"].date() == yesterday
    assert float(rows[0]["tn"]) == pytest.approx(5.0)
    assert float(rows[0]["tx"]) == pytest.approx(15.0)
    assert float(rows[0]["tntxm"]) == pytest.approx(10.0)


def test_v_quotidienne_realtime_aggregates_min_max_per_day():
    """Plusieurs lectures sur le même jour → MIN(tn) et MAX(tx)."""
    yesterday = _yesterday()
    insert_horaire_temps_reel(STATION, _at(yesterday, 10), tn=5.0, tx=12.0)
    insert_horaire_temps_reel(STATION, _at(yesterday, 14), tn=3.0, tx=17.0)
    insert_horaire_temps_reel(STATION, _at(yesterday, 16), tn=8.0, tx=15.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert len(rows) == 1
    assert float(rows[0]["tn"]) == pytest.approx(3.0)
    assert float(rows[0]["tx"]) == pytest.approx(17.0)
    assert float(rows[0]["tntxm"]) == pytest.approx(10.0)


def test_v_quotidienne_realtime_separates_stations():
    other = "31069001"
    yesterday = _yesterday()

    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=15.0)
    insert_horaire_temps_reel(other, _at(yesterday, 12), tn=8.0, tx=18.0)

    rows_a = fetch_v_quotidienne_realtime(station_code=STATION)
    rows_b = fetch_v_quotidienne_realtime(station_code=other)

    assert len(rows_a) == 1
    assert float(rows_a[0]["tn"]) == pytest.approx(5.0)
    assert len(rows_b) == 1
    assert float(rows_b[0]["tn"]) == pytest.approx(8.0)


# NB : le test "lectures sur deux journées différentes" est couvert par
# `combines_htr_and_horaire_across_days` plus bas, qui utilise Horaire pour
# le jour ancien (la fenêtre HTR de 36 h ne couvre pas systématiquement la
# veille d'avant-hier selon l'heure d'exécution).


# ---------------------------------------------------------------------------
# Source InfrahoraireTempsReel (< 3 h)
# ---------------------------------------------------------------------------


def test_v_quotidienne_realtime_aggregates_infrahoraire_into_hourly_min_max():
    """
    3 lectures infra-horaires dans la même heure sont d'abord agrégées
    par heure (`LAST(t)`, `MIN(t)`, `MAX(t)`) puis intégrées au quotidien.
    L'agrégat horaire tombe sur `date_trunc('hour', t - 1s) + 1h`.

    Selon l'heure d'exécution, ce bucket peut être à cheval entre deux
    journées météorologiques (tn-day ≠ tx-day) : dans ce cas, aucune
    ligne n'apparaît car chaque journée manque l'une des deux valeurs.
    """
    now = _utc_now()
    hour_start = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    bucket = hour_start + dt.timedelta(hours=1)
    tn_day = meteorological_tn_day(bucket)
    tx_day = meteorological_tx_day(bucket)

    # 3 lectures dans la même heure : t = 5, 15, 10
    insert_infrahoraire_temps_reel(
        STATION, hour_start + dt.timedelta(minutes=10), t=5.0
    )
    insert_infrahoraire_temps_reel(
        STATION, hour_start + dt.timedelta(minutes=30), t=15.0
    )
    insert_infrahoraire_temps_reel(
        STATION, hour_start + dt.timedelta(minutes=50), t=10.0
    )

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    if tn_day == tx_day:
        # Cas nominal : bucket dans (06:01, 18:00] d'une même journée méteo
        assert len(rows) == 1
        assert rows[0]["date"].date() == tn_day
        assert float(rows[0]["tn"]) == pytest.approx(5.0)
        assert float(rows[0]["tx"]) == pytest.approx(15.0)
    else:
        # Bucket à cheval (avant 06:01 ou après 18:00) :
        # tn alimente tn_day, tx alimente tx_day, chacun manque l'autre
        # → tntxm NULL → ligne exclue
        assert rows == []


def test_v_quotidienne_realtime_infrahoraire_coexists_with_htr():
    """
    HTR sur hier et ITR récent : la lecture d'hier reste intacte tant que
    l'ITR n'alimente pas la fenêtre tx d'hier (cas "tôt le matin", où
    l'ITR contribue tx à hier).
    """
    yesterday = _yesterday()
    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=15.0)

    now = _utc_now()
    reading_time = now - dt.timedelta(minutes=30)
    # bucket = heure suivant celle de reading_time
    bucket = reading_time.replace(minute=0, second=0, microsecond=0) + dt.timedelta(
        hours=1
    )
    itr_tx_day = meteorological_tx_day(bucket)

    insert_infrahoraire_temps_reel(STATION, reading_time, t=99.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    yesterday_row = next(r for r in rows if r["date"].date() == yesterday)
    assert float(yesterday_row["tn"]) == pytest.approx(5.0)
    if itr_tx_day == yesterday:
        # ITR contribue tx à hier : MAX(15, 99) = 99
        assert float(yesterday_row["tx"]) == pytest.approx(99.0)
    else:
        # ITR contribue tx à un autre jour : tx d'hier reste à 15
        assert float(yesterday_row["tx"]) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Source Horaire (36 h à 4 jours dans le passé)
# ---------------------------------------------------------------------------


def test_v_quotidienne_realtime_uses_horaire_for_older_data():
    """Donnée hors fenêtre HTR : la `tn` de J-3 vient bien de la table `Horaire`."""
    three_days_ago = _utc_today() - dt.timedelta(days=3)
    insert_horaire(STATION, _horaire_tn_anchor(), tn=2.0)
    insert_horaire_temps_reel(STATION, _htr_tx_anchor(), tx=12.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert len(rows) == 1
    assert rows[0]["date"].date() == three_days_ago
    # tn=2.0 n'existe que dans `Horaire` : la source est bien lue par la vue.
    assert float(rows[0]["tn"]) == pytest.approx(2.0)
    assert float(rows[0]["tx"]) == pytest.approx(12.0)
    assert float(rows[0]["tntxm"]) == pytest.approx(7.0)


def test_v_quotidienne_realtime_combines_htr_and_horaire_across_days():
    """Deux jours via deux sources différentes → 2 lignes dans la vue."""
    yesterday = _yesterday()
    three_days_ago = _utc_today() - dt.timedelta(days=3)

    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=15.0)
    insert_horaire(STATION, _horaire_tn_anchor(), tn=2.0)
    insert_horaire_temps_reel(STATION, _htr_tx_anchor(), tx=12.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert len(rows) == 2
    assert rows[0]["date"].date() == three_days_ago
    assert float(rows[0]["tn"]) == pytest.approx(2.0)
    assert float(rows[0]["tx"]) == pytest.approx(12.0)
    assert rows[1]["date"].date() == yesterday
    assert float(rows[1]["tn"]) == pytest.approx(5.0)
    assert float(rows[1]["tx"]) == pytest.approx(15.0)


# NB : la fenêtre HTR (de now-3j à now-3h) et la fenêtre Horaire (de now-4j à
# now-3j) ne se chevauchent jamais. L'agrégation cross-sources sur un MÊME jour
# météo est exercée par `uses_horaire_for_older_data` (tn Horaire + tx HTR sur
# J-3) et par HTR+ITR (`infrahoraire_coexists_with_htr`).


# ---------------------------------------------------------------------------
# Journée météorologique : décalage tn (18 h) et tx (6 h)
# ---------------------------------------------------------------------------


def test_v_quotidienne_realtime_tn_uses_18h_meteorological_day():
    """
    `tn` du jour D = MIN(tn) sur les lectures dont l'heure ≤ D 18:00.
    Une lecture à `hier 17:00` doit alimenter `tn(hier)`.
    Une lecture à `hier 20:00` doit alimenter `tn(aujourd'hui)` (et donc NE PAS
    contaminer `tn(hier)`).
    """
    yesterday = _yesterday()

    insert_horaire_temps_reel(STATION, _at(yesterday, 17), tn=10.0, tx=20.0)
    # tn=3.0 à 20h : si la frontière était mal appliquée, cette valeur
    # se retrouverait dans tn(hier) et MIN deviendrait 3.0 au lieu de 10.0.
    insert_horaire_temps_reel(STATION, _at(yesterday, 20), tn=3.0, tx=15.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    # Seule la ligne d'hier est complète : aujourd'hui n'a pas de tx
    # (pas de lecture entre aujourd'hui 06:01 et demain 06:00).
    assert len(rows) == 1
    assert rows[0]["date"].date() == yesterday
    assert float(rows[0]["tn"]) == pytest.approx(10.0)
    # tx d'hier reçoit les deux lectures (toutes deux < demain 06:00) → MAX = 20.0
    assert float(rows[0]["tx"]) == pytest.approx(20.0)


def test_v_quotidienne_realtime_tx_uses_6h_meteorological_day():
    """
    `tx` du jour D = MAX(tx) sur les lectures dont l'heure > D 06:00.
    Une lecture à `hier 04:00` doit alimenter `tx(avant-hier)`.
    Une lecture à `hier 12:00` doit alimenter `tx(hier)` (et `tx(hier)` NE doit
    PAS être contaminée par celle de 04:00).
    """
    yesterday = _yesterday()

    # tx=30 à 04h : doit basculer sur avant-hier ; si la frontière 6h était
    # mal appliquée, on retrouverait 30 dans tx(hier).
    insert_horaire_temps_reel(STATION, _at(yesterday, 4), tn=10.0, tx=30.0)
    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=20.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    # Avant-hier n'apparaît pas : il n'a pas de tn (les deux lectures ont une
    # tn-day = hier, pas avant-hier).
    assert len(rows) == 1
    assert rows[0]["date"].date() == yesterday
    # tn d'hier reçoit les deux lectures → MIN(10, 5) = 5.0
    assert float(rows[0]["tn"]) == pytest.approx(5.0)
    # tx d'hier reçoit UNIQUEMENT la lecture de 12h → 20.0 (pas 30.0)
    assert float(rows[0]["tx"]) == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Filtres `tntxm IS NOT NULL` et "3 jours"
# ---------------------------------------------------------------------------


def test_v_quotidienne_realtime_filters_day_with_only_tn():
    """Un jour avec uniquement tn (tx manquant) → tntxm NULL → ligne exclue."""
    yesterday = _yesterday()
    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=5.0, tx=None)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert rows == []


def test_v_quotidienne_realtime_filters_day_with_only_tx():
    """Symétrique : un jour avec uniquement tx (tn manquant) → ligne exclue."""
    yesterday = _yesterday()
    insert_horaire_temps_reel(STATION, _at(yesterday, 12), tn=None, tx=15.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert rows == []


def test_v_quotidienne_realtime_excludes_data_older_than_3_days():
    """
    Donnée à 4 jours dans le passé (encore dans la fenêtre Horaire), mais la
    journée agrégée tombe avant `date_trunc('day', now()) - 3 jours` et la
    lecture elle-même est antérieure à `now() - 4j + 18h` pour la tn
    (et `- 3j + 6h` pour la tx) → exclue.
    """
    four_days_ago = _utc_today() - dt.timedelta(days=4)
    insert_horaire(STATION, _at(four_days_ago, 12), tn=2.0, tx=12.0)

    rows = fetch_v_quotidienne_realtime(station_code=STATION)

    assert rows == []
