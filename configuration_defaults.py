# SPDX-License-Identifier: GPL-3.0-only
"""Public defaults contain no printer identity or recorded calibration."""
from tool_profiles import fresh_profile


def default_configuration():
    return {**fresh_profile(0,''),'schema_version':1,'printer_name':'','moonraker_url':'','moonraker_ip':'','moonraker_port':7125,
        'output_mode':'analysis','heating_lead_s':0,'cooling_hold_s':0,
        'positioning_order':'y_then_x',
        'scheduler':'hybrid_predictive','burst_aggressiveness':50,'tool_mode':'single','tool_count':4,'tool_profiles':{},
        'motion':{'max_velocity_mm_s':300,'max_accel_mm_s2':3000,
                  'square_corner_velocity_mm_s':5,'minimum_cruise_ratio':.5}}
