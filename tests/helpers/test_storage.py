"""Tests for the storage helper."""
import asyncio
from datetime import timedelta
import json
from unittest.mock import Mock, patch

import pytest

from homeassistant.const import (
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import CoreState
from homeassistant.helpers import storage
from homeassistant.util import dt

from tests.common import async_fire_time_changed

MOCK_VERSION = 1
MOCK_VERSION_2 = 2
MOCK_MINOR_VERSION_1 = 1
MOCK_MINOR_VERSION_2 = 2
MOCK_KEY = "storage-test"
MOCK_DATA = {"hello": "world"}
MOCK_DATA2 = {"goodbye": "cruel world"}


@pytest.fixture
def store(hass):
    """Fixture of a store that prevents writing on Home Assistant stop."""
    yield storage.Store(hass, MOCK_VERSION, MOCK_KEY)


@pytest.fixture
def store_v_1_1(hass):
    """Fixture of a store that prevents writing on Home Assistant stop."""
    yield storage.Store(
        hass, MOCK_VERSION, MOCK_KEY, minor_version=MOCK_MINOR_VERSION_1
    )


@pytest.fixture
def store_v_1_2(hass):
    """Fixture of a store that prevents writing on Home Assistant stop."""
    yield storage.Store(
        hass, MOCK_VERSION, MOCK_KEY, minor_version=MOCK_MINOR_VERSION_2
    )


@pytest.fixture
def store_v_2_1(hass):
    """Fixture of a store that prevents writing on Home Assistant stop."""
    yield storage.Store(
        hass, MOCK_VERSION_2, MOCK_KEY, minor_version=MOCK_MINOR_VERSION_1
    )


async def test_loading(hass, store):
    """Test we can save and load data."""
    await store.async_save(MOCK_DATA)
    data = await store.async_load()
    assert data == MOCK_DATA


async def test_custom_encoder(hass):
    """Test we can save and load data."""

    class JSONEncoder(json.JSONEncoder):
        """Mock JSON encoder."""

        def default(self, o):
            """Mock JSON encode method."""
            return "9"

    store = storage.Store(hass, MOCK_VERSION, MOCK_KEY, encoder=JSONEncoder)
    await store.async_save(Mock())
    data = await store.async_load()
    assert data == "9"


async def test_loading_non_existing(hass, store):
    """Test we can save and load data."""
    with patch("homeassistant.util.json.open", side_effect=FileNotFoundError):
        data = await store.async_load()
    assert data is None


async def test_loading_parallel(hass, store, hass_storage, caplog):
    """Test we can save and load data."""
    hass_storage[store.key] = {"version": MOCK_VERSION, "data": MOCK_DATA}

    results = await asyncio.gather(store.async_load(), store.async_load())

    assert results[0] is MOCK_DATA
    assert results[1] is MOCK_DATA
    assert caplog.text.count(f"Loading data for {store.key}")


async def test_saving_with_delay(hass, store, hass_storage):
    """Test saving data after a delay."""
    store.async_delay_save(lambda: MOCK_DATA, 1)
    assert store.key not in hass_storage

    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": MOCK_DATA,
    }


async def test_saving_on_final_write(hass, hass_storage):
    """Test delayed saves trigger when we quit Home Assistant."""
    store = storage.Store(hass, MOCK_VERSION, MOCK_KEY)
    store.async_delay_save(lambda: MOCK_DATA, 5)
    assert store.key not in hass_storage

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    hass.state = CoreState.stopping
    await hass.async_block_till_done()

    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert store.key not in hass_storage

    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": MOCK_DATA,
    }


async def test_not_delayed_saving_while_stopping(hass, hass_storage):
    """Test delayed saves don't write after the stop event has fired."""
    store = storage.Store(hass, MOCK_VERSION, MOCK_KEY)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    hass.state = CoreState.stopping

    store.async_delay_save(lambda: MOCK_DATA, 1)
    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert store.key not in hass_storage


async def test_not_delayed_saving_after_stopping(hass, hass_storage):
    """Test delayed saves don't write after stop if issued before stopping Home Assistant."""
    store = storage.Store(hass, MOCK_VERSION, MOCK_KEY)
    store.async_delay_save(lambda: MOCK_DATA, 10)
    assert store.key not in hass_storage

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    hass.state = CoreState.stopping
    await hass.async_block_till_done()
    assert store.key not in hass_storage

    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=15))
    await hass.async_block_till_done()
    assert store.key not in hass_storage


async def test_not_saving_while_stopping(hass, hass_storage):
    """Test saves don't write when stopping Home Assistant."""
    store = storage.Store(hass, MOCK_VERSION, MOCK_KEY)
    hass.state = CoreState.stopping
    await store.async_save(MOCK_DATA)
    assert store.key not in hass_storage


async def test_loading_while_delay(hass, store, hass_storage):
    """Test we load new data even if not written yet."""
    await store.async_save({"delay": "no"})
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    store.async_delay_save(lambda: {"delay": "yes"}, 1)
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    data = await store.async_load()
    assert data == {"delay": "yes"}


async def test_writing_while_writing_delay(hass, store, hass_storage):
    """Test a write while a write with delay is active."""
    store.async_delay_save(lambda: {"delay": "yes"}, 1)
    assert store.key not in hass_storage
    await store.async_save({"delay": "no"})
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    data = await store.async_load()
    assert data == {"delay": "no"}


async def test_multiple_delay_save_calls(hass, store, hass_storage):
    """Test a write while a write with changing delays."""
    store.async_delay_save(lambda: {"delay": "yes"}, 1)
    store.async_delay_save(lambda: {"delay": "yes"}, 2)
    store.async_delay_save(lambda: {"delay": "yes"}, 3)

    assert store.key not in hass_storage
    await store.async_save({"delay": "no"})
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    async_fire_time_changed(hass, dt.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"delay": "no"},
    }

    data = await store.async_load()
    assert data == {"delay": "no"}


