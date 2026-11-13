"""Tests for GLEDOPTO quirks."""

from unittest import mock

import pytest
from zha.quirks import DEVICE_REGISTRY
from zigpy.zcl import ClusterType
from zigpy.zcl.clusters.general import (
    Basic,
    Groups,
    Identify,
    LevelControl,
    OnOff,
    Scenes,
)
from zigpy.zcl.clusters.lighting import Color

from zhaquirks.builder import UnitOfTemperature, UnitOfTime
from zhaquirks.gledopto.glc008p import (
    COLOR_LOOP_DEFAULT_TIME,
    GledoptoColorCluster,
    GledoptoColorLoopConfigCluster,
    GledoptoColorRangeConfigCluster,
)
import zhaquirks.gledopto.glc009


def test_gledopto_glc009_signature(assert_signature_matches_quirk):
    """Test GLEDOPTO GL-C-009 signature is matched to its quirk."""
    signature = {
        "node_descriptor": "NodeDescriptor(logical_type=<LogicalType.Router: 1>, complex_descriptor_available=0, user_descriptor_available=0, reserved=0, aps_flags=0, frequency_band=<FrequencyBand.Freq2400MHz: 8>, mac_capability_flags=<MACCapabilityFlags.AllocateAddress|RxOnWhenIdle|MainsPowered|FullFunctionDevice: 142>, manufacturer_code=0, maximum_buffer_size=80, maximum_incoming_transfer_size=160, server_mask=0, maximum_outgoing_transfer_size=160, descriptor_capability_field=<DescriptorCapability.NONE: 0>, *allocate_address=True, *is_alternate_pan_coordinator=False, *is_coordinator=False, *is_end_device=False, *is_full_function_device=True, *is_mains_powered=True, *is_receiver_on_when_idle=True, *is_router=True, *is_security_capable=False)",
        "endpoints": {
            "11": {
                "profile_id": 49246,
                "device_type": "0x0100",
                "in_clusters": [
                    "0x0000",
                    "0x0003",
                    "0x0004",
                    "0x0005",
                    "0x0006",
                    "0x0008",
                    "0x0300",
                ],
                "out_clusters": [],
            },
            "13": {
                "profile_id": 49246,
                "device_type": "0x0100",
                "in_clusters": ["0x1000"],
                "out_clusters": ["0x1000"],
            },
        },
        "manufacturer": "GLEDOPTO",
        "model": "GL-C-009",
        "class": "zigpy.device.Device",
    }

    assert_signature_matches_quirk(zhaquirks.gledopto.glc009.GLC009, signature)


@pytest.fixture
def glc008p(zigpy_device_from_v2_quirk):
    """Return a quirked GL-C-008P."""
    return zigpy_device_from_v2_quirk(
        "GLEDOPTO",
        "GL-C-008P",
        endpoint_ids=[11],
        cluster_ids={
            11: {
                Basic.cluster_id: ClusterType.Server,
                Identify.cluster_id: ClusterType.Server,
                Groups.cluster_id: ClusterType.Server,
                Scenes.cluster_id: ClusterType.Server,
                OnOff.cluster_id: ClusterType.Server,
                LevelControl.cluster_id: ClusterType.Server,
                Color.cluster_id: ClusterType.Server,
            }
        },
    )


def _set_range(device, warmest_k, coldest_k):
    config = device.endpoints[11].gledopto_range_config
    config.update_attribute(config.AttributeDefs.warmest_k.id, warmest_k)
    config.update_attribute(config.AttributeDefs.coldest_k.id, coldest_k)


def test_glc008p_entities(glc008p):
    """Test the clusters and configuration entities of the GL-C-008P."""
    endpoint = glc008p.endpoints[11]
    assert isinstance(endpoint.light_color, GledoptoColorCluster)
    assert isinstance(endpoint.gledopto_range_config, GledoptoColorRangeConfigCluster)
    assert isinstance(
        endpoint.gledopto_color_loop_config, GledoptoColorLoopConfigCluster
    )

    entry = DEVICE_REGISTRY.match_entry(glc008p)
    entities = {
        metadata.resolved_unique_id_suffix: metadata
        for metadata in entry.zha_device_factory.quirk_definition.entity_metadata
    }
    assert set(entities) == {"warmest_k", "coldest_k", "color_loop_time"}
    assert entities["warmest_k"].unit == UnitOfTemperature.KELVIN
    assert entities["coldest_k"].unit == UnitOfTemperature.KELVIN
    assert entities["color_loop_time"].unit == UnitOfTime.SECONDS
    assert entities["color_loop_time"].step == 7


def test_glc008p_default_range_is_transparent(glc008p):
    """Test that the default range leaves native values unchanged."""
    color = glc008p.endpoints[11].light_color
    assert color.get(Color.AttributeDefs.color_temp_physical_min.name) == 154
    assert color.get(Color.AttributeDefs.color_temp_physical_max.name) == 500

    for mireds in (154, 250, 370, 500):
        color.update_attribute(Color.AttributeDefs.color_temperature.id, mireds)
        assert color.get(Color.AttributeDefs.color_temperature.name) == mireds


def test_glc008p_configured_range(glc008p):
    """Test that a configured range is mapped onto the native range."""
    _set_range(glc008p, 2700, 6500)
    color = glc008p.endpoints[11].light_color

    assert color.get(Color.AttributeDefs.color_temp_physical_min.name) == 154
    assert color.get(Color.AttributeDefs.color_temp_physical_max.name) == 370

    color.update_attribute(Color.AttributeDefs.color_temperature.id, 500)
    assert color.get(Color.AttributeDefs.color_temperature.name) == 370
    color.update_attribute(Color.AttributeDefs.color_temperature.id, 327)
    assert color.get(Color.AttributeDefs.color_temperature.name) == 262


