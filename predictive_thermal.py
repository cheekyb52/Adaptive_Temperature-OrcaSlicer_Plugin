# SPDX-License-Identifier: GPL-3.0-only
"""Brand-independent closed-loop sensor-response model and predictive control.

These are offline candidate predictions, not measurements of polymer melt or
validated burst capacity. No hardware access or printer-specific commands.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Response:
    delay_s: float
    time_constant_s: float

    def __post_init__(self):
        if (not math.isfinite(self.delay_s) or self.delay_s < 0 or
                not math.isfinite(self.time_constant_s) or self.time_constant_s <= 0):
            raise ValueError('Response needs a nonnegative delay and positive finite time constant')

    @property
    def horizon_s(self):
        return self.delay_s-math.log(0.05)*self.time_constant_s


def response_pair(profile):
    if not isinstance(profile, dict):
        raise ValueError('A heating and cooling response profile is required')
    def read(key):
        data = profile[key]
        if not isinstance(data, dict) or any(isinstance(data.get(k), bool) for k in ('delay_s','time_constant_s')):
            raise ValueError('Each response requires numeric delay and time constant')
        return Response(float(data['delay_s']),float(data['time_constant_s']))
    heating,cooling = read('heating'),read('cooling')
    if max(heating.horizon_s,cooling.horizon_s) > 300:
        raise ValueError('Response horizon exceeds the initial 300-second planning limit')
    return heating,cooling


def response_is_configured(profile):
    """Placeholders stay unconfigured even if an older copy changed source."""
    return (isinstance(profile,dict)
            and profile.get('source') not in (None,'','unconfigured')
            and profile.get('configuration_state')!='unconfigured'
            and str(profile.get('name','')).strip().casefold() not in (
                'unconfigured response','response calibration required'))


def profile_from_result(result,binding):
    """Adopt candidate fits from the recorder without upgrading validation status."""
    directions={}
    for transition in result.get('transitions',[]):
        if transition.get('status')!='candidate_fit':continue
        model=transition['model']
        direction='heating' if transition['to_target_c']>transition['from_target_c'] else 'cooling'
        directions[direction]={'delay_s':model['delay_s'],'time_constant_s':model['time_constant_s']}
    profile={'schema_version':1,'name':'Latest loaded response calibration',
        'source':'recorded_loaded_temperature_step_candidate','configuration_state':'configured','independently_validated':False,
        'binding':dict(binding),'heating':directions.get('heating'),'cooling':directions.get('cooling'),
        'observed_conditions':{'flow_mm3_s':result.get('commanded_flow_mm3_s'),'fan_fraction':result.get('fan_fraction')},
        'scope':'Candidate closed-loop sensor response; burst capacity and printing quality unvalidated.'}
    response_pair(profile)
    return profile


def advance_temperature(temperature, target, seconds, heating, cooling):
    """Exact first-order evolution AFTER the setpoint's transport delay."""
    response = heating if target >= temperature else cooling
    return target+(temperature-target)*math.exp(-seconds/response.time_constant_s)


class SensorPredictor:
    """Directional FOPDT approximation with ordered delayed command events."""
    def __init__(self, heating, cooling, time_s, temperature_c, initial_target_c):
        self.heating,self.cooling = heating,cooling
        self.time_s,self.temperature_c = time_s,temperature_c
        self.target_c,self.last_command_c = initial_target_c,initial_target_c
        self.pending = []

    def command(self, time_s, target_c):
        response = self.heating if target_c >= self.last_command_c else self.cooling
        effective = time_s+response.delay_s
        # Directional delays must not reorder commands. Closely spaced reversals
        # are approximated as arriving in issue order, explicitly a model limit.
        if self.pending:
            effective = max(effective,self.pending[-1][0])
        self.pending.append((effective,target_c))
        self.last_command_c = target_c

    def advance(self, to_time_s):
        if to_time_s < self.time_s-1e-9:
            raise ValueError('Predictor time cannot go backward')
        while self.pending and self.pending[0][0] <= to_time_s:
            when,target = self.pending.pop(0)
            self.temperature_c = advance_temperature(self.temperature_c,self.target_c,
                max(0,when-self.time_s),self.heating,self.cooling)
            self.time_s,self.target_c = max(when,self.time_s),target
        self.temperature_c = advance_temperature(self.temperature_c,self.target_c,
            max(0,to_time_s-self.time_s),self.heating,self.cooling)
        self.time_s = to_time_s
        return self.temperature_c

    def copy(self):
        other = SensorPredictor(self.heating,self.cooling,self.time_s,self.temperature_c,self.target_c)
        other.last_command_c = self.last_command_c
        other.pending = list(self.pending)
        return other


def choose_target(predictor, future_demands, minimum_c, maximum_c, step_s=0.5,
                  burst_budget=None,flow_forecast=None,curve=None,diagnostics=None):
    """Receding-horizon, single-control-move search over bounded setpoints.

    Each candidate is simulated through directional delay and heating/cooling
    response, assuming a constant candidate over the prediction horizon.
    Only the first control interval is committed and the plan is recomputed.
    Future undershoot costs more than overshoot. The horizon can request cooling
    BEFORE demand falls. The single-control horizon is an approximation.
No claim of optimality or melt-quality guarantees is made.
"""
    if not future_demands:
        raise ValueError('Future demand samples are required')
    if burst_budget is not None:
        from burst_model import sustainable_flow
        if curve is None or flow_forecast is None or len(flow_forecast)!=len(future_demands):
            raise ValueError('Burst prediction requires a matching flow forecast and capacity curve')
    candidates = sorted(set([minimum_c,maximum_c,predictor.last_command_c]+
                            list(range(math.ceil(minimum_c),math.floor(maximum_c)+1))))
    best = None
    for target in candidates:
        if not minimum_c <= target <= maximum_c:
            continue
        trial = predictor.copy()
        trial.command(trial.time_s,target)
        cost = 0.02*(target-predictor.last_command_c)**2
        initial_time = trial.time_s
        budget=burst_budget.copy() if burst_budget is not None else None
        shortfall=0.0
        seconds=0.0
        for i,demand in enumerate(future_demands,1):
            dt=step_s if budget is None else flow_forecast[i-1][0]
            previous=trial.temperature_c
            seconds=i*step_s if budget is None else seconds+dt
            value = trial.advance(initial_time+seconds)
            error = value-demand
            cost += (40 if error < 0 else 1)*error*error*dt
            if budget is not None:
                shortfall+=budget.advance(flow_forecast[i-1][1],sustainable_flow(curve,(previous+value)/2),dt)
        # First minimize unsupported burst volume, then track average-flow demand.
        # No candidate may expand the configured temperature bounds.
        key = (round(shortfall,8),cost,abs(target-predictor.last_command_c),target)
        if best is None or key < best[0]:
            best = key,target,shortfall
    if diagnostics is not None:diagnostics['forecast_unfunded_volume_mm3']=best[2]
    return best[1]