async def test_multiple_save_calls(hass, store, hass_storage):
    """Test multiple write tasks."""

    assert store.key not in hass_storage

    tasks = [store.async_save({"savecount": savecount}) for savecount in range(6)]
    await asyncio.gather(*tasks)
    assert hass_storage[store.key] == {
        "version": MOCK_VERSION,
        "minor_version": 1,
        "key": MOCK_KEY,
        "data": {"savecount": 5},
    }

    data = await store.async_load()
    assert data == {"savecount": 5}


async def test_migrator_no_existing_config(hass, store, hass_storage):
    """Test migrator with no existing config."""
    with patch("os.path.isfile", return_value=False), patch.object(
        store, "async_load", return_value={"cur": "config"}
    ):
        data = await storage.async_migrator(hass, "old-path", store)

    assert data == {"cur": "config"}
    assert store.key not in hass_storage


async def test_migrator_existing_config(hass, store, hass_storage):
    """Test migrating existing config."""
    with patch("os.path.isfile", return_value=True), patch("os.remove") as mock_remove:
        data = await storage.async_migrator(
            hass, "old-path", store, old_conf_load_func=lambda _: {"old": "config"}
        )

    assert len(mock_remove.mock_calls) == 1
    assert data == {"old": "config"}
    assert hass_storage[store.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": 1,
        "data": data,
    }


async def test_migrator_transforming_config(hass, store, hass_storage):
    """Test migrating config to new format."""

    async def old_conf_migrate_func(old_config):
        """Migrate old config to new format."""
        return {"new": old_config["old"]}

    with patch("os.path.isfile", return_value=True), patch("os.remove") as mock_remove:
        data = await storage.async_migrator(
            hass,
            "old-path",
            store,
            old_conf_migrate_func=old_conf_migrate_func,
            old_conf_load_func=lambda _: {"old": "config"},
        )

    assert len(mock_remove.mock_calls) == 1
    assert data == {"new": "config"}
    assert hass_storage[store.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": 1,
        "data": data,
    }


async def test_minor_version_default(hass, store, hass_storage):
    """Test minor version default."""

    await store.async_save(MOCK_DATA)
    assert hass_storage[store.key]["minor_version"] == 1


async def test_minor_version(hass, store_v_1_2, hass_storage):
    """Test minor version."""

    await store_v_1_2.async_save(MOCK_DATA)
    assert hass_storage[store_v_1_2.key]["minor_version"] == MOCK_MINOR_VERSION_2


async def test_migrate_major_not_implemented_raises(hass, store, store_v_2_1):
    """Test migrating between major versions fails if not implemented."""

    await store_v_2_1.async_save(MOCK_DATA)
    with pytest.raises(NotImplementedError):
        await store.async_load()


async def test_migrate_minor_not_implemented(
    hass, hass_storage, store_v_1_1, store_v_1_2
):
    """Test migrating between minor versions does not fail if not implemented."""

    assert store_v_1_1.key == store_v_1_2.key

    await store_v_1_1.async_save(MOCK_DATA)
    assert hass_storage[store_v_1_1.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": MOCK_MINOR_VERSION_1,
        "data": MOCK_DATA,
    }
    data = await store_v_1_2.async_load()
    assert hass_storage[store_v_1_1.key]["data"] == data

    await store_v_1_2.async_save(MOCK_DATA)
    assert hass_storage[store_v_1_2.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": MOCK_MINOR_VERSION_2,
        "data": MOCK_DATA,
    }


async def test_migration(hass, hass_storage, store_v_1_2):
    """Test migration."""
    calls = 0

    class CustomStore(storage.Store):
        async def _async_migrate_func(
            self, old_major_version, old_minor_version, old_data: dict
        ):
            nonlocal calls
            calls += 1
            assert old_major_version == store_v_1_2.version
            assert old_minor_version == store_v_1_2.minor_version
            return old_data

    await store_v_1_2.async_save(MOCK_DATA)
    assert hass_storage[store_v_1_2.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": MOCK_MINOR_VERSION_2,
        "data": MOCK_DATA,
    }
    assert calls == 0

    legacy_store = CustomStore(hass, 2, store_v_1_2.key, minor_version=1)
    data = await legacy_store.async_load()
    assert calls == 1
    assert hass_storage[store_v_1_2.key]["data"] == data

    await legacy_store.async_save(MOCK_DATA)
    assert hass_storage[legacy_store.key] == {
        "key": MOCK_KEY,
        "version": 2,
        "minor_version": 1,
        "data": MOCK_DATA,
    }


async def test_legacy_migration(hass, hass_storage, store_v_1_2):
    """Test legacy migration method signature."""
    calls = 0

    class LegacyStore(storage.Store):
        async def _async_migrate_func(self, old_version, old_data: dict):
            nonlocal calls
            calls += 1
            assert old_version == store_v_1_2.version
            return old_data

    await store_v_1_2.async_save(MOCK_DATA)
    assert hass_storage[store_v_1_2.key] == {
        "key": MOCK_KEY,
        "version": MOCK_VERSION,
        "minor_version": MOCK_MINOR_VERSION_2,
        "data": MOCK_DATA,
    }
    assert calls == 0

    legacy_store = LegacyStore(hass, 2, store_v_1_2.key, minor_version=1)
    data = await legacy_store.async_load()
    assert calls == 1
    assert hass_storage[store_v_1_2.key]["data"] == data

    await legacy_store.async_save(MOCK_DATA)
    assert hass_storage[legacy_store.key] == {
        "key": MOCK_KEY,
        "version": 2,
        "minor_version": 1,
        "data": MOCK_DATA,
    }
