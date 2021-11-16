"""The Energy websocket API."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
import functools
from types import ModuleType
from typing import Any, Awaitable, Callable, cast

import voluptuous as vol

from homeassistant.components import recorder, websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.integration_platform import (
    async_process_integration_platforms,
)
from homeassistant.helpers.singleton import singleton
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .data import (
    DEVICE_CONSUMPTION_SCHEMA,
    ENERGY_SOURCE_SCHEMA,
    EnergyManager,
    EnergyPreferencesUpdate,
    async_get_manager,
)
from .types import EnergyPlatform, GetSolarForecastType
from .validate import async_validate

EnergyWebSocketCommandHandler = Callable[
    [HomeAssistant, websocket_api.ActiveConnection, "dict[str, Any]", "EnergyManager"],
    None,
]
AsyncEnergyWebSocketCommandHandler = Callable[
    [HomeAssistant, websocket_api.ActiveConnection, "dict[str, Any]", "EnergyManager"],
    Awaitable[None],
]


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Set up the energy websocket API."""
    websocket_api.async_register_command(hass, ws_get_prefs)
    websocket_api.async_register_command(hass, ws_save_prefs)
    websocket_api.async_register_command(hass, ws_info)
    websocket_api.async_register_command(hass, ws_validate)
    websocket_api.async_register_command(hass, ws_solar_forecast)
    websocket_api.async_register_command(hass, ws_get_fossil_energy_consumption)


@singleton("energy_platforms")
async def async_get_energy_platforms(
    hass: HomeAssistant,
) -> dict[str, GetSolarForecastType]:
    """Get energy platforms."""
    platforms: dict[str, GetSolarForecastType] = {}

    async def _process_energy_platform(
        hass: HomeAssistant, domain: str, platform: ModuleType
    ) -> None:
        """Process energy platforms."""
        if not hasattr(platform, "async_get_solar_forecast"):
            return

        platforms[domain] = cast(EnergyPlatform, platform).async_get_solar_forecast

    await async_process_integration_platforms(hass, DOMAIN, _process_energy_platform)

    return platforms


