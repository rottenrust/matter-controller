"""Install the per-user LaunchAgent and create the personal Safari bookmark."""
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'local.matter-lamp.remote'
os.umask(0o077)
private = ROOT/'remote-access'
private.mkdir(mode=0o700,exist_ok=True)
key = private/'key'
if not key.exists():
    key.write_text(secrets.token_urlsafe(32))
name = subprocess.check_output(['/usr/sbin/scutil','--get','LocalHostName'],text=True).strip()
url = f'http://{name}.local:8765/p/{key.read_text().strip()}/'
bookmark = ROOT/'Свет — Гостиная.webloc'
bookmark.write_bytes(plistlib.dumps({'URL':url}))
bookmark.chmod(0o600)
agents = Path.home()/'Library/LaunchAgents'
agents.mkdir(exist_ok=True)
plist = agents/(LABEL+'.plist')
config = {'Label':LABEL,'ProgramArguments':[sys.executable,str(ROOT/'remote/server.py')],
          'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
          'StandardErrorPath':str(private/'service-error.log')}
content = plistlib.dumps(config)
if plist.exists() and plist.read_bytes()!=content:
    original = plistlib.loads(plist.read_bytes())
    if original.get('ProgramArguments',[None,None])[-1]!=str(ROOT/'remote/server.py'):
        sys.exit('Существующий агент принадлежит другой установке.')
plist.write_bytes(content)
domain = 'gui/'+str(os.getuid())
active = subprocess.run(['launchctl','print',domain+'/'+LABEL],capture_output=True).returncode==0
if active:
    subprocess.run(['launchctl','bootout',domain+'/'+LABEL],check=True)
subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True)
print('Личный ярлык и пользовательская служба готовы.')
