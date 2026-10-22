# SPDX-License-Identifier: GPL-3.0-only
"""Estimated burst reserve, independent of MeltCalc's steady-state model.

The attributed geometry is a prior, never a measurement of usable molten
volume. Slider fractions and recharge policy are Orca Flow heuristics.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    base=Path(__file__).resolve().parent
    resource=base/'resources/meltcalc/hotends.json'
    if not resource.exists():resource=base/'plugin/orca_flow/resources/meltcalc/hotends.json'
    return json.loads(resource.read_text(encoding='utf-8'))


def burst_settings(cfg,recovery_horizon_s):
    identity=cfg.get('burst_preset','legacy_average')
    aggression=cfg.get('burst_aggressiveness',50)
    if isinstance(aggression,bool) or not isinstance(aggression,(int,float)) or not math.isfinite(aggression) or not 0<=aggression<=100:
        raise ValueError('Burst aggressiveness must be a number from 0 to 100')
    if identity=='legacy_average':return None
    geometry=None
    if identity!='none':
        geometry=next((p for p in catalog()['hotends'] if p['id']==identity),None)
        if not geometry:raise ValueError('Unknown hotend burst preset')
        if geometry['filament_paths']!=1 or abs(geometry['filament_diameter_mm']-1.75)>.001:
            raise ValueError('Burst presets currently support one 1.75 mm feed path only')
    volume=0 if geometry is None else math.pi*(geometry['filament_diameter_mm']/2)**2*geometry['physical_length_mm']
    # 0/50/100 maps to 0/25/50% of the physical filament-equivalent inventory.
    # This is a tuning allowance, not an identified melt reservoir fraction.
    capacity=volume*aggression/200
    return {'preset_id':identity,'preset_name':None if geometry is None else geometry['manufacturer']+' '+geometry['name'],
            'aggressiveness':aggression,'physical_inventory_proxy_mm3':volume,
            'estimated_reserve_capacity_mm3':capacity,'recovery_horizon_s':recovery_horizon_s,
            'max_recharge_rate_mm3_s':capacity/recovery_horizon_s,
            'spare_capacity_recharge_fraction':.25,'initial_reserve_mm3':0,
            'geometry':geometry,'estimated':True,'independently_validated':False,
            'source_url':catalog()['source_url'] if geometry else None,
            'source_commit':catalog()['source_commit'] if geometry else None,
            'license':'CC-BY-4.0' if geometry else None,
            'policy':'Orca Flow heuristic; no MeltCalc burst-capacity claim or endorsement'}


def sustainable_flow(curve,temperature_c):
    """Forward capacity curve, consistent with inversion and its flow headroom."""
    points=curve.points
    if temperature_c<points[0].temperature_c:return 0.0
    for lower,upper in zip(points,points[1:]):
        if temperature_c<upper.temperature_c:
            flow=lower.max_reliable_flow_mm3_s
            if curve.interpolation=='linear':
                flow+=(upper.max_reliable_flow_mm3_s-flow)*(temperature_c-lower.temperature_c)/(upper.temperature_c-lower.temperature_c)
            return flow*(1-curve.headroom)
    return points[-1].max_reliable_flow_mm3_s*(1-curve.headroom)


@dataclass
class BurstBudget:
    capacity_mm3: float
    max_recharge_rate_mm3_s: float
    remaining_mm3: float=0.0

    def copy(self):return BurstBudget(self.capacity_mm3,self.max_recharge_rate_mm3_s,self.remaining_mm3)

    def advance(self,flow_mm3_s,sustainable_mm3_s,seconds):
        """Return unsupported excess volume; carry reserve between commands."""
        excess=max(0,flow_mm3_s-sustainable_mm3_s)*seconds
        unfunded=max(0,excess-self.remaining_mm3)
        self.remaining_mm3=max(0,self.remaining_mm3-excess)
        spare=max(0,sustainable_mm3_s-flow_mm3_s)
        refill=min(self.max_recharge_rate_mm3_s,spare*.25)*seconds
        self.remaining_mm3=min(self.capacity_mm3,self.remaining_mm3+refill)
        return unfunded
