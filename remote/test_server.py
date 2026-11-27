import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from chip_session import ChipSession


REPORT = '\n'.join(f'[TOO] {field}: 1' for field in server.FIELDS).encode()
BUSY = b'[DMG] CASESession.cpp: CHIP Error 0x000000DB'
SUCCESS = b'[DMG] Received Command Response Status for Endpoint=1 Cluster=0x0000_0006 Command=0x0000_0000 Status=0x0'


class GarlandTest(unittest.TestCase):
    def test_frames(self):
        for data, frame in [
            ({'action': 'power', 'on': True}, '7e0404010001ff00ef'),
            ({'action': 'power', 'on': False}, '7e0404000000ff00ef'),
            ({'action': 'brightness', 'value': 30}, '7e04011e01ffff00ef'),
            ({'action': 'color', 'hex': '#000080'}, '7e0705030000ff10ef'),
            ({'action': 'color', 'hex': '#ffffff'}, '7e070503ffffff10ef'),
        ]:
            self.assertEqual(server.garland_frame(data)[0].hex(), frame)
        for data in [None, {'action': 'timer'}, {'action': 'brightness', 'value': True},
                     {'action': 'power', 'on': 0}, {'action': 'brightness', 'value': 101},
                     {'action': 'color', 'hex': '#000000'}, {'action': 'color', 'hex': '123456'}]:
            with self.assertRaises(ValueError):
                server.garland_frame(data)

    def test_failed_dispatch_preserves_last_success(self):
        with patch.object(server, 'last_garland_command', None), patch.object(server.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(server.send_garland({'action': 'power', 'on': True}), {'last': 'включить'})
            run.return_value.returncode = 5
            with self.assertRaisesRegex(RuntimeError, 'не нашёл'):
                server.send_garland({'action': 'power', 'on': False})
            self.assertEqual(server.last_garland_command, 'включить')


class MatterTest(unittest.TestCase):
    def test_read_retries_but_commands_are_sent_once(self):
        for replies, read, error in (
            ([BUSY, REPORT], True, None),
            ([ConnectionError(), REPORT], True, None),
            ([BUSY, BUSY], True, 'занята'),
            ([BUSY], False, 'занята'),
            ([TimeoutError()], False, 'вовремя'),
            ([SUCCESS], False, None),
            ([b''], False, 'подтверждение'),
            ([b'[TOO] Run command failure: failed\n' + SUCCESS], False, 'подтверждение'),
            ([b'OS Error 0x02000041'], False, 'Нет маршрута'),
            ([b'OS Error 0x02000041\n[TOO] Error: CHIP Error 0x0000002F'], False, 'подтверждение'),
        ):
            with self.subTest(read=read, error=error), patch.object(server, 'SESSION') as session, patch.object(server.time, 'sleep'):
                session.run.side_effect = replies
                action = server.snapshot if read else lambda: server.matter('onoff', 'off', 1, 1)
                if error:
                    with self.assertRaisesRegex(RuntimeError, error):
                        action()
                elif read:
                    self.assertEqual(set(action()), set(server.FIELDS))
                else:
                    action()
                self.assertEqual(session.run.call_count, len(replies))

    def test_write_verified_by_its_own_readback(self):
        with patch.object(server, 'SESSION') as session:
            session.run.side_effect = [b'[EM] >>> Type 0001:07 (IM:WriteResponse)', b'[TOO] StartUpOnOff: null']
            server.matter('onoff', 'write', 'start-up-on-off', 'null', 1, 1)
            session.run.assert_called_with(('onoff', 'read', 'start-up-on-off', 1, 1))
            session.run.side_effect = [b'[EM] >>> Type 0001:07 (IM:WriteResponse)', b'[TOO] OnTime: 50']
            with self.assertRaisesRegex(RuntimeError, 'подтверждение'):
                server.matter('onoff', 'write', 'on-time', 0, 1, 1)

    def test_shell_boundaries_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            (storage / 'chip_tool_history').write_text('original')
            tool = storage / 'test-chip'
            tool.write_text("""#!/bin/sh
while IFS= read -r line; do
  case "$line" in
    quit) exit ;;
    crash*) exit 1 ;;
    silent*) continue ;;
  esac
  printf '[TOO] Command: %s \\n' "$line"
  printf 'result\\n'
done
""")
            tool.chmod(0o700)
            session = ChipSession(tool, storage)
            self.addCleanup(session.close)
            self.assertIn(b'result', session.run(('read',)))
            pid = session.process.pid
            session.run(('again',))
            self.assertEqual(session.process.pid, pid)
            with self.assertRaises(ConnectionError):
                session.run(('crash',))
            session.run(('restart',))
            self.assertNotEqual(session.process.pid, pid)
            with self.assertRaises(ConnectionError):
                session.run(('silent',))
            self.assertIsNone(session.process)
            backup = storage / 'chip_tool_history.before-remote'
            self.assertEqual(backup.read_text(), 'original')
            self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
