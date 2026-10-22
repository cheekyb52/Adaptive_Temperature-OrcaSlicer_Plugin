# SPDX-License-Identifier: GPL-3.0-only
"""Build offline browser checks for the real page and a mocked Orca bridge."""
import json
from build_plugin import ROOT,VERSION,build
from configuration_defaults import default_configuration


def inject(page,checks):
    return page.replace('\n})();\n</script>','\n'+checks+'\n})();\n</script>')


def main():
    build()
    page=(ROOT/'dist'/f'Adaptive Temperature {VERSION}'/'Adaptive Temperature preview.html').read_text(encoding='utf-8')
    folder=ROOT/'.build/ui'
    folder.mkdir(parents=True,exist_ok=True)
    checks=r'''
    (()=>{const assert=(v,m)=>{if(!v)throw Error(m);};try{
      assert(document.title==='Adaptive Temperature','Title missing');
      assert(document.querySelectorAll('input[type=checkbox]').length===0,'Confirmation checkboxes remain');
      assert(!$('previewNotice').hidden&&$('connect').disabled&&$('run').disabled,'Disconnected page must not operate hardware');
      assert($('calibrationTolerance').value==='0.5','Default stability tolerance missing');
      assert($('positioningOrder').value==='y_then_x','Default positioning order missing');
      assert($('material').value==='PLA'&&$('filamentType').value==='PLA','Filament defaults must be editable PLA');
      assert($('moonrakerIp').value===''&&$('moonrakerPort').value==='7125','IP and default port missing');
      assert($('calibrationNotice').getClientRects().length>0,'First-use calibration notice missing');
      assert($('saveSettingsTop').form===$('settingsForm'),'Top Save does not use the settings form');
      const curveRect=$('curveChart').getBoundingClientRect();assert(curveRect.width<=400&&curveRect.height<=240,'Filament graph is oversized');
      $('moonrakerIp').value='127.0.0.2';$('moonrakerPort').value='8125';
      assert(connectionValues().moonraker_url==='http://127.0.0.2:8125','IP and custom port not combined');
      $('moonrakerIp').value='http://127.0.0.2:8125';let refused=false;try{connectionValues();}catch(e){refused=true;}assert(refused,'IP field accepted a full URL');
      $('moonrakerIp').value='';$('moonrakerPort').value='7125';
      fillConnection({...defaultConfig,moonraker_url:'127.0.0.3:8126'});
      assert($('moonrakerIp').value==='127.0.0.3'&&$('moonrakerPort').value==='8126','Old address failed to migrate');
      fillConnection(defaultConfig);
      document.querySelector('[data-page="calibrationPage"]').click();
      assert(!$('calibrationPage').hidden,'Navigation failed');
      chart($('temperatureChart'),[0,10,20],[{values:[210,220,218]},{values:[220,220,220],dash:true}],'Recorded time, s','Nozzle temperature');
      const liveRect=$('temperatureChart').getBoundingClientRect();assert(liveRect.width<=720&&liveRect.height<=240,'Live graph is oversized');
      assert($('temperatureChart').querySelector('.chart-label').getBoundingClientRect().height<20,'Live graph labels are oversized');
      $('testKind').value='LoadedThermal';$('testKind').dispatchEvent(new Event('change'));
      assert($('testPointRow').hidden&&!$('responseTestFields').hidden,'Response test fields missing');
      document.querySelector('[data-page="analysisPage"]').click();
      $('predictionChart').hidden=false;chart($('predictionChart'),[0,10,20],[{values:[220,230,210]}],'Modeled time, s','Temperature');
      const predictionRect=$('predictionChart').getBoundingClientRect();assert(predictionRect.width<=720&&predictionRect.height<=240,'Prediction graph is oversized');
      assert($('burstPreset').options.length===97,'Hotend catalog missing');
      $('burstPreset').value='lukes-lab-pika-u1';$('burstPreset').dispatchEvent(new Event('input'));
      assert($('burstEstimate').textContent.includes('21.4'),'Burst estimate failed');
      $('burstAggressiveness').value='100';$('burstAggressiveness').dispatchEvent(new Event('input'));
      assert($('burstEstimate').textContent.includes('42.8'),'Burst slider failed');
      $('material').value='Example PLA';$('hotend').value='Example hotend';$('nozzleDiameter').value='0.6';
      $('heatDelay').value='2';$('heatTau').value='3';$('coolDelay').value='4';$('coolTau').value='5';$('bindResponse').click();
      setPoints([{temperature_c:210,max_reliable_flow_mm3_s:12},{temperature_c:240,max_reliable_flow_mm3_s:30}]);$('maxFlow').value='30';
      document.querySelector('[data-page="curvePage"]').click();$('saveSettingsTop').click();
      assert($('status').textContent.includes('Preview settings updated')&&config.tool_configured,'Calibration readiness must be automatic');
      assert(!('tool_binding_confirmed' in readForm()),'Removed checkbox flag still serialized');
      $('toolMode').value='toolchanger';$('toolMode').dispatchEvent(new Event('input'));
      $('editingTool').value='1';$('editingTool').dispatchEvent(new Event('change'));
      $('copySource').value='0';assert(!$('copyCalibration').disabled,'Copy should need calibration data, not a checkbox');$('copyCalibration').click();
      assert(profileReady(readForm())&&$('heaterName').value==='extruder1','Copied response did not become ready or destination mapping changed');
      $('outputMode').value='temperature_test';$('outputMode').dispatchEvent(new Event('input'));$('saveOutput').click();
      assert($('savedOutputState').textContent.includes('commands will be inserted'),'Output save still needs a removed checkbox');
      $('editingTool').value='0';$('editingTool').dispatchEvent(new Event('change'));assert(profileReady(readForm()),'Tool switch lost calibration');
      send({command:'import_settings',config:readForm()});assert($('outputMode').value==='analysis'&&profileReady(readForm()),'Import should preserve calibration without confirmation gates');
      running=true;setEnabled();assert($('saveSettingsTop').disabled&&$('saveSettings').disabled,'Save during calibration was enabled');running=false;setEnabled();
      receive({command:'connection_error',message:'Connection timed out'});assert($('connectionStatus').textContent.includes('timed out'),'Connection error missing');
      receive({command:'printer',sample:{moonraker_url:'http://127.0.0.1:7125',klippy_state:'ready',telemetry_error:'Unknown heater'}});
      assert($('connectionStatus').textContent.includes('Connected to')&&$('connectionStatus').textContent.includes('Unknown heater'),'Connection and tool telemetry status must be distinct');
      document.body.dataset.uiCheck='passed';document.title='PASS - Adaptive Temperature UI';
    }catch(e){document.body.dataset.uiCheck='failed';document.body.dataset.uiError=e.message;document.title='FAIL - '+e.message;}})();
    '''
    (folder/'ui-check.html').write_text(inject(page,checks),encoding='utf-8')
    # The mock records requests and returns state; it cannot connect or operate hardware.
    mock="""<script>window.__messages=[];window.orca={onMessage:cb=>window.__receive=cb,postMessage:message=>{window.__messages.push(message);if(message.command==='refresh')window.__receive({command:'state',config:DEFAULTS,results:[],report:null,job:null});if(message.command==='save')window.__receive({command:'saved',config:message.config,results:[],report:null,job:null});}};</script>""".replace('DEFAULTS',json.dumps(default_configuration()).replace('<','\\u003c'))
    bridge_checks=r'''
    (()=>{const assert=(v,m)=>{if(!v)throw Error(m);};try{
      assert($('previewNotice').hidden,'Mock Orca bridge did not load');
      document.querySelector('[data-page="calibrationPage"]').click();
      $('positioningOrder').value='x_then_y';$('positioningOrder').dispatchEvent(new Event('input'));
      assert($('positioningProtocol').textContent.includes('X to travel center first, then Y'),'Move priority description did not update');
      $('moonrakerIp').value='127.0.0.2';$('moonrakerIp').dispatchEvent(new Event('input'));
      $('moonrakerPort').value='8125';$('moonrakerPort').dispatchEvent(new Event('input'));
      assert(dirty&&!$('connect').disabled,'Unsaved connection still disables Check');
      $('position').click();const position=window.__messages.at(-1);
      assert(position.command==='start'&&position.action==='Position'&&position.config.positioning_order==='x_then_y','Position did not submit selected move priority');
      window.__receive({command:'start_error',message:'Offline positioning fixture'});
      const before=window.__messages.length;$('connect').click();
      const message=window.__messages.at(-1);
      assert(window.__messages.length===before+1&&message.command==='connect','Check did not reach the Orca bridge');
      assert(message.connection.moonraker_ip==='127.0.0.2'&&message.connection.moonraker_port===8125&&message.connection.moonraker_url==='http://127.0.0.2:8125','Check used saved connection instead of current inputs');
      assert($('connect').disabled&&checkingConnection,'Check must display pending state');
      window.__receive({command:'connection_error',message:'Connection refused'});
      assert(!$('connect').disabled&&!checkingConnection&&$('connectionStatus').textContent.includes('refused'),'Failure did not release Check');
      $('connect').click();window.__receive({command:'printer',sample:{moonraker_url:message.connection.moonraker_url,klippy_state:'ready',temperature_c:22,target_c:0}});
      assert($('connectionStatus').textContent.includes('Connected to')&&!checkingConnection,'Success did not show current connection');
      $('maxTemp').value='245';$('maxFlow').value='75';$('headroom').value='10';
      setPoints([{temperature_c:210,max_reliable_flow_mm3_s:30},{temperature_c:220,max_reliable_flow_mm3_s:38},{temperature_c:230,max_reliable_flow_mm3_s:50},{temperature_c:245,max_reliable_flow_mm3_s:75}]);
      $('testKind').value='LoadedThermal';$('testKind').dispatchEvent(new Event('change'));
      $('calibrationBaseline').value='220';$('calibrationPeak').value='230';$('calibrationFlow').value='35';$('calibrationFlow').dispatchEvent(new Event('input'));
      assert($('run').disabled&&$('calibrationStatus').textContent.includes('34.2'),'Invalid response flow needs an inline explanation');
      assert($('responseFlowLimit').textContent.includes('34.2'),'Baseline flow limit missing beside response settings');
      $('calibrationFlow').value='30';$('calibrationFlow').dispatchEvent(new Event('input'));
      assert(dirty&&!$('run').disabled,'Editing valid response settings must not require saving first');
      $('calibrationTolerance').value='0.75';$('calibrationTolerance').dispatchEvent(new Event('input'));
      assert(!($('run').disabled)&&$('stabilityDescription').textContent.includes('1.65'),'Editable percentage was not reflected as Celsius');
      const count=window.__messages.length;$('run').click();
      const request=window.__messages.at(-1);
      assert(window.__messages.length===count+1&&request.command==='start'&&request.action==='LoadedThermal','Run did not send an explicit response calibration action');
      assert(request.config.calibration_tolerance_percent===0.75&&request.config.calibration_flow_mm3_s===30&&request.config.calibration_baseline_c===220&&request.config.calibration_peak_c===230,'Run used old saved settings');
      assert(starting&&$('run').disabled,'Duplicate starts must be disabled while pending');
      window.__receive({command:'start_error',message:'Fixture start failed'});
      assert(!starting&&!$('run').disabled&&$('calibrationStatus').textContent.includes('Fixture start failed'),'Start failure was hidden or blocked retry');
      $('calibrationFlow').value='31';$('calibrationFlow').dispatchEvent(new Event('input'));$('run').click();
      const retry=window.__messages.at(-1);
      window.__receive({command:'started',config:retry.config,results:[],report:null,job:{status:'running',description:'Fixture response test'}});
      assert(!dirty&&!starting&&running&&$('run').disabled,'Started response must apply settings and prevent another start');
      window.__receive({command:'state',config:retry.config,results:[],report:null,job:{status:'complete',description:'Fixture completed'}});
      assert(!running&&!$('run').disabled,'Completion did not enable another valid test');
      const telemetry=Array.from({length:201},(_,t)=>({time_s:1000+t,
        stage:t<60?'loaded_temperature_no_flow_baseline':t<165?'loaded_temperature_flow':'loaded_temperature_post_flow',
        response_phase:t<60?'':t<90?'baseline':t<125?'heating':t<165?'cooling':'complete',
        target_c:t<90||t>=125?220:230,
        temperature_c:t<40?25+195*t/40:t<90?220+.2*Math.sin(t):t<125?220+10*(1-Math.exp(-(t-90)/4)):t<165?230-10*(1-Math.exp(-(t-125)/5)):220,
        flow_mm3_s:t>=60&&t<165?16:0,power_fraction:.3}));
      const job={status:'running',description:'Fixture heating response',samples:telemetry.slice(0,111)};
      window.__receive({command:'state',config:retry.config,results:[],report:null,job});
      assert(Number($('temperatureChart').dataset.xMin)===80,'Auto graph did not follow heating with preceding context');
      assert(Number($('temperatureChart').dataset.yMin)>210,'Warmup flattened response temperature scale');
      assert(Number($('temperatureOverview').dataset.xMin)===0,'Full-run overview lost warmup');
      assert($('temperatureCaption').textContent.includes('Following heating'),'Active graph phase is not explained');
      $('temperatureView').value='response';$('temperatureView').dispatchEvent(new Event('change'));
      assert(Number($('temperatureChart').dataset.xMin)===60,'Entire response graph should exclude initial warmup');
      $('temperatureView').value='full';$('temperatureView').dispatchEvent(new Event('change'));
      assert(Number($('temperatureChart').dataset.xMin)===0,'Full-run graph selection omitted warmup');
      $('temperatureView').value='auto';
      window.__receive({command:'state',config:retry.config,results:[],report:null,job:{...job,samples:telemetry}});
      assert(Number($('temperatureChart').dataset.xMin)===60,'Post-flow recovery should reveal the full loaded response');
      window.__receive({command:'state',config:retry.config,results:[],report:null,job:{...job,status:'complete',samples:telemetry}});
      assert(Number($('temperatureChart').dataset.xMin)===60&&Number($('temperatureChart').dataset.xMax)===164,'Completed graph must show all loaded phases and exclude recovery');
      assert($('temperatureChart').querySelector('path')&&!$('temperatureChart').innerHTML.includes('NaN'),'Response chart geometry is invalid');
      assert($('temperatureChart').getBoundingClientRect().height<=240&&$('temperatureOverview').getBoundingClientRect().height<=100,'New graphs exceed compact layout');
      assert($('liveFlow').textContent==='0.0','Completed zero flow must remain visible');
      assert(document.querySelectorAll('input[type=checkbox]').length===0,'Checkboxes remain in bridge UI');
      document.body.dataset.uiCheck='passed';document.title='PASS - Adaptive Temperature bridge';
    }catch(e){document.body.dataset.uiCheck='failed';document.body.dataset.uiError=e.message;document.title='FAIL - '+e.message;}})();
    '''
    bridge_page=page.replace('<script>\n(()=>',mock+'\n<script>\n(()=>',1)
    (folder/'ui-bridge-check.html').write_text(inject(bridge_page,bridge_checks),encoding='utf-8')
    print(folder/'ui-check.html');print(folder/'ui-bridge-check.html')


if __name__=='__main__':main()
