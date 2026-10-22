"""Prepare a public, illustrated guide using the released plugin's actual UI.

All fixtures are offline: the fake Orca bridge cannot access a printer.
Run this file, render the capture manifest with Chrome, then use --package.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from configuration_defaults import default_configuration
from tool_profiles import fresh_profile

VERSION = '0.5.8'
OUT = ROOT / 'docs' / 'Adaptive Temperature Guide'
REVIEW = ROOT / '.build' / 'guide'


def example_config():
    cfg = default_configuration()
    cfg.update(material='Example PLA', filament_type='PLA', hotend='Example hotend / 0.4 mm',
               printer_name='Example printer', moonraker_ip='192.0.2.10', moonraker_port=7125,
               moonraker_url='http://192.0.2.10:7125', max_flow_mm3_s=24, burst_aggressiveness=0,
               calibration_flow_mm3_s=12, calibration_baseline_c=220, calibration_peak_c=230,
               points=[dict(temperature_c=t, max_reliable_flow_mm3_s=q)
                       for t, q in [(210, 12), (220, 16), (230, 20), (240, 24)]])
    return cfg


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'images').mkdir(exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    page = (ROOT / 'dist' / f'Adaptive Temperature {VERSION}' /
            'Adaptive Temperature preview.html').read_text(encoding='utf-8')
    cfg = example_config()
    mock = """<script>
    // Documentation fixture only. No HTTP, WebSocket, process or printer access.
    window.orca={onMessage:cb=>window.__receive=cb,postMessage:m=>{
      if(m.command==='refresh')window.__receive({command:'state',config:EXAMPLE,results:[],report:null,job:null});
    }};
    </script>""".replace('EXAMPLE', json.dumps(cfg))
    page = page.replace('<script>\n(()=>', mock + '\n<script>\n(()=>', 1)
    common = r"""
    function capture(title, nodes){
      const root=document.createElement('div');root.className='guide-capture';
      const h=document.createElement('div');h.className='capture-title';h.textContent='Adaptive Temperature 0.5.8 / '+title;root.append(h);
      nodes.forEach(node=>root.append(node));
      const note=document.createElement('p');note.className='capture-note';note.textContent='UI example only - illustrative inputs and simulated results.';root.append(note);
      document.body.replaceChildren(root);
      const style=document.createElement('style');style.textContent=`
      :root{--orca-bg:#2b2d30;--orca-fg:#e4e6e8;--orca-muted:#b2b8be;--orca-border:#44484b;--orca-accent:#008c80}
      body{height:auto;overflow:visible;display:block;background:#2b2d30}
      .guide-capture{padding:18px;width:1100px}.capture-title{font-size:17px;font-weight:600;margin-bottom:14px}
      .capture-note{font-size:11px;color:#b2b8be;margin:10px 0 0}.guide-capture>.group{margin-bottom:12px}
      `;document.head.append(style);document.documentElement.dataset.captureReady='true';
      document.documentElement.dataset.contentHeight=Math.ceil(root.getBoundingClientRect().height);
    }
    function group(id){return $(id).closest('.group');}
    function nav(id){document.querySelector('[data-page="'+id+'"]').click();}
    function readyResponse(){
      const binding={printer_name:'Example printer',physical_tool:0,material:'Example PLA',nozzle_diameter_mm:.4,hotend:'Example hotend / 0.4 mm'};
      return {name:'Latest loaded response calibration',source:'recorded_loaded_temperature_step_candidate',configuration_state:'configured',independently_validated:false,binding,
        heating:{delay_s:2,time_constant_s:2.7},cooling:{delay_s:3,time_constant_s:3.2}};
    }
    """
    fixtures = [
        ('01-profile', 590, """nav('curvePage');capture('Filament curve - tool and filament setup',[group('toolMode'),group('material')]);"""),
        ('02-curve', 730, """$('trialFlow').value='16';renderCurve();capture('Filament curve - bounds and flow points',[group('minTemp'),group('pointRows')]);"""),
        ('03-connection', 670, """nav('calibrationPage');capture('Hotend calibration - connection and positioning',[group('moonrakerIp'),group('position')]);"""),
        ('04-flow-load', 440, """nav('calibrationPage');$('testPoint').value='1';renderProtocol();setEnabled();capture('Hotend calibration - flow-load response',[group('run')]);"""),
        ('05-response-test', 630, """nav('calibrationPage');$('testKind').value='LoadedThermal';$('testKind').dispatchEvent(new Event('change'));capture('Hotend calibration - heating and cooling',[group('run')]);"""),
        ('06-results', 915, r"""
        nav('calibrationPage');
        const samples=Array.from({length:201},(_,t)=>({time_s:1000+t,
          stage:t<50?'loaded_temperature_no_flow_baseline':t<165?'loaded_temperature_flow':'loaded_temperature_post_flow',
          response_phase:t<50?'':t<85?'baseline':t<125?'heating':t<165?'cooling':'complete',
          target_c:t<85||t>=125?220:230,
          temperature_c:t<45?25+195*t/45:t<85?220+.12*Math.sin(t):t<125?220+10*(1-Math.exp(-Math.max(0,t-87)/2.7))+.12*Math.sin(t):t<165?220+10*Math.exp(-Math.max(0,t-128)/3.2)+.12*Math.sin(t):220,
          flow_mm3_s:t>=50&&t<165?12:0,power_fraction:.4}));
        renderState({config,results:[{physical_tool:0,commanded_flow_mm3_s:12,transitions:[
          {status:'candidate_fit',from_target_c:220,to_target_c:230,model:{sensor_response_95_percent_s:10.1}},
          {status:'candidate_fit',from_target_c:230,to_target_c:220,model:{sensor_response_95_percent_s:12.6}}]}],
          job:{status:'complete',description:'T0: constant 12 mm\u00b3/s; stabilize at 220 \u00b0C, heat to 230 \u00b0C, cool to 220 \u00b0C; recovery and heater off.',samples},report:null});
        capture('Hotend calibration - completed response (simulated)',[group('jobStatus'),group('exportResults')]);
        """),
        ('07-adopt-response', 550, """
        nav('analysisPage');setResponse(readyResponse());
        const wrap=document.createElement('div');wrap.className='group';
        const title=document.createElement('div');title.className='group-title';title.textContent='Calibrated hotend response';wrap.append(title);
        const body=document.createElement('div');body.className='group-body';wrap.append(body);
        const start=$('responseSource');let node=start;while(node){const next=node.nextElementSibling;body.append(node);node=next;}
        body.append($('saveOutput').parentElement);capture('Slice analysis - apply and save the response',[wrap]);
        """),
        ('08-output-burst', 710, """
        nav('analysisPage');const c={...config,thermal_response:readyResponse(),output_mode:'temperature_test'};fill(c);setEnabled();
        const outer=group('outputMode'),body=outer.querySelector('.group-body');
        const preset=group('burstPreset');const response=$('responseSettings');response.replaceChildren(preset);
        [...body.children].forEach(n=>{if(n.tagName==='P'&&n.className==='small muted')n.remove();});
        capture('Slice analysis - output mode and burst scheduling',[outer]);
        """),
        ('09-export', 530, """
        nav('analysisPage');lastReport={target:{peak_planned_flow_mm3_s:18,peak_requested_flow_mm3_s:21,deposition_moves:12000},selected_logical_tool:0,
        curve_preview:{temperature_target_estimate_c:230},gcode_modified:true,warnings:['Illustrative analysis only.'],edit:{status:'modified',commands_inserted:36,temperature_range_c:[210,236],events:[
        {before_source_line:1200,modeled_time_s:35,logical_tool:0,temperature_c:230},
        {before_source_line:1400,modeled_time_s:48,logical_tool:0,temperature_c:218}]}};renderAnalysis();
        $('editCommands').closest('details').open=true;capture('Slice analysis - exported file results (simulated)',[group('plannedPeak'),group('editSummary')]);
        """),
        ('10-tools', 265, """
        nav('curvePage');const c={...config,tool_mode:'toolchanger',tool_count:4,thermal_response:readyResponse(),tool_profiles:{}};
        c.tool_profiles['0']={...c,tool_profiles:undefined,tool_configured:true};
        c.tool_profiles['1']=JSON.parse(JSON.stringify(c.tool_profiles['0']));
        Object.assign(c.tool_profiles['1'],{physical_tool:1,logical_tool:1,heater_name:'extruder1'});
        c.tool_profiles['1'].thermal_response.binding.physical_tool=1;
        fill(c);$('editingTool').value='1';$('editingTool').dispatchEvent(new Event('change'));$('copySource').value='0';setEnabled();
        capture('Printer tools - copying an identical setup',[group('toolMode')]);
        """),
    ]
    manifest = []
    for name, height, code in fixtures:
        script = common + '\ntry{\n' + code + "\n}catch(e){document.title='CAPTURE ERROR: '+e.message;document.body.dataset.captureError=e.stack;}"
        html = page.replace('\n})();\n</script>', '\n' + script + '\n})();\n</script>')
        fixture = REVIEW / (name + '.html')
        fixture.write_text(html, encoding='utf-8')
        manifest.append(dict(name=name, width=1100, height=height, url=fixture.as_uri(),
                             image=str(OUT / 'images' / (name + '.png'))))
    (REVIEW / 'captures.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    content = (ROOT / 'docs' / 'guide-content.html').read_text(encoding='utf-8')
    (OUT / 'index.html').write_text(content, encoding='utf-8')
    from tools.build_repo_docs import build as build_repo_guide
    build_repo_guide(ROOT)
    (OUT / 'START HERE.txt').write_text(
        'Adaptive Temperature 0.5.8 - Illustrated User Guide\n\n'
        'Open index.html in a browser, or read Adaptive Temperature User Guide.pdf.\n'
        'Keep the images folder beside index.html. Screenshots show the released UI\n'
        'with illustrative inputs and simulated results, never a live printer.\n'
        'The example IP address 192.0.2.10 is a documentation placeholder.\n'
        'This guide does not install or change the plugin.\n', encoding='utf-8')
    print(REVIEW / 'captures.json')


def package():
    for image in range(1, 11):
        assert list((OUT / 'images').glob(f'{image:02}-*.png')), f'Missing screenshot {image}'
    pdf = OUT / 'Adaptive Temperature User Guide.pdf'
    assert pdf.read_bytes().startswith(b'%PDF-'), 'Missing PDF'
    archive = ROOT / 'dist' / f'Adaptive-Temperature-{VERSION}-User-Guide.zip'
    files = [OUT/'index.html', OUT/'START HERE.txt', pdf, *sorted((OUT/'images').glob('*.png'))]
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in files:
            z.write(file, str(file.relative_to(OUT)))
    verification = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    verification['archive'] = str(archive.relative_to(ROOT))
    (REVIEW/'artifact-manifest.json').write_text(json.dumps(verification, indent=2), encoding='utf-8')
    print(archive)


def print_source():
    # Printed image hyperlinks otherwise expand to this PC's absolute file paths.
    # Embed the pictures and remove only their click-to-enlarge anchors.
    content = (OUT / 'index.html').read_text(encoding='utf-8')
    content = re.sub(r'<a href="images/[^\"]+">(<img[^>]+>)</a>', r'\1', content)
    for file in (OUT / 'images').glob('*.png'):
        uri = 'data:image/png;base64,' + base64.b64encode(file.read_bytes()).decode('ascii')
        content = content.replace('src="images/' + file.name + '"', 'src="' + uri + '"')
    target = REVIEW / 'print.html'
    target.write_text(content, encoding='utf-8')
    print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', action='store_true')
    parser.add_argument('--print-source', action='store_true')
    args = parser.parse_args()
    if args.package:
        package()
    elif args.print_source:
        print_source()
    else:
        prepare()