def _ws_with_manager(
    func: Any,
) -> websocket_api.WebSocketCommandHandler:
    """Decorate a function to pass in a manager."""

    @websocket_api.async_response
    @functools.wraps(func)
    async def with_manager(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        manager = await async_get_manager(hass)

        result = func(hass, connection, msg, manager)

        if asyncio.iscoroutine(result):
            await result

    return with_manager


@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/get_prefs",
    }
)
@_ws_with_manager
@callback
def ws_get_prefs(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
    manager: EnergyManager,
) -> None:
    """Handle get prefs command."""
    if manager.data is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No prefs")
        return

    connection.send_result(msg["id"], manager.data)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/save_prefs",
        vol.Optional("energy_sources"): ENERGY_SOURCE_SCHEMA,
        vol.Optional("device_consumption"): [DEVICE_CONSUMPTION_SCHEMA],
    }
)
@_ws_with_manager
async def ws_save_prefs(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
    manager: EnergyManager,
) -> None:
    """Handle get prefs command."""
    msg_id = msg.pop("id")
    msg.pop("type")
    await manager.async_update(cast(EnergyPreferencesUpdate, msg))
    connection.send_result(msg_id, manager.data)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/info",
    }
)
@websocket_api.async_response
async def ws_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get info command."""
    forecast_platforms = await async_get_energy_platforms(hass)
    connection.send_result(
        msg["id"],
        {
            "cost_sensors": hass.data[DOMAIN]["cost_sensors"],
            "solar_forecast_domains": list(forecast_platforms),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/validate",
    }
)
@websocket_api.async_response
async def ws_validate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle validate command."""
    connection.send_result(msg["id"], (await async_validate(hass)).as_dict())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/solar_forecast",
    }
)
@_ws_with_manager
async def ws_solar_forecast(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
    manager: EnergyManager,
) -> None:
    """Handle solar forecast command."""
    if manager.data is None:
        connection.send_result(msg["id"], {})
        return

    config_entries: dict[str, str | None] = {}

    for source in manager.data["energy_sources"]:
        if (
            source["type"] != "solar"
            or source.get("config_entry_solar_forecast") is None
        ):
            continue

        # typing is not catching the above guard for config_entry_solar_forecast being none
        for config_entry in source["config_entry_solar_forecast"]:  # type: ignore[union-attr]
            config_entries[config_entry] = None

    if not config_entries:
        connection.send_result(msg["id"], {})
        return

    forecasts = {}

    forecast_platforms = await async_get_energy_platforms(hass)

    for config_entry_id in config_entries:
        config_entry = hass.config_entries.async_get_entry(config_entry_id)
        # Filter out non-existing config entries or unsupported domains

        if config_entry is None or config_entry.domain not in forecast_platforms:
            continue

        forecast = await forecast_platforms[config_entry.domain](hass, config_entry_id)

        if forecast is not None:
            forecasts[config_entry_id] = forecast

    connection.send_result(msg["id"], forecasts)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "energy/fossil_energy_consumption",
        vol.Required("start_time"): str,
        vol.Required("end_time"): str,
        vol.Required("energy_statistic_ids"): [str],
        vol.Required("co2_statistic_id"): str,
        vol.Required("period"): vol.Any("5minute", "hour", "day", "month"),
    }
)
@websocket_api.async_response
async def ws_get_fossil_energy_consumption(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Calculate amount of fossil based energy."""
    start_time_str = msg["start_time"]
    end_time_str = msg["end_time"]

    if start_time := dt_util.parse_datetime(start_time_str):
        start_time = dt_util.as_utc(start_time)
    else:
        connection.send_error(msg["id"], "invalid_start_time", "Invalid start_time")
        return

    if end_time := dt_util.parse_datetime(end_time_str):
        end_time = dt_util.as_utc(end_time)
    else:
        connection.send_error(msg["id"], "invalid_end_time", "Invalid end_time")
        return

    # Fetch energy statistics
    energy_statistics = await hass.async_add_executor_job(
        recorder.statistics.statistics_during_period,
        hass,
        start_time,
        end_time,
        msg["energy_statistic_ids"],
        "hour",
        True,
    )

    # Fetch CO2 statistics
    co2_statistics = await hass.async_add_executor_job(
        recorder.statistics.statistics_during_period,
        hass,
        start_time,
        end_time,
        [msg["co2_statistic_id"]],
        "hour",
        True,
    )

    def _combine_sum_statistics(
        stats: dict[str, list[dict[str, Any]]]
    ) -> dict[datetime, float]:
        """Combine multiple statistics, returns a dict indexed by start time."""
        result: defaultdict[datetime, float] = defaultdict(float)

        for stat in stats.values():
            for period in stat:
                if period["sum"] is None:
                    continue
                result[period["start"]] += period["sum"]

        return dict(result)

    merged_energy_statistics = _combine_sum_statistics(energy_statistics)
    indexed_co2_statistics = {
        period["start"]: period["mean"]
        for period in co2_statistics.get(msg["co2_statistic_id"], {})
    }

    # Calculate amount of fossil based energy, assume 100% fossil if missing
    fossil_energy = [
        {"start": start, "sum": sum * indexed_co2_statistics.get(start, 100) / 100}
        for start, sum in merged_energy_statistics.items()
    ]

    if msg["period"] == "hour":
        result = {
            period["start"].isoformat(): period["sum"] for period in fossil_energy
        }
        connection.send_result(msg["id"], result)

    if msg["period"] == "day":
        reduced_fossil_energy = recorder.statistics.reduce_statistics_per_day(
            {"combined": fossil_energy}
        )
        result = {
            period["start"]: period["sum"]
            for period in reduced_fossil_energy["combined"]
        }
        connection.send_result(msg["id"], result)

    reduced_fossil_energy = recorder.statistics.reduce_statistics_per_month(
        {"combined": fossil_energy}
    )
    result = {
        period["start"]: period["sum"] for period in reduced_fossil_energy["combined"]
    }
    connection.send_result(msg["id"], result)
