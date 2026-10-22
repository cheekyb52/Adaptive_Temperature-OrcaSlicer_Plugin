"""Render and check staged positioning UI without building a release wheel."""
import json
from pathlib import Path

from configuration_defaults import default_configuration
from tool_profiles import PROFILE_FIELDS, fresh_profile


def main():
    root=Path(__file__).parent
    resources=root/'plugin/orca_flow/resources'
    page=(resources/'panel.html').read_text(encoding='utf-8')
    cfg=default_configuration()
    cfg.update(moonraker_ip='127.0.0.1',moonraker_url='http://127.0.0.1:7125')
    data={'DEFAULT_CONFIG':cfg,'DEFAULT_RESPONSE':json.loads((resources/'default-response.json').read_text(encoding='utf-8')),
          'HOTEND_PRESETS':json.loads((resources/'meltcalc/hotends.json').read_text(encoding='utf-8')),
          'TOOL_SCHEMA':{'fields':PROFILE_FIELDS,'templates':{str(i):fresh_profile(i,'') for i in range(4)}}}
    for name,value in data.items():page=page.replace('/*'+name+'*/null',json.dumps(value).replace('<','\\u003c'))
    mock='''<script>
    window.__requests=[];
    window.orca={onMessage(fn){window.__receive=fn;},postMessage(message){window.__requests.push(message);
    if(message.command==='refresh')window.__receive({command:'state',config:FIXTURE_CONFIG,job:null,results:[],report:null});}};
    </script>'''.replace('FIXTURE_CONFIG',json.dumps(cfg))
    page=page.replace('<script>\n(()=>',mock+'\n<script>\n(()=>',1)
    checks=r'''<script>
    (()=>{const el=id=>document.getElementById(id),assert=(value,message)=>{if(!value)throw Error(message);};try{
      document.querySelector('[data-page="calibrationPage"]').click();
      assert(el('positioningOrder').value==='y_then_x','Default priority must be Y first');
      assert(el('positioningProtocol').textContent.includes('Y to travel center first, then X'),'Y-first explanation missing');
      el('positioningOrder').value='x_then_y';el('positioningOrder').dispatchEvent(new Event('input'));
      assert(el('positioningProtocol').textContent.includes('X to travel center first, then Y'),'X-first explanation missing');
      el('position').click();let request=window.__requests.at(-1);
      assert(request.command==='start'&&request.action==='Position'&&request.config.positioning_order==='x_then_y','Position did not submit selected priority');
      window.__receive({command:'start_error',message:'Offline fixture: no hardware operation'});
      el('saveConnection').click();request=window.__requests.at(-1);
      assert(request.command==='save'&&request.config.positioning_order==='x_then_y','Save omitted positioning priority');
      window.__receive({command:'saved',config:request.config,job:null,results:[],report:null});
      assert(el('positioningOrder').value==='x_then_y','Saved priority did not reload');
      el('toolMode').value='toolchanger';el('toolMode').dispatchEvent(new Event('input'));
      el('editingTool').value='1';el('editingTool').dispatchEvent(new Event('change'));
      assert(el('positioningOrder').value==='x_then_y','Changing tools must preserve printer positioning priority');
      const legacy={...request.config};delete legacy.positioning_order;
      window.__receive({command:'imported_settings',config:legacy});
      assert(el('positioningOrder').value==='y_then_x','Legacy settings must receive the Y-first default');
      el('positioningOrder').scrollIntoView({block:'center'});
      document.body.dataset.uiCheck='passed';document.title='PASS - staged move priority';
    }catch(error){document.body.dataset.uiCheck='failed';document.body.dataset.uiError=error.message;document.title='FAIL - '+error.message;}})();
    </script>'''
    page=page.replace('</body>',checks+'</body>')
    out=root/'.build/positioning'
    out.mkdir(parents=True,exist_ok=True)
    (out/'ui-check.html').write_text(page,encoding='utf-8')
    print(out/'ui-check.html')


if __name__=='__main__':main()
