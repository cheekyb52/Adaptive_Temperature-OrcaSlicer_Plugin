# SPDX-License-Identifier: GPL-3.0-only
import unittest
from moonraker_connection import connection_config


class ConnectionTests(unittest.TestCase):
    def test_ip_port_default_and_ipv6(self):
        self.assertEqual(connection_config({'moonraker_ip':'127.0.0.1'})['moonraker_url'],'http://127.0.0.1:7125')
        result=connection_config({'moonraker_ip':'::1','moonraker_port':8125})
        self.assertEqual(result['moonraker_url'],'http://[::1]:8125')

    def test_old_saved_addresses_migrate_without_a_protocol(self):
        for legacy in ('127.0.0.1:8125','http://127.0.0.1:8125'):
            result=connection_config({'moonraker_url':legacy})
            self.assertEqual((result['moonraker_ip'],result['moonraker_port']),('127.0.0.1',8125))
        self.assertEqual(connection_config({'moonraker_url':'https://127.0.0.1'})['moonraker_port'],443)

    def test_invalid_ip_port_credentials_and_paths_are_rejected(self):
        for ip in ('127.0.0.1:7125','http://127.0.0.1','printer.local','999.0.0.1'):
            with self.subTest(ip=ip),self.assertRaises(ValueError):connection_config({'moonraker_ip':ip})
        for port in (0,65536,7125.5,True,'abc'):
            with self.subTest(port=port),self.assertRaises(ValueError):connection_config({'moonraker_ip':'127.0.0.1','moonraker_port':port})
        for url in ('http://user:pass@127.0.0.1','http://127.0.0.1/path','file:///tmp/no'):
            with self.subTest(url=url),self.assertRaises(ValueError):connection_config({'moonraker_url':url})
