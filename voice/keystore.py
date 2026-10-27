"""API keys live in Windows Credential Manager (encrypted, tied to your Windows login), not in
corelynx.env. To see or change them: Control Panel > Credential Manager > Windows Credentials,
entries named "zx-smart-room".

Copy one to the clipboard (e.g. to back it up in a password manager):
    python -P keystore.py copy ANTHROPIC_API_KEY

Store one straight from the clipboard (copy it in the provider's dashboard first; it is never shown,
and the clipboard is cleared afterwards):
    python -P keystore.py paste DEEPGRAM_API_KEY

Move keys from corelynx.env into it (values are never printed):
    python -P keystore.py
To add or replace one later, put it in corelynx.env and run that again.
"""
import os
import sys

import keyring

SERVICE = 'zx-smart-room'
SECRET_NAMES = ['CORELYNX_API_KEY', 'ANTHROPIC_API_KEY', 'ELEVENLABS_API_KEY', 'DEEPGRAM_API_KEY']
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, 'corelynx.env')


def get(name):
    try:
        return keyring.get_password(SERVICE, name) or ''
    except Exception:
        return ''


def fill(env):
    """Adds the stored keys to a settings dict; a key still typed into corelynx.env wins."""
    for name in SECRET_NAMES:
        if not env.get(name):
            value = get(name)
            if value:
                env[name] = value
    return env


def move_from_env_file():
    with open(ENV_FILE, encoding='utf-8') as f:
        lines = f.read().split('\n')
    out = []
    for line in lines:
        name, sep, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if sep and name in SECRET_NAMES and value and not line.lstrip().startswith('#'):
            keyring.set_password(SERVICE, name, value)
            if keyring.get_password(SERVICE, name) != value:
                sys.exit(f'{name}: could not be stored safely; corelynx.env left unchanged')
            out.append(f'# {name} is stored in Windows Credential Manager ({SERVICE})')
            out.append(f'{name}=')
            print(f'{name}: moved to Windows Credential Manager')
        else:
            out.append(line)
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))
    for name in SECRET_NAMES:
        print(f'{name}: ' + ('stored' if get(name) else 'NOT stored'))


def copy_to_clipboard(name):
    """Puts one key on the clipboard (never on screen), e.g. to paste into a password manager."""
    import subprocess
    value = get(name)
    if not value:
        sys.exit(f'{name} is not stored')
    subprocess.run(['powershell', '-NoProfile', '-Command', '$input | Set-Clipboard'],
                   input=value, text=True, check=True)
    print(f'{name} copied to the clipboard. Paste it where you need it, then copy something else over it.')


def paste_from_clipboard(name):
    """Stores the key on the clipboard (never shown), then clears the clipboard."""
    import subprocess
    if name not in SECRET_NAMES:
        sys.exit(f'{name} is not one of {", ".join(SECRET_NAMES)}')
    value = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw'],
                           capture_output=True, text=True, check=True).stdout.strip()
    if not value or len(value) < 20 or any(c.isspace() for c in value):
        sys.exit("The clipboard doesn't hold a key. Copy the key itself (one line, no spaces) and try again.")
    keyring.set_password(SERVICE, name, value)
    if get(name) != value:
        sys.exit(f'{name}: could not be stored')
    subprocess.run(['powershell', '-NoProfile', '-Command', 'Set-Clipboard -Value " "'], check=False)
    print(f'{name} stored in Windows Credential Manager. Clipboard cleared.')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'copy':
        copy_to_clipboard(sys.argv[2].upper())
    elif len(sys.argv) == 3 and sys.argv[1] == 'paste':
        paste_from_clipboard(sys.argv[2].upper())
    else:
        move_from_env_file()
