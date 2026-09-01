"""
Helpers d'insertion pour les tables source horaires :
- `Horaire` : table consolidée (4 jours à 3 jours dans le passé)
- `HoraireTempsReel` : flux horaire temps réel (3 h à 3 jours dans le passé)
- `InfrahoraireTempsReel` : flux infra-horaire temps réel (3 dernières heures)
- `mv_quotidienne_realtime` : la "MV" remplacée par table en test (voir conftest)
"""

from __future__ import annotations

import datetime as dt

from django.db import connection


def insert_horaire(
    num_poste: str,
    timestamp: dt.datetime,
    *,
    t: float | None = None,
    tn: float | None = None,
    tx: float | None = None,
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public."Horaire"
                ("NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI",
                 "AAAAMMJJHH", "T", "TN", "TX")
            VALUES
                (%(num_poste)s, %(name)s, 0, 0, 0,
                 %(timestamp)s, %(t)s, %(tn)s, %(tx)s)
            ON CONFLICT ("NUM_POSTE", "AAAAMMJJHH")
            DO UPDATE SET
                "T"  = EXCLUDED."T",
                "TN" = EXCLUDED."TN",
                "TX" = EXCLUDED."TX"
            """,
            {
                "num_poste": num_poste,
                "name": f"ST {num_poste}",
                "timestamp": timestamp,
                "t": t,
                "tn": tn,
                "tx": tx,
            },
        )


def insert_horaire_temps_reel(
    geo_id_insee: str,
    validity_time: dt.datetime,
    *,
    t: float | None = None,
    tn: float | None = None,
    tx: float | None = None,
    reference_time: dt.datetime | None = None,
    insert_time: dt.datetime | None = None,
) -> None:
    ref = reference_time or validity_time
    ins = insert_time or validity_time
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public."HoraireTempsReel"
                ("geo_id_insee", "lat", "lon",
                 "reference_time", "insert_time", "validity_time",
                 "t", "tn", "tx")
            VALUES
                (%(geo_id_insee)s, 0, 0,
                 %(reference_time)s, %(insert_time)s, %(validity_time)s,
                 %(t)s, %(tn)s, %(tx)s)
            ON CONFLICT ("geo_id_insee", "validity_time")
            DO UPDATE SET
                "t"  = EXCLUDED."t",
                "tn" = EXCLUDED."tn",
                "tx" = EXCLUDED."tx"
            """,
            {
                "geo_id_insee": geo_id_insee,
                "reference_time": ref,
                "insert_time": ins,
                "validity_time": validity_time,
                "t": t,
                "tn": tn,
                "tx": tx,
            },
        )


def insert_infrahoraire_temps_reel(
    geo_id_insee: str,
    validity_time: dt.datetime,
    *,
    t: float | None = None,
    reference_time: dt.datetime | None = None,
    insert_time: dt.datetime | None = None,
) -> None:
    ref = reference_time or validity_time
    ins = insert_time or validity_time
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public."InfrahoraireTempsReel"
                ("geo_id_insee", "lat", "lon",
                 "reference_time", "insert_time", "validity_time", "t")
            VALUES
                (%(geo_id_insee)s, 0, 0,
                 %(reference_time)s, %(insert_time)s, %(validity_time)s, %(t)s)
            ON CONFLICT ("geo_id_insee", "validity_time")
            DO UPDATE SET "t" = EXCLUDED."t"
            """,
            {
                "geo_id_insee": geo_id_insee,
                "reference_time": ref,
                "insert_time": ins,
                "validity_time": validity_time,
                "t": t,
            },
        )


def insert_mv_quotidienne_realtime(
    station_code: str,
    date: dt.date | dt.datetime,
    *,
    tn: float | None = None,
    tx: float | None = None,
) -> None:
    """
    Insère une ligne dans la "MV" mv_quotidienne_realtime, qui est une
    table régulière en test (voir conftest). `tntxm` est dérivé de (tn+tx)/2,
    comme côté SQL.
    """
    tntxm = (tn + tx) / 2 if tn is not None and tx is not None else None
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.mv_quotidienne_realtime
                (station_code, date, tntxm, tn, tx)
            VALUES
                (%(station_code)s, %(date)s, %(tntxm)s, %(tn)s, %(tx)s)
            """,
            {
                "station_code": station_code,
                "date": date,
                "tntxm": tntxm,
                "tn": tn,
                "tx": tx,
            },
        )


def fetch_all_as_dicts(sql: str, params: list | None = None) -> list[dict]:
    """Exécute `sql` et retourne les lignes sous forme de dicts colonne → valeur."""
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        columns = [col[0] for col in cur.description]
        return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]


def fetch_v_quotidienne_realtime(
    station_code: str | None = None,
    date_start: dt.date | dt.datetime | None = None,
    date_end: dt.date | dt.datetime | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if station_code is not None:
        clauses.append("station_code = %s")
        params.append(station_code)
    if date_start is not None:
        clauses.append("date >= %s")
        params.append(date_start)
    if date_end is not None:
        clauses.append("date <= %s")
        params.append(date_end)

    sql = "SELECT station_code, date, tntxm, tn, tx FROM public.v_quotidienne_realtime"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY station_code, date"

    return fetch_all_as_dicts(sql, params)


def fetch_v_quotidienne(
    station_code: str | None = None,
    date_start: dt.date | dt.datetime | None = None,
    date_end: dt.date | dt.datetime | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if station_code is not None:
        clauses.append("station_code = %s")
        params.append(station_code)
    if date_start is not None:
        clauses.append("date >= %s")
        params.append(date_start)
    if date_end is not None:
        clauses.append("date <= %s")
        params.append(date_end)

    sql = "SELECT station_code, date, tntxm, tn, tx FROM public.v_quotidienne"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY station_code, date"

    return fetch_all_as_dicts(sql, params)


def fetch_v_mensuelle_realtime(
    station_code: str | None = None,
    date_start: dt.date | dt.datetime | None = None,
    date_end: dt.date | dt.datetime | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if station_code is not None:
        clauses.append("station_code = %s")
        params.append(station_code)
    if date_start is not None:
        clauses.append("date >= %s")
        params.append(date_start)
    if date_end is not None:
        clauses.append("date <= %s")
        params.append(date_end)

    sql = (
        "SELECT station_code, date, tnn, tnn_date, txx, txx_date, tmm "
        "FROM public.v_mensuelle_realtime"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY station_code, date"

    return fetch_all_as_dicts(sql, params)
