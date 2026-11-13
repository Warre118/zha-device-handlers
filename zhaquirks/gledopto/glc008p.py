"""Custom ZHA quirk for GLEDOPTO GL-C-008P RGB+CCT."""

from __future__ import annotations

import logging

import zigpy.types as t
from zigpy.typing import UNDEFINED
from zigpy.zcl.clusters.lighting import Color
from zigpy.zcl.foundation import (
    BaseAttributeDefs,
    BaseCommandDefs,
    Status,
    ZCLAttributeDef,
    ZCLCommandDef,
)

from zhaquirks import LocalDataCluster
from zhaquirks.builder import QuirkBuilder, UnitOfTime
from zhaquirks.clusters import CustomCluster


_LOGGER = logging.getLogger(__name__)

MANUFACTURER = "GLEDOPTO"
MODEL = "GL-C-008P"
ENDPOINT_ID = 11

# Native limits reported by the device.
NATIVE_MIN_MIREDS = 154
NATIVE_MAX_MIREDS = 500

DEFAULT_WARMEST_K = 2000
DEFAULT_COLDEST_K = 6500

KELVIN_MIN = 1000
KELVIN_MAX = 10000

COLOR_LOOP_DEFAULT_SECONDS = 25
COLOR_LOOP_STEP_SECONDS = 7
COLOR_LOOP_MIN_SECONDS = 4
COLOR_LOOP_MAX_SECONDS = 60

_COLOR_TEMP_ATTR_IDS = frozenset(
    {
        Color.AttributeDefs.color_temperature.id,
        Color.AttributeDefs.start_up_color_temperature.id,
    }
)

_WRITABLE_COLOR_TEMP_ATTR_IDS = frozenset(
    {
        Color.AttributeDefs.start_up_color_temperature.id,
    }
)

_MISSING = object()


def _coerce_kelvin(value: object, default: int) -> int:
    try:
        kelvin = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return default

    return max(KELVIN_MIN, min(KELVIN_MAX, kelvin))


def _kelvin_range_to_mireds(
    warmest_k: object,
    coldest_k: object,
) -> tuple[int, int]:
    warmest = _coerce_kelvin(warmest_k, DEFAULT_WARMEST_K)
    coldest = _coerce_kelvin(coldest_k, DEFAULT_COLDEST_K)

    min_mireds = round(1_000_000 / coldest)
    max_mireds = round(1_000_000 / warmest)

    if min_mireds > max_mireds:
        min_mireds, max_mireds = max_mireds, min_mireds

    if min_mireds == max_mireds:
        return NATIVE_MIN_MIREDS, NATIVE_MAX_MIREDS

    return min_mireds, max_mireds


# -------------------------------------------------------------------
# Local CCT configuration
# -------------------------------------------------------------------


class GledoptoRangeConfigCluster(LocalDataCluster):
    """Local per-device CCT range settings."""

    cluster_id = 0xFC01
    ep_attribute = "gledopto_range_config"

    _DEFAULT_VALUES = {
        0x0000: DEFAULT_WARMEST_K,
        0x0001: DEFAULT_COLDEST_K,
    }

    class AttributeDefs(BaseAttributeDefs):
        """Attributes for virtual Kelvin range configuration."""

        warmest_k = ZCLAttributeDef(
            id=0x0000,
            type=t.uint16_t,
        )
        coldest_k = ZCLAttributeDef(
            id=0x0001,
            type=t.uint16_t,
        )

    @property
    def mireds_range(self) -> tuple[int, int]:
        warmest = self.get(self.AttributeDefs.warmest_k) or DEFAULT_WARMEST_K
        coldest = self.get(self.AttributeDefs.coldest_k) or DEFAULT_COLDEST_K

        return _kelvin_range_to_mireds(warmest, coldest)


# -------------------------------------------------------------------
# Local Color Loop controls
# -------------------------------------------------------------------


