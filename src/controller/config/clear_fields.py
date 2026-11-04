"""clear_<field> flags for ConfigUpdateRequest, keyed by facet."""
from __future__ import annotations

from controller.config.facets import aprs as aprs_facet
from controller.config.facets import gpio_fan as gpio_fan_facet
from controller.config.facets import identity_timing as identity_timing_facet
from controller.config.facets import link_radio as link_radio_facet
from controller.config.facets import net as net_facet
from controller.config.facets import radio_audio as radio_audio_facet
from controller.config.facets import remote_base as remote_base_facet
from controller.config.facets import weather as weather_facet

FACET_CLEAR_FIELDS: dict[str, tuple[str, ...]] = {
    "identity_timing": identity_timing_facet.CLEAR_FIELDS,
    "radio_audio": radio_audio_facet.CLEAR_FIELDS,
    "link_radio": link_radio_facet.CLEAR_FIELDS,
    "remote_base": remote_base_facet.CLEAR_FIELDS,
    "aprs": aprs_facet.CLEAR_FIELDS,
    "weather": weather_facet.CLEAR_FIELDS,
    "net": net_facet.CLEAR_FIELDS,
    "gpio_fan": gpio_fan_facet.CLEAR_FIELDS,
}

ALL_CLEAR_FIELDS: tuple[str, ...] = tuple(
    name for names in FACET_CLEAR_FIELDS.values() for name in names
)
