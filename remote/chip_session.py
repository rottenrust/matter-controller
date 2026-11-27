"""A serialized chip-tool interactive shell. The HTTP controller owns the lock."""
import os
import select
import shutil
import subprocess
import tempfile
import time


class ChipSession:
    def __init__(self, executable, storage):
        self.executable = executable
        self.storage = storage
        self.process = None
        self.input_guard = None

    def start(self):
        # libedit rotates the CLI history when the web remote polls the lamp.
        history = self.storage / 'chip_tool_history'
        backup = self.storage / 'chip_tool_history.before-remote'
        if history.is_file() and not backup.is_file():
            with tempfile.NamedTemporaryFile(dir=self.storage) as tmp, history.open('rb') as src:
                shutil.copyfileobj(src, tmp)
                tmp.flush()
                try:
                    os.link(tmp.name, backup)
                except FileExistsError:
                    if not backup.is_file():
                        raise
        # chip-tool expects "quit" even on EOF. The guard forwards literal input
        # and also terminates the shell if the HTTP process exits unexpectedly.
        self.input_guard = subprocess.Popen(
            ['/bin/sh', '-c', '/bin/cat; echo quit'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, bufsize=0, start_new_session=True)
        try:
            self.process = subprocess.Popen(
                [self.executable, 'interactive', 'start', '--advertise-operational', '0',
                 '--storage-directory', self.storage],
                stdin=self.input_guard.stdout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, start_new_session=True, env={**os.environ, 'TMPDIR': str(self.storage)})
        except BaseException:
            self.close()
            raise
        finally:
            if self.input_guard:
                self.input_guard.stdout.close()

    def close(self):
        if self.input_guard:
            self.input_guard.stdin.close()
            self.input_guard.stdout.close()
        if self.process:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
            self.process = None
        if self.input_guard:
            self.input_guard.wait()
            self.input_guard = None

    def run(self, args):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.start()
        # Only server-generated CLI tokens reach this method.
        command = ' '.join(map(str, args)) + ' --timeout 45'
        begin = b'[TOO] Command: ' + command.encode() + b' '
        end = b'[TOO] Command: delay sleep 0 '
        output = bytearray()
        deadline = time.monotonic() + 50
        try:
            self.input_guard.stdin.write((command + '\ndelay sleep 0\n').encode())
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise TimeoutError('Лампа не ответила вовремя.')
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk or len(output) + len(chunk) > 1024 * 1024:
                    raise ConnectionError('Соединение с контроллером прервалось.')
                output.extend(chunk)
                stop = output.find(end)
                if stop >= 0:
                    start = output.find(begin, 0, stop)
                    if start < 0:
                        raise ConnectionError('Контроллер не подтвердил получение команды.')
                    return bytes(output[start + len(begin):stop])
        except OSError:
            self.close()
            raise