class GledoptoColorLoopControlCluster(LocalDataCluster):
    """Local Color Loop speed setting and controls."""

    cluster_id = 0xFC02
    ep_attribute = "gledopto_color_loop_control"

    _DEFAULT_VALUES = {
        0x0000: COLOR_LOOP_DEFAULT_SECONDS,
    }

    class AttributeDefs(BaseAttributeDefs):
        """Attributes for local Color Loop controls."""

        color_loop_speed = ZCLAttributeDef(
            id=0x0000,
            type=t.uint16_t,
            access="r",
        )

    class ServerCommandDefs(BaseCommandDefs):
        """Commands for adjusting Color Loop speed."""

        speed_up = ZCLCommandDef(id=0x00, schema={})
        speed_down = ZCLCommandDef(id=0x01, schema={})

    async def command(
        self,
        command_id,
        *args,
        manufacturer=None,
        expect_reply=True,
        **kwargs,
    ):
        current = int(
            self.get(self.AttributeDefs.color_loop_speed)
            or COLOR_LOOP_DEFAULT_SECONDS
        )

        if command_id == self.ServerCommandDefs.speed_up.id:
            new_speed = current + COLOR_LOOP_STEP_SECONDS
        elif command_id == self.ServerCommandDefs.speed_down.id:
            new_speed = current - COLOR_LOOP_STEP_SECONDS
        else:
            return None, Status.UNSUP_CLUSTER_COMMAND

        if not COLOR_LOOP_MIN_SECONDS <= new_speed <= COLOR_LOOP_MAX_SECONDS:
            return None, Status.SUCCESS

        self._update_attribute(
            self.AttributeDefs.color_loop_speed.id,
            new_speed,
        )

        _LOGGER.info("Color Loop speed changed to %s seconds", new_speed)

        color_cluster = self.endpoint.in_clusters.get(Color.cluster_id)

        if color_cluster is not None:
            active = color_cluster.get(Color.AttributeDefs.color_loop_active)

            if active is not None and int(active) != 0:
                direction = color_cluster.get(
                    Color.AttributeDefs.color_loop_direction
                )
                direction = int(direction or 0)

                _LOGGER.info(
                    "Updating active Color Loop: speed=%s seconds, direction=%s",
                    new_speed,
                    direction,
                )

                return await color_cluster.command(
                    Color.ServerCommandDefs.color_loop_set.id,
                    update_flags=0x05,
                    action=0x02,
                    direction=direction,
                    time=new_speed,
                    start_hue=0,
                    manufacturer=manufacturer,
                    expect_reply=expect_reply,
                )

        # A successful local setting change must still return a valid ZCL result
        # when the Color Loop is inactive (or no Color cluster is available).
        return None, Status.SUCCESS


# -------------------------------------------------------------------
# Color Control cluster
# -------------------------------------------------------------------


