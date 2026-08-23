from automation.fleet_sync.core import assign_contract_ids, merge_completed_history
from automation.fleet_sync.model import ContractObservation


def observation(**overrides):
    values = {
        "client": "Client",
        "region": "Region",
        "start_date": "2026-08-01",
        "end_date": "2027-08-31",
        "day_rate": 400_000,
        "status": "Firm",
        "page": 1,
        "row": "row=1",
    }
    values.update(overrides)
    return ContractObservation(**values)


def historical_contract(**overrides):
    values = {
        "id": "old",
        "vesselId": "rig-a",
        "startDate": "2025-01-01",
        "endDate": "2025-12-31",
        "dayRate": 300_000,
        "client": "Old Client",
        "region": "Region",
        "status": "Firm",
    }
    values.update(overrides)
    return values


def test_contract_id_does_not_change_with_end_date_or_rate() -> None:
    first = assign_contract_ids(
        "rig-a",
        [{"startDate": "2026-08-01", "endDate": "2027-08-31", "dayRate": 1, "client": "A", "region": "B", "status": "Firm"}],
    )[0]
    amended = assign_contract_ids(
        "rig-a",
        [{"startDate": "2026-08-01", "endDate": "2028-08-31", "dayRate": 2, "client": "A", "region": "B", "status": "Firm"}],
    )[0]
    assert first["id"] == amended["id"]


def test_history_merge_keeps_completed_non_overlapping_firm_contract() -> None:
    old = [historical_contract()]
    merged = merge_completed_history(old, [observation()], "2026-08-05")
    assert [item["client"] for item in merged] == ["Old Client", "Client"]
    assert merged[0]["startDate"] == "2025-01-01"
    assert merged[0]["endDate"] == "2025-12-31"
    assert merged[0]["dayRate"] == 0


def test_history_merge_clips_month_precision_boundary_overlap() -> None:
    old = [
        historical_contract(
            startDate="2016-07-01",
            endDate="2026-03-31",
        )
    ]
    current = observation(
        start_date="2026-03-01",
        end_date="2027-03-31",
        date_precision="month",
    )

    merged = merge_completed_history(old, [current], "2026-08-05")

    assert merged[0]["client"] == "Old Client"
    assert merged[0]["startDate"] == "2016-07-01"
    assert merged[0]["endDate"] == "2026-02-28"
    assert merged[0]["dayRate"] == 0
    assert merged[1]["client"] == "Client"


def test_history_merge_clips_valid_suffix_after_overlap() -> None:
    old = [
        historical_contract(
            startDate="2025-03-01",
            endDate="2025-12-31",
        )
    ]
    prior = observation(
        start_date="2024-03-01",
        end_date="2025-03-31",
        date_precision="month",
    )

    merged = merge_completed_history(old, [prior], "2026-08-05")

    assert merged[0]["client"] == "Client"
    assert merged[1]["client"] == "Old Client"
    assert merged[1]["startDate"] == "2025-04-01"
    assert merged[1]["endDate"] == "2025-12-31"
    assert merged[1]["dayRate"] == 0


def test_history_merge_drops_fully_contained_history() -> None:
    old = [
        historical_contract(
            startDate="2026-03-01",
            endDate="2026-03-31",
        )
    ]
    current = observation(
        start_date="2026-01-01",
        end_date="2026-06-30",
    )

    merged = merge_completed_history(old, [current], "2026-08-05")

    assert len(merged) == 1
    assert merged[0]["client"] == "Client"


def test_history_merge_is_idempotent_after_boundary_clipping() -> None:
    old = [
        historical_contract(
            startDate="2016-07-01",
            endDate="2026-03-31",
        )
    ]
    current = observation(
        start_date="2026-03-01",
        end_date="2027-03-31",
        date_precision="month",
    )

    first = merge_completed_history(old, [current], "2026-08-05")
    second = merge_completed_history(first, [current], "2026-08-05")

    assert second == first


def test_history_merge_drops_stale_future_and_options() -> None:
    old = [
        {
            "id": "old-option",
            "vesselId": "rig-a",
            "startDate": "2027-01-01",
            "endDate": "2028-01-01",
            "dayRate": 0,
            "client": "Old Client",
            "region": "Region",
            "status": "Option",
        }
    ]
    merged = merge_completed_history(old, [observation()], "2026-08-05")
    assert len(merged) == 1
    assert merged[0]["client"] == "Client"
