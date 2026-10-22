# SPDX-License-Identifier: GPL-3.0-only
"""Independent tool calibration records; never access printer hardware."""
from copy import deepcopy
from predictive_thermal import response_is_configured

PROFILE_FIELDS=('material','filament_type','nozzle_diameter_mm','hotend','physical_tool','logical_tool',
    'min_temperature_c','max_temperature_c','max_flow_mm3_s','headroom_percent','interpolation','points',
    'thermal_response','burst_preset','burst_aggressiveness','heater_name','fan_name','tool_change_style',
    'calibration_flow_mm3_s','calibration_baseline_c','calibration_peak_c','calibration_tolerance_percent','tool_configured','calibration_origin')
MAPPING_FIELDS=('physical_tool','logical_tool','heater_name','fan_name','tool_change_style')


def fresh_profile(tool,printer_name):
    binding={'printer_name':printer_name,'physical_tool':tool,'material':'PLA',
             'nozzle_diameter_mm':.4,'hotend':'Unconfigured hotend'}
    return {**{k:v for k,v in binding.items() if k!='printer_name'},'logical_tool':tool,'filament_type':'PLA',
        'min_temperature_c':210,'max_temperature_c':240,'max_flow_mm3_s':1,'headroom_percent':10,
        'interpolation':'linear','points':[{'temperature_c':210,'max_reliable_flow_mm3_s':1},
                                          {'temperature_c':240,'max_reliable_flow_mm3_s':1}],
        'thermal_response':{'name':'Unconfigured response','source':'unconfigured','configuration_state':'unconfigured','independently_validated':False,
            'binding':binding,'heating':{'delay_s':0,'time_constant_s':1},'cooling':{'delay_s':0,'time_constant_s':1}},
        'burst_preset':'none','burst_aggressiveness':0,'heater_name':'extruder' if tool==0 else f'extruder{tool}',
        'fan_name':'fan','tool_change_style':'active_only','calibration_flow_mm3_s':.5,
        'calibration_baseline_c':210,'calibration_peak_c':220,'calibration_tolerance_percent':0.5,'tool_configured':False,
        'calibration_origin':{'kind':'unconfigured','note':'Restrictive placeholder bounds, not measured flow or response.'}}


def profile_config(cfg,tool):
    profile=cfg.get('tool_profiles',{}).get(str(tool))
    if tool==cfg['physical_tool']:profile={k:deepcopy(cfg[k]) for k in PROFILE_FIELDS if k in cfg}
    if profile is None:profile=fresh_profile(tool,cfg['printer_name'])
    return {**deepcopy(cfg),**deepcopy(profile),'tool_mode':'single','tool_profiles':{}}


def copy_calibration(cfg,source_tool,destination_tool):
    if source_tool==destination_tool:raise ValueError('Choose different source and destination tools')
    if any(isinstance(t,bool) or not isinstance(t,int) or not 0<=t<cfg['tool_count'] for t in (source_tool,destination_tool)):
        raise ValueError('Copy source/destination must be a configured tool slot')
    source=profile_config(cfg,source_tool)
    if not response_is_configured(source.get('thermal_response')):
        raise ValueError('The source tool has no calibrated response. Import saved settings or complete its response calibration before copying.')
    if any(source.get(k)!=v for k,v in source['thermal_response'].get('binding',{}).items()):
        raise ValueError('The source response does not match its current filament, hotend, nozzle or tool. Load a matching calibration before copying.')
    destination=profile_config(cfg,destination_tool)
    result={k:deepcopy(source[k]) for k in PROFILE_FIELDS}
    result.update({k:deepcopy(destination[k]) for k in MAPPING_FIELDS})
    response=result['thermal_response']
    response['copied_from_binding']=deepcopy(response.get('binding'))
    response['binding']={k:result[k] if k in result else cfg[k] for k in response.get('binding',{})}
    response['independently_validated']=False
    response['source']='copied_response_candidate'
    result['tool_configured']=True
    result['calibration_origin']={'kind':'copied','source_tool':source_tool,
        'note':'Copied calibration; the destination was not independently measured.'}
    return result