class GledoptoColorRangeCluster(CustomCluster, Color):
    """Map the configurable virtual CCT range to native mireds."""

    def _virtual_range(self) -> tuple[int, int]:
        config_cluster = self.endpoint.in_clusters.get(
            GledoptoRangeConfigCluster.cluster_id
        )

        if config_cluster is not None:
            return config_cluster.mireds_range

        return _kelvin_range_to_mireds(
            DEFAULT_WARMEST_K,
            DEFAULT_COLDEST_K,
        )

    @staticmethod
    def _virtual_to_native(mireds, virtual_min, virtual_max):
        if mireds is None:
            return None

        span = virtual_max - virtual_min
        ratio = (
            0.0
            if span == 0
            else (float(mireds) - virtual_min) / span
        )
        ratio = max(0.0, min(1.0, ratio))

        return round(
            NATIVE_MIN_MIREDS
            + ratio * (NATIVE_MAX_MIREDS - NATIVE_MIN_MIREDS)
        )

    @staticmethod
    def _native_to_virtual(mireds, virtual_min, virtual_max):
        if mireds is None:
            return None

        native_span = NATIVE_MAX_MIREDS - NATIVE_MIN_MIREDS
        ratio = (float(mireds) - NATIVE_MIN_MIREDS) / native_span
        ratio = max(0.0, min(1.0, ratio))

        return round(
            virtual_min + ratio * (virtual_max - virtual_min)
        )

    def get(self, key, default=None):
        """Return virtual CCT values."""

        try:
            attr = self.find_attribute(key)
        except (KeyError, ValueError):
            return super().get(key, default)

        if attr.id == Color.AttributeDefs.color_temp_physical_min.id:
            return self._virtual_range()[0]

        if attr.id == Color.AttributeDefs.color_temp_physical_max.id:
            return self._virtual_range()[1]

        value = super().get(key, _MISSING)

        if value is _MISSING:
            return default

        if value in (None, 0) or attr.id not in _COLOR_TEMP_ATTR_IDS:
            return value

        return self._native_to_virtual(
            value,
            *self._virtual_range(),
        )

    async def read_attributes(
        self,
        attributes,
        allow_cache=False,
        only_cache=False,
        manufacturer=UNDEFINED,
        **kwargs,
    ):
        """Return virtual CCT values in attribute reads."""

        success, failure = await super().read_attributes(
            attributes,
            allow_cache=allow_cache,
            only_cache=only_cache,
            manufacturer=manufacturer,
            **kwargs,
        )

        for attr_id in list(success):
            try:
                attr = self.find_attribute(attr_id)
            except (KeyError, ValueError):
                continue

            value = success[attr_id]

            if attr.id == Color.AttributeDefs.color_temp_physical_min.id:
                success[attr_id] = self._virtual_range()[0]

            elif attr.id == Color.AttributeDefs.color_temp_physical_max.id:
                success[attr_id] = self._virtual_range()[1]

            elif value not in (None, 0) and attr.id in _COLOR_TEMP_ATTR_IDS:
                success[attr_id] = self._native_to_virtual(
                    value,
                    *self._virtual_range(),
                )

        return success, failure

    async def write_attributes(
        self,
        attributes,
        manufacturer=UNDEFINED,
        *,
        update_cache=True,
        **kwargs,
    ):
        """Translate startup CCT writes to native mireds."""

        converted = dict(attributes)

        for attr_id, value in attributes.items():
            if value in (None, 0):
                continue

            try:
                attr = self.find_attribute(attr_id)
            except (KeyError, ValueError):
                continue

            if attr.id in _WRITABLE_COLOR_TEMP_ATTR_IDS:
                converted[attr_id] = self._virtual_to_native(
                    value,
                    *self._virtual_range(),
                )

        return await super().write_attributes(
            converted,
            manufacturer=manufacturer,
            update_cache=update_cache,
            **kwargs,
        )

    async def command(
        self,
        command_id,
        *args,
        manufacturer=None,
        expect_reply=True,
        **kwargs,
    ):
        """Translate CCT commands and configure Color Loop speed."""

        kwargs = dict(kwargs)

        if command_id == Color.ServerCommandDefs.color_loop_set.id:
            names = (
                "update_flags",
                "action",
                "direction",
                "time",
                "start_hue",
            )

            command_values = dict(zip(names, args))
            command_values.update(kwargs)

            flags = int(command_values.get("update_flags", 0))
            action = int(command_values.get("action", -1))

            action_flag = 0x01
            time_flag = 0x04

            # Configure the requested speed when starting the loop.
            if flags & action_flag and action in (0x01, 0x02):
                control_cluster = self.endpoint.in_clusters.get(
                    GledoptoColorLoopControlCluster.cluster_id
                )

                if control_cluster is not None:
                    speed = int(
                        control_cluster.get(
                            GledoptoColorLoopControlCluster.AttributeDefs.color_loop_speed
                        )
                        or COLOR_LOOP_DEFAULT_SECONDS
                    )

                    command_values["time"] = speed
                    command_values["update_flags"] = flags | time_flag

                    _LOGGER.info("Starting Color Loop with speed %s seconds", speed)

            return await super().command(
                command_id,
                manufacturer=manufacturer,
                expect_reply=expect_reply,
                **command_values,
            )

        # Move to color temperature.
        if command_id == Color.ServerCommandDefs.move_to_color_temp.id:
            virtual_min, virtual_max = self._virtual_range()

            if args:
                args = (
                    self._virtual_to_native(
                        args[0],
                        virtual_min,
                        virtual_max,
                    ),
                    *args[1:],
                )
            elif "color_temp_mireds" in kwargs:
                kwargs["color_temp_mireds"] = self._virtual_to_native(
                    kwargs["color_temp_mireds"],
                    virtual_min,
                    virtual_max,
                )

        # Move / step color temperature.
        elif command_id in (
            Color.ServerCommandDefs.move_color_temp.id,
            Color.ServerCommandDefs.step_color_temp.id,
        ):
            virtual_min, virtual_max = self._virtual_range()
            args = list(args)

            if command_id == Color.ServerCommandDefs.move_color_temp.id:
                positional_indexes = (2, 3)
            else:
                positional_indexes = (3, 4)

            for index, name in zip(
                positional_indexes,
                ("color_temp_min_mireds", "color_temp_max_mireds"),
            ):
                if len(args) > index:
                    args[index] = self._virtual_to_native(
                        args[index],
                        virtual_min,
                        virtual_max,
                    )
                elif name in kwargs:
                    kwargs[name] = self._virtual_to_native(
                        kwargs[name],
                        virtual_min,
                        virtual_max,
                    )

        return await super().command(
            command_id,
            *args,
            manufacturer=manufacturer,
            expect_reply=expect_reply,
            **kwargs,
        )


