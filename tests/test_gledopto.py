"""Tests for GLEDOPTO quirks."""

from unittest import mock

import pytest
from zigpy.zcl import ClusterType, foundation
from zigpy.zcl.clusters.lighting import Color

import zhaquirks.gledopto.glc009
from zhaquirks.gledopto.glc008p import (
    COLOR_LOOP_DEFAULT_SECONDS,
    GledoptoColorLoopControlCluster,
    GledoptoColorRangeCluster,
    GledoptoRangeConfigCluster,
)


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
def gledopto_glc008p(zigpy_device_from_v2_quirk):
    """Return a quirked GL-C-008P with its endpoint 11 clusters."""
    cluster_ids = {
        0x0000: ClusterType.Server,
        0x0003: ClusterType.Server,
        0x0004: ClusterType.Server,
        0x0005: ClusterType.Server,
        0x0006: ClusterType.Server,
        0x0008: ClusterType.Server,
        Color.cluster_id: ClusterType.Server,
        0x1000: ClusterType.Server,
        0x0019: ClusterType.Client,
    }

    return zigpy_device_from_v2_quirk(
        "GLEDOPTO",
        "GL-C-008P",
        endpoint_ids=[11],
        cluster_ids={11: cluster_ids},
    )


def test_gledopto_glc008p_clusters_and_defaults(gledopto_glc008p):
    """Test GL-C-008P exposes the replacement and local control clusters."""
    endpoint = gledopto_glc008p.endpoints[11]

    assert isinstance(endpoint.color, GledoptoColorRangeCluster)
    assert isinstance(endpoint.gledopto_range_config, GledoptoRangeConfigCluster)
    assert isinstance(
        endpoint.gledopto_color_loop_control,
        GledoptoColorLoopControlCluster,
    )

    assert endpoint.gledopto_range_config.get("warmest_k") == 2000
    assert endpoint.gledopto_range_config.get("coldest_k") == 6500
    assert endpoint.gledopto_color_loop_control.get("color_loop_speed") == (
        COLOR_LOOP_DEFAULT_SECONDS
    )


def test_gledopto_glc008p_kelvin_range_mapping(gledopto_glc008p):
    """Test configurable Kelvin limits are exposed as virtual mired limits."""
    endpoint = gledopto_glc008p.endpoints[11]
    config = endpoint.gledopto_range_config
    color = endpoint.color

    config._update_attribute(config.AttributeDefs.warmest_k.id, 2200)
    config._update_attribute(config.AttributeDefs.coldest_k.id, 6000)

    assert config.mireds_range == (167, 455)
    assert color.get(Color.AttributeDefs.color_temp_physical_min) == 167
    assert color.get(Color.AttributeDefs.color_temp_physical_max) == 455

    color._update_attribute(Color.AttributeDefs.color_temperature.id, 200)
    assert color.get(Color.AttributeDefs.color_temperature) == 205


async def test_gledopto_glc008p_color_loop_speed_inactive(gledopto_glc008p):
    """Speed changes while Color Loop is inactive return a successful ZCL result."""
    control = gledopto_glc008p.endpoints[11].gledopto_color_loop_control

    result = await control.command(control.ServerCommandDefs.speed_up.id)
    assert result.command_id == control.ServerCommandDefs.speed_up.id
    assert result.status == foundation.Status.SUCCESS
    assert control.get("color_loop_speed") == 32

    result = await control.command(control.ServerCommandDefs.speed_down.id)
    assert result.command_id == control.ServerCommandDefs.speed_down.id
    assert result.status == foundation.Status.SUCCESS
    assert control.get("color_loop_speed") == 25


async def test_gledopto_glc008p_color_loop_speed_active(gledopto_glc008p):
    """Changing speed while active updates the running Color Loop."""
    endpoint = gledopto_glc008p.endpoints[11]
    control = endpoint.gledopto_color_loop_control
    color = endpoint.color

    color._update_attribute(Color.AttributeDefs.color_loop_active.id, 1)
    color._update_attribute(Color.AttributeDefs.color_loop_direction.id, 1)

    color_response = foundation.GENERAL_COMMANDS[
        foundation.GeneralCommand.Default_Response
    ].schema(
        command_id=Color.ServerCommandDefs.color_loop_set.id,
        status=foundation.Status.SUCCESS,
    )

    with mock.patch.object(
        color,
        "command",
        new=mock.AsyncMock(return_value=color_response),
    ) as color_command:
        result = await control.command(control.ServerCommandDefs.speed_up.id)

    assert result.command_id == control.ServerCommandDefs.speed_up.id
    assert result.status == foundation.Status.SUCCESS
    color_command.assert_awaited_once_with(
        Color.ServerCommandDefs.color_loop_set.id,
        update_flags=0x05,
        action=0x02,
        direction=1,
        time=32,
        start_hue=0,
        manufacturer=None,
        expect_reply=True,
    )


async def test_gledopto_glc008p_start_uses_saved_color_loop_speed(gledopto_glc008p):
    """Starting the Color Loop overrides the command time with the saved speed."""
    endpoint = gledopto_glc008p.endpoints[11]
    control = endpoint.gledopto_color_loop_control
    color = endpoint.color
    control._update_attribute(control.AttributeDefs.color_loop_speed.id, 39)

    with mock.patch.object(
        Color,
        "command",
        new=mock.AsyncMock(return_value=(None, foundation.Status.SUCCESS)),
    ) as parent_command:
        result = await color.command(
            Color.ServerCommandDefs.color_loop_set.id,
            update_flags=0x01,
            action=0x02,
            direction=0,
            time=25,
            start_hue=0,
        )

    assert result == (None, foundation.Status.SUCCESS)
    parent_command.assert_awaited_once_with(
        Color.ServerCommandDefs.color_loop_set.id,
        manufacturer=None,
        expect_reply=True,
        update_flags=0x05,
        action=0x02,
        direction=0,
        time=39,
        start_hue=0,
    )
