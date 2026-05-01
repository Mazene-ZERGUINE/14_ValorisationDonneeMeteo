import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from weather.tests.helpers.stations import insert_station


@pytest.mark.django_db
def test_station_list_filters_by_first_temperature_year_max(client: APIClient):
    insert_station(
        "00000001", "Station 1959", first_temperature_date=dt.datetime(1959, 1, 1)
    )
    insert_station(
        "00000002", "Station 1960", first_temperature_date=dt.datetime(1960, 1, 1)
    )
    insert_station(
        "00000003", "Station 1975", first_temperature_date=dt.datetime(1975, 6, 1)
    )

    response = client.get(
        reverse("station-list"),
        {"first_temperature_year_max": 1960},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert [station["code"] for station in response.json()["results"]] == [
        "00000001",
        "00000002",
    ]