def test_glc008p_start_up_previous_is_not_mapped(glc008p):
    """Test that the start-up value for the previous temperature is kept."""
    _set_range(glc008p, 2700, 6500)
    color = glc008p.endpoints[11].light_color

    color.update_attribute(Color.AttributeDefs.start_up_color_temperature.id, 0xFFFF)
    assert color.get(Color.AttributeDefs.start_up_color_temperature.name) == 0xFFFF


async def test_glc008p_read_attributes(glc008p):
    """Test that attribute reads return values in the configured range."""
    _set_range(glc008p, 2700, 6500)
    color = glc008p.endpoints[11].light_color
    color.update_attribute(Color.AttributeDefs.color_temperature.id, 500)

    success, _ = await color.read_attributes(
        [
            Color.AttributeDefs.color_temperature.name,
            Color.AttributeDefs.color_temp_physical_max.name,
        ],
        only_cache=True,
    )
    assert success[Color.AttributeDefs.color_temperature.name] == 370
    assert success[Color.AttributeDefs.color_temp_physical_max.name] == 370


async def test_glc008p_write_start_up_color_temperature(glc008p):
    """Test that start-up writes are mapped, except for the previous value."""
    _set_range(glc008p, 2700, 6500)
    color = glc008p.endpoints[11].light_color
    start_up = Color.AttributeDefs.start_up_color_temperature.name

    with mock.patch.object(
        Color, "write_attributes", new=mock.AsyncMock()
    ) as write_attributes:
        await color.write_attributes({start_up: 370})
        await color.write_attributes({start_up: 0xFFFF})

    assert write_attributes.await_args_list[0].args[0] == {start_up: 500}
    assert write_attributes.await_args_list[1].args[0] == {start_up: 0xFFFF}


async def test_glc008p_color_temp_commands(glc008p):
    """Test that color temperature commands are mapped to the native range."""
    _set_range(glc008p, 2700, 6500)
    color = glc008p.endpoints[11].light_color

    with mock.patch.object(Color, "command", new=mock.AsyncMock()) as command:
        await color.move_to_color_temp(370, 10)
        await color.step_color_temp(
            step_mode=Color.StepMode.Up,
            step_size=55,
            transition_time=5,
            color_temp_min_mireds=0,
            color_temp_max_mireds=370,
        )
        await color.move_color_temp(Color.MoveMode.Up, 108, 154, 262)

    assert command.await_args_list[0].kwargs == {
        "manufacturer": None,
        "expect_reply": True,
        "color_temp_mireds": 500,
        "transition_time": 10,
    }
    assert command.await_args_list[1].kwargs == {
        "manufacturer": None,
        "expect_reply": True,
        "step_mode": Color.StepMode.Up,
        "step_size": 88,
        "transition_time": 5,
        "color_temp_min_mireds": 0,
        "color_temp_max_mireds": 500,
    }
    assert command.await_args_list[2].kwargs == {
        "manufacturer": None,
        "expect_reply": True,
        "move_mode": Color.MoveMode.Up,
        "rate": 173,
        "color_temp_min_mireds": 154,
        "color_temp_max_mireds": 327,
    }


async def test_glc008p_color_loop_start_uses_configured_time(glc008p):
    """Test that starting the color loop uses the configured loop time."""
    color = glc008p.endpoints[11].light_color

    with mock.patch.object(Color, "command", new=mock.AsyncMock()) as command:
        await color.color_loop_set(
            update_flags=Color.ColorLoopUpdateFlags.Action
            | Color.ColorLoopUpdateFlags.Direction
            | Color.ColorLoopUpdateFlags.Time,
            action=Color.ColorLoopAction.Activate_from_current_hue,
            direction=Color.ColorLoopDirection.Increment,
            time=7,
            start_hue=0,
        )
        await color.color_loop_set(
            update_flags=Color.ColorLoopUpdateFlags.Action,
            action=Color.ColorLoopAction.Deactivate,
            direction=Color.ColorLoopDirection.Decrement,
            time=0,
            start_hue=0,
        )

    assert command.await_args_list[0].kwargs["time"] == COLOR_LOOP_DEFAULT_TIME
    # Stopping the loop is passed through unchanged
    assert command.await_args_list[1].kwargs["time"] == 0


async def test_glc008p_color_loop_time_inactive(glc008p):
    """Test that changing the loop time while inactive sends nothing."""
    config = glc008p.endpoints[11].gledopto_color_loop_config

    with mock.patch.object(Color, "command", new=mock.AsyncMock()) as command:
        await config.write_attributes({config.AttributeDefs.color_loop_time.name: 11})

    command.assert_not_awaited()
    assert config.get(config.AttributeDefs.color_loop_time.name) == 11


async def test_glc008p_color_loop_time_active(glc008p):
    """Test that changing the loop time is applied to a running loop."""
    endpoint = glc008p.endpoints[11]
    config = endpoint.gledopto_color_loop_config
    color = endpoint.light_color
    color.update_attribute(Color.AttributeDefs.color_loop_active.id, 1)
    color.update_attribute(
        Color.AttributeDefs.color_loop_direction.id,
        Color.ColorLoopDirection.Increment,
    )

    with mock.patch.object(Color, "command", new=mock.AsyncMock()) as command:
        await config.write_attributes({config.AttributeDefs.color_loop_time.name: 39})

    command.assert_awaited_once_with(
        Color.ServerCommandDefs.color_loop_set.id,
        manufacturer=None,
        expect_reply=True,
        update_flags=Color.ColorLoopUpdateFlags.Action
        | Color.ColorLoopUpdateFlags.Time,
        action=Color.ColorLoopAction.Activate_from_current_hue,
        direction=Color.ColorLoopDirection.Increment,
        time=39,
        start_hue=0,
    )
