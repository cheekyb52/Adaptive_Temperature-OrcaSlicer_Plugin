"""Offline sweep of exported flow-load probe commands and real PS resplitting."""
import json
import math
from pathlib import Path

from static_probe import loaded_response_probe


def audit():
    area=math.pi*(1.75/2)**2
    commands_checked=0
    worst_duration_error=0.0
    worst_phase_undershoot=0.0
    fixtures=[]
    for hundredths in range(200,10001):
        flow=hundredths/100
        plan=loaded_response_probe(flow,240)
        assert plan['total_requested_duration_s']==55
        index=0
        duration=0.0
        for phase in plan['phases']:
            group=plan['commands'][index:index+phase['command_count']]
            index+=phase['command_count']
            actual_duration=0.0
            total_e=0.0
            for command in group:
                op,e,f=command.split()
                assert op=='G1' and e.startswith('E') and f.startswith('F')
                distance,feed=float(e[1:]),float(f[1:])
                assert 0<distance<=100 and feed>0,(flow,command)
                actual_flow=feed/60*area
                assert actual_flow<=phase['requested_flow_mm3_s']+1e-10,(flow,phase,command)
                actual_duration+=distance/(feed/60)
                total_e+=distance
                commands_checked+=1
            error=phase['duration_s']-actual_duration
            assert -1e-9<=error<0.001,(flow,phase,error)
            worst_duration_error=max(worst_duration_error,abs(error))
            worst_phase_undershoot=max(worst_phase_undershoot,phase['requested_flow_mm3_s']*phase['duration_s']-total_e*area)
            duration+=actual_duration
        assert abs(duration-55)<=0.01,(flow,duration)
        # Keep a smaller corpus for exercising the runner's actual re-split loop.
        if hundredths%100==0 or hundredths in (201,9999):fixtures.append(plan)
    return {'flow_count':9801,'range_mm3_s':[2,100],'step_mm3_s':.01,'commands_checked':commands_checked,
            'max_phase_duration_error_s':worst_duration_error,'max_phase_volume_undershoot_mm3':worst_phase_undershoot,
            'failures':0,'hardware_commands_sent':False},fixtures


if __name__=='__main__':
    out=Path(__file__).parent/'.build/rounding-audit'
    out.mkdir(parents=True,exist_ok=True)
    report,fixtures=audit()
    (out/'probe-audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (out/'probe-fixtures.json').write_text(json.dumps(fixtures),encoding='utf-8')
    print(json.dumps(report,indent=2))