# -------------------------------------------------------------------
# Quirk registration and Home Assistant entities
# -------------------------------------------------------------------

builder = (
    QuirkBuilder(MANUFACTURER, MODEL)
    .replaces(
        GledoptoColorRangeCluster,
        endpoint_id=ENDPOINT_ID,
    )
    .adds(
        GledoptoRangeConfigCluster,
        endpoint_id=ENDPOINT_ID,
    )
    .adds(
        GledoptoColorLoopControlCluster,
        endpoint_id=ENDPOINT_ID,
    )
    .number(
        attribute_name="warmest_k",
        cluster_id=GledoptoRangeConfigCluster.cluster_id,
        endpoint_id=ENDPOINT_ID,
        min_value=KELVIN_MIN,
        max_value=KELVIN_MAX,
        step=50,
        unit="K",
        mode="box",
        translation_key="cct_warmest_k",
        fallback_name="CCT warmest (K)",
    )
    .number(
        attribute_name="coldest_k",
        cluster_id=GledoptoRangeConfigCluster.cluster_id,
        endpoint_id=ENDPOINT_ID,
        min_value=KELVIN_MIN,
        max_value=KELVIN_MAX,
        step=50,
        unit="K",
        mode="box",
        translation_key="cct_coldest_k",
        fallback_name="CCT coldest (K)",
    )
    .sensor(
        attribute_name="color_loop_speed",
        cluster_id=GledoptoColorLoopControlCluster.cluster_id,
        endpoint_id=ENDPOINT_ID,
        unit=UnitOfTime.SECONDS,
        translation_key="color_loop_speed",
        fallback_name="Color Loop Speed",
    )
    .command_button(
        command_name=GledoptoColorLoopControlCluster.ServerCommandDefs.speed_up.name,
        cluster_id=GledoptoColorLoopControlCluster.cluster_id,
        endpoint_id=ENDPOINT_ID,
        translation_key="color_loop_speed_up",
        fallback_name="Color Loop Speed +7s",
        unique_id_suffix="color_loop_speed_up",
    )
    .command_button(
        command_name=GledoptoColorLoopControlCluster.ServerCommandDefs.speed_down.name,
        cluster_id=GledoptoColorLoopControlCluster.cluster_id,
        endpoint_id=ENDPOINT_ID,
        translation_key="color_loop_speed_down",
        fallback_name="Color Loop Speed -7s",
        unique_id_suffix="color_loop_speed_down",
    )
)

builder.add_to_registry()
