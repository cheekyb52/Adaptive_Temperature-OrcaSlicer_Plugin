# SPDX-License-Identifier: GPL-3.0-only
"""Normalize separate IP/port fields and older saved Moonraker addresses."""
from ipaddress import ip_address
import re
from urllib.parse import urlsplit


def connection_config(raw):
    if not isinstance(raw,dict):raise ValueError('Connection settings must be an object')
    host=raw.get('moonraker_ip','')
    if not isinstance(host,str):raise ValueError('Enter the printer IP address')
    host=host.strip()
    port=raw.get('moonraker_port',7125)
    if isinstance(port,bool) or not re.fullmatch(r'\d+',str(port)) or not 1<=int(port)<=65535:
        raise ValueError('Moonraker port must be an integer from 1 to 65535')
    port=int(port)
    legacy=raw.get('moonraker_url','')
    if not isinstance(legacy,str):raise ValueError('Invalid Moonraker address')
    legacy=legacy.strip()
    scheme='http'
    if legacy:
        explicit='://' in legacy
        parsed=urlsplit(legacy if explicit else 'http://'+legacy)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Use a Moonraker IP and port, without a path or credentials')
        legacy_host=str(ip_address(parsed.hostname))
        legacy_port=parsed.port or ((443 if parsed.scheme=='https' else 80) if explicit else 7125)
        if not host:
            host,port=legacy_host,legacy_port
        if host==legacy_host and port==legacy_port:scheme=parsed.scheme
    if host:
        try:host=str(ip_address(host))
        except ValueError as exc:raise ValueError('Enter an IP address only; enter the port in its separate box') from exc
        address='['+host+']' if ':' in host else host
        url=f'{scheme}://{address}:{port}'
    else:url=''
    heater=raw.get('heater_name','extruder')
    fan=raw.get('fan_name','fan')
    if not isinstance(heater,str) or not re.fullmatch(r'extruder(?:[1-9]\d*)?',heater):
        raise ValueError('Use a configured extruder heater name')
    if not isinstance(fan,str) or not re.fullmatch(r'fan|fan_generic [A-Za-z0-9_]+',fan):
        raise ValueError('Use fan or fan_generic followed by its configured name')
    return {'moonraker_ip':host,'moonraker_port':port,'moonraker_url':url,'heater_name':heater,'fan_name':fan}
