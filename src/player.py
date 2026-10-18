"""Fixed-grid ANSI music player. Only its own tmux session is ever changed."""
import argparse
import gzip
import json
import os
import re
from functools import lru_cache
from pathlib import Path
import shlex
import shutil
import signal
import socket
import select
import termios
import tty
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0'
ESC = '\x1b['


def default_audio():
    bundled = ROOT / 'audio/music.mp3'
    if bundled.is_file():
        return bundled
    data_home = os.environ.get('XDG_DATA_HOME', '')
    base = Path(data_home) if data_home and Path(data_home).is_absolute() else Path.home() / '.local/share'
    return base / 'world-execute/music.mp3'


def tmux(*args, check=True):
    return subprocess.run(['tmux', *args], check=check, capture_output=True, text=True)


def parse_background(value):
    if not re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
        raise argparse.ArgumentTypeError('Use a six-digit RGB color, e.g. #171c22')
    value = value.lstrip('#')
    return tuple(int(value[i:i+2],16) for i in (0,2,4))


def parse_osc_background(data):
    match = re.search(rb'\x1b\]11;rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})(?:\x07|\x1b\\)', data)
    if not match:
        return None
    return tuple(round(int(v,16)*255/(16**len(v)-1)) for v in match.groups())


def query_background(timeout=.35):
    """Bounded OSC 11 query; always restore the tty's original input mode."""
    fd = None
    original = None
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        original = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSANOW)
        os.write(fd, b'\x1b]11;?\x1b\\')
        data = b''
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            if select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]:
                data += os.read(fd,1024)
                color = parse_osc_background(data)
                if color is not None:
                    return color
    except (OSError, termios.error):
        pass
    finally:
        if fd is not None:
            try:
                if original is not None:
                    termios.tcsetattr(fd,termios.TCSANOW,original)
            finally:
                os.close(fd)
    return None


ALPHA_COLOR = re.compile(r'\x1b\[(38|48);2;(\d+);(\d+);(\d+);(\d+)m')


@lru_cache(maxsize=512)
def resolve_colors(line, background):
    def replace(match):
        channel,r,g,b,a = map(int,match.groups())
        alpha = max(0,min(1000,a))/1000
        mixed = [round(base+(color-base)*alpha) for base,color in zip(background,(r,g,b))]
        return ESC+str(channel)+';2;'+';'.join(map(str,mixed))+'m'
    return ALPHA_COLOR.sub(replace,line)


def viewport(columns, rows):
    """Fit the source cell aspect into the available pane without cropping."""
    if columns < 80 or rows < 22:
        return None
    # Text cells cannot be enlarged by repetition without duplicating words/rows.
    scale = min(1.0, columns / 160, rows / 44)
    width, height = max(1, int(160 * scale)), max(1, int(44 * scale))
    return (columns - width) // 2 + 1, (rows - height) // 2 + 1, width, height


def origin(columns, rows, width=160, height=44):
    if columns < width or rows < height:
        return None
    return (columns - width) // 2 + 1, (rows - height) // 2 + 1


@lru_cache(maxsize=256)
def resize_line(line, width):
    if width == 160:
        return line
    # Each exported glyph is one terminal column. Preserve its exact SGR style.
    cells = []
    style = []
    for token in re.findall(r'\x1b\[[0-9;]*m|[^\x1b]', line):
        if token.startswith('\x1b'):
            if token == ESC+'0m':
                style = []
            else:
                # Exporter emits complete fg/bg/bold triples, so keep the last triple.
                style.append(token)
                style = style[-3:]
        else:
            cells.append((''.join(style), token))
    output = ESC+'0m'
    previous = None
    for x in range(width):
        current, char = cells[min(159, int((x+.5)*160/width))]
        if current != previous:
            output += current
            previous = current
        output += char
    return output + ESC+'0m'


def resize_rows(rows, width, height):
    return [resize_line(rows[min(43, int((y+.5)*44/height))], width) for y in range(height)]


def status_text(status, columns):
    left, right = status['left'], status['right']
    if columns < 135:
        right = right[-24:]  # Keep the historical clock on smaller terminals.
    available = max(0, columns - len(right) - 2)
    if len(left) > available:
        tag = left.split(' ', 1)[0]
        tail = left.split()[1:]
        while tail and len(tag+' … '+' '.join(tail)) > available:
            tail.pop(0)
        left = (tag+' … '+' '.join(tail))[:available]
    return left.replace('#', '##'), right.replace('#', '##')


CLOCK_CODES = 'aAbBdHeHIjmMpSwyYzZ'


def adapt_format(value):
    """Replace ordinary clock tokens, never tokens inside plugin shell commands."""
    out = ''
    i = 0
    while i < len(value):
        if value.startswith('#(', i):
            end, depth = i + 2, 1
            while end < len(value) and depth:
                if value[end] == '(':
                    depth += 1
                elif value[end] == ')':
                    depth -= 1
                end += 1
            out += value[i:end]
            i = end
        elif value.startswith('%%', i):
            out += '%%'
            i += 2
        elif value[i] == '%' and i + 1 < len(value) and value[i+1] in CLOCK_CODES:
            out += '#{@world_' + value[i+1] + '}'
            i += 2
        elif value.startswith('#{session_name}', i):
            out += '#{@world_session}'
            i += len('#{session_name}')
        elif value.startswith('#S', i):
            out += '#{@world_session}'
            i += 2
        else:
            out += value[i]
            i += 1
    return out


def copy_theme(source, target, window=False):
    result = tmux('show-options', '-A', *(['-w'] if window else []), '-t', source)
    for line in result.stdout.splitlines():
        parts = shlex.split(line)
        if len(parts) < 2:
            continue
        key = parts[0].rstrip('*')
        if key.startswith('@') or key.startswith('window-status' if window else 'status'):
            tmux('set-option', *(['-w'] if window else []), '-t', target, key, parts[1])


def missing_clock_fields(value):
    has = lambda codes: any('#{@world_'+c+'}' in value for c in codes)
    additions = []
    if not (has('yY') and has('mbB') and has('de')):
        additions.append('#{@world_date}')
    if not (has('HI') and has('M')):
        additions.append('#{@world_time}')
    return additions


_WINDOW_LAYOUT = {}
_PLAYBACK_WINDOW = {}


def sync_windows(session, left):
    labels = re.findall(r'(\d+):([^ *]+)(\*)?', left)
    active = next((index for index,name,star in labels if star), None)
    if active is None:
        return
    layout = tuple(labels)
    if _WINDOW_LAYOUT.get(session) == layout:
        return
    if session not in _PLAYBACK_WINDOW:
        _PLAYBACK_WINDOW[session] = tmux('display-message', '-p', '-t', session, '#{window_id}').stdout.strip()
    playback = _PLAYBACK_WINDOW[session]
    result = tmux('list-windows', '-t', session, '-F', '#{window_index} #{window_id}').stdout
    existing = dict(line.split() for line in result.splitlines())
    # Keep the actual playback pane/process alive; replace only our history placeholders.
    if active in existing and existing[active] != playback:
        tmux('kill-window', '-t', existing.pop(active))
    current = next((index for index,wid in existing.items() if wid == playback), None)
    if current != active:
        tmux('move-window', '-s', playback, '-t', session+':'+active)
        if current is not None:
            existing.pop(current)
    existing[active] = playback
    desired = {index:name for index,name,star in labels}
    for index,wid in list(existing.items()):
        if index not in desired and wid != playback:
            tmux('kill-window', '-t', wid)
            existing.pop(index)
    for index,name in desired.items():
        wid = existing.get(index)
        if wid is None:
            wid = tmux('new-window', '-d', '-P', '-F', '#{window_id}',
                       '-t', session+':'+index, '-n', name, 'exec sleep 86400').stdout.strip()
            copy_theme(playback, wid, window=True)
            tmux('set-option', '-w', '-t', wid, 'automatic-rename', 'off')
            tmux('set-option', '-w', '-t', wid, 'remain-on-exit', 'off')
        tmux('rename-window', '-t', wid, name)
    tmux('select-window', '-t', playback)
    _WINDOW_LAYOUT[session] = layout



def configure_story_status(session):
    result = tmux('show-options', '-A', '-t', session)
    formats = {}
    for line in result.stdout.splitlines():
        parts = shlex.split(line)
        if parts:
            parts[0] = parts[0].rstrip('*')
        if len(parts) >= 2 and (parts[0] in ('status-left', 'status-right') or parts[0].startswith('status-format[')):
            formats[parts[0]] = adapt_format(parts[1])
    # Only count fields reachable from the rendered status-format rows.
    visible = '\n'.join(v for k,v in formats.items() if k.startswith('status-format'))
    for side in ('status-left', 'status-right'):
        if side in visible:
            visible += '\n' + formats.get(side, '')
    additions = missing_clock_fields(visible)
    if additions:
        key = 'status-right' if any('status-right' in v for k,v in formats.items() if k.startswith('status-format')) else 'status-format[0]'
        formats[key] = formats.get(key, '') + ' | ' + ' '.join(additions)
        if key == 'status-right':
            old = tmux('show-options', '-A', '-v', '-t', session, 'status-right-length').stdout.strip()
            extra = (11 if '#{@world_date}' in additions else 0) + (9 if '#{@world_time}' in additions else 0) + 3
            tmux('set-option', '-t', session, 'status-right-length', str(max(40, int(old or 40)+extra)))
    # Preserve window styling, adding an index only when the theme omits it.
    for option in ('window-status-format', 'window-status-current-format'):
        value = tmux('show-options', '-w', '-A', '-v', '-t', session, option).stdout.rstrip('\n')
        if '#I' not in value and '#{window_index}' not in value:
            tmux('set-option', '-w', '-t', session, option, '#I:' + value)
    tmux('set-option', '-t', session, 'renumber-windows', 'off')
    for key, value in formats.items():
        tmux('set-option', '-t', session, key, value)
    tmux('set-option', '-t', session, '@world_ready', '1')


class EndingClock:
    """Blend the ending's narrative date into the viewer's current local clock."""
    def __init__(self):
        self.anchor = None

    def resolve(self, historical, seconds, now=None):
        now = now or datetime.now().astimezone()
        if seconds >= 205.0:
            return now
        if seconds < 195.856 or historical is None:
            return historical
        if self.anchor is None:
            self.anchor = historical.timestamp()
        u = max(0, min(1, (seconds - 195.856) / (205.0 - 195.856)))
        u = u * u * (3 - 2 * u)
        # Interpret the source's displayed date in the viewer's local timezone.
        source = datetime.fromtimestamp(self.anchor, timezone.utc).replace(tzinfo=now.tzinfo)
        return datetime.fromtimestamp(source.timestamp()*(1-u) + now.timestamp()*u, now.tzinfo)


def update_status(session, status, columns, seconds=0, ending=None):
    match = re.search(r'\[([^]]+)\]', status['left'])
    clock = status['right'][-24:]
    values = {'clock': clock}
    if match:
        values['session'] = match.group(1)
    try:
        fields = clock.split()
        months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
        hour, minute, second = map(int, fields[3].split(':'))
        date = datetime(int(fields[4]), months.index(fields[1])+1, int(fields[2]),
                        hour, minute, second, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        date = None
    if ending is not None:
        date = ending.resolve(date, seconds)
        if date is not None:
            values['clock'] = date.strftime('%a %b %d %H:%M:%S %Y')
    values['date'] = date.strftime('%Y-%m-%d') if date else '---- -- --'
    values['time'] = date.strftime('%H:%M:%S') if date else '--:--:--'
    for code in CLOCK_CODES:
        values[code] = date.strftime('%'+code) if date else '--'
    command = []
    for key,value in values.items():
        if command:
            command.append(';')
        command += ['set-option', '-t', session, '@world_'+key, value]
    sync_windows(session, status['left'])
    command += [';', 'refresh-client', '-S']
    tmux(*command, check=False)


def write_frame(payload):
    # DEC synchronized output: supporting terminals present the full update together.
    # Unsupported terminals ignore the mode; each update always closes it.
    if payload:
        sys.stdout.write(ESC+'?2026h'+payload+ESC+'?2026l')
        sys.stdout.flush()


def diff_rows(rows, previous, x, y):
    return ''.join(f'{ESC}{y+r};{x}H{line}' for r, line in enumerate(rows)
                   if previous is None or line != previous[r])


class Audio:
    def __init__(self, filename, directory):
        address = str(Path(directory) / 'audio.sock')
        self.process = subprocess.Popen([
            'mpv', '--no-config', '--no-video', '--no-terminal', '--pause=yes',
            '--keep-open=yes', '--audio-display=no', f'--input-ipc-server={address}',
            '--', str(filename)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(2)
        try:
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError('mpv could not open the audio file or audio device.')
                try:
                    self.connection.connect(address)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(.05)
            else:
                raise RuntimeError('mpv IPC startup timed out.')
            self.stream = self.connection.makefile('rwb', buffering=0)
            self.request = 0
            for _ in range(100):
                if self.command(['get_property', 'time-pos']) is not None:
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('mpv did not load the soundtrack.')
        except BaseException:
            self.close()
            raise

    def command(self, command):
        self.request += 1
        self.stream.write((json.dumps({'command': command, 'request_id': self.request})+'\n').encode())
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError('Audio player disconnected.')
            result = json.loads(line)
            if result.get('request_id') == self.request:
                return result.get('data')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.connection.close()


def launch_tmux(args):
    name = 'world-execute-' + uuid.uuid4().hex[:10]
    previous = tmux('display-message', '-p', '#{session_id}').stdout.strip() if os.environ.get('TMUX') else ''
    cols, lines = shutil.get_terminal_size()
    if cols < 80 or lines < 23:
        raise RuntimeError(f'Please enlarge the terminal to at least 80 columns × 23 rows (current: {cols} × {lines}).')
    command = [sys.executable, str(ROOT/'src/player.py'), '--me', '--inside', '--session', name,
               '--audio', str(args.audio), '--data', str(args.data),
               '--background', '#'+''.join(f'{v:02x}' for v in args.background)]
    if previous:
        command += ['--return-session', previous]
    if args.silent:
        command += ['--silent']
    if args.duration:
        command += ['--duration', str(args.duration)]
    # Same tmux server = user's global theme/config; no custom server or global mutations.
    tmux('new-session', '-d', '-s', name, '-x', str(cols), '-y', str(lines), shlex.join(command))
    try:
        if previous:
            copy_theme(previous, name)
            copy_theme(previous, name, window=True)
        # Retain theme, status position/line count, plugin commands and window formats.
        configure_story_status(name)
        tmux('set-window-option', '-t', name, 'window-style', 'default')
        tmux('set-window-option', '-t', name, 'window-active-style', 'default')
        tmux('set-window-option', '-t', name, 'remain-on-exit', 'off')
        tmux('set-window-option', '-t', name, 'automatic-rename', 'off')
        tmux('rename-window', '-t', name, 'world.execute')
        if previous:
            tmux('switch-client', '-t', name)
        else:
            subprocess.run(['tmux', 'attach-session', '-t', name], check=True)
    except BaseException:
        tmux('kill-session', '-t', name, check=False)
        raise


def play(args):
    if args.inside:
        # Wait for attachment: do not spend the opening notes in a detached session.
        for _ in range(600):
            if tmux('list-clients', '-t', args.session, '-F', '#{client_tty}', check=False).stdout.strip():
                break
            time.sleep(.05)
        else:
            raise RuntimeError('No client attached to the playback session.')
    audio = None
    entered = False
    with tempfile.TemporaryDirectory(prefix='world-execute-') as temp, gzip.open(args.data, 'rt') as frames:
        meta = json.loads(next(frames))
        if (meta['version'], meta['cols'], meta['rows']) != (3, 160, 44):
            raise RuntimeError('Unsupported frame data.')
        try:
            if not args.silent:
                audio = Audio(args.audio, temp)
            sys.stdout.write(ESC+'?1049h'+ESC+'?25l'+ESC+'?7l'+ESC+'0m'+ESC+'2J')
            sys.stdout.flush()
            entered = True
            last_size = None
            previous = None
            index = -1
            rows = None
            record = None
            ending = EndingClock()
            status_at = -1.0
            start = time.monotonic()
            if audio:
                audio.command(['set_property', 'pause', False])
            while True:
                size = shutil.get_terminal_size()
                pos = viewport(*size)
                if not pos:
                    if audio:
                        audio.command(['set_property', 'pause', True])
                    pause = time.monotonic()
                    sys.stdout.write(ESC+'0m'+ESC+'2J'+ESC+'H'+'Resize terminal to at least 80 columns x 22 rows.')
                    sys.stdout.flush()
                    while not viewport(*shutil.get_terminal_size()):
                        time.sleep(.1)
                    start += time.monotonic() - pause
                    if audio:
                        audio.command(['set_property', 'pause', False])
                    last_size = None
                    continue
                seconds = audio.command(['get_property', 'time-pos']) if audio else time.monotonic()-start
                if seconds is None:
                    time.sleep(.01)
                    continue
                if seconds >= min(meta['duration'], args.duration or meta['duration']):
                    break
                target = min(meta['frames']-1, int(seconds*meta['fps']))
                changed = target > index
                while index < target:
                    line = frames.readline()
                    if not line:
                        raise RuntimeError('Frame data ended prematurely.')
                    index += 1
                    # Consume skipped frames without JSON parsing; every record is self-contained.
                    if index == target:
                        record = json.loads(line)
                        rows = [resolve_colors(row,args.background) for row in record['rows']]
                clear = ''
                if size != last_size:
                    clear = ESC+'0m'+ESC+'2J'
                    previous = None
                    last_size = size
                    changed = True
                if changed and rows:
                    x, y, width, height = pos
                    fitted = resize_rows(rows, width, height)
                    write_frame(clear+diff_rows(fitted, previous, x, y))
                    previous = fitted
                if args.inside and record and time.monotonic() - status_at >= .125:
                    update_status(args.session, record['status'], size.columns, seconds, ending)
                    status_at = time.monotonic()
                if audio and audio.command(['get_property', 'eof-reached']):
                    break
                time.sleep(1/(meta['fps']*2))
        finally:
            try:
                if audio:
                    audio.close()
            finally:
                if entered:
                    try:
                        sys.stdout.write(ESC+'?2026l'+ESC+'0m'+ESC+'?7h'+ESC+'?25h'+ESC+'?1049l')
                        sys.stdout.flush()
                    except (BrokenPipeError, OSError):
                        pass


def main():
    parser = argparse.ArgumentParser(prog='world.execute', description='Play the Unix → Linux → Arch character MV in your terminal.')
    parser.add_argument('--me', action='store_true', help='Start the MV at music time zero.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    parser.add_argument('--audio', type=Path, default=default_audio(),
                        help='Override bundled soundtrack; source checkouts fall back to the user data directory.')
    parser.add_argument('--data', type=Path, default=ROOT/'data/frames.jsonl.gz')
    parser.add_argument('--background', type=parse_background, help='Override terminal background RGB, e.g. #171c22.')
    parser.add_argument('--no-tmux', action='store_true', help='Play directly in the current terminal.')
    parser.add_argument('--silent', action='store_true', help='Diagnostic playback without audio.')
    parser.add_argument('--duration', type=float, help='Diagnostic playback limit in seconds.')
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--session', default='', help=argparse.SUPPRESS)
    parser.add_argument('--return-session', default='', help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.audio = args.audio.expanduser().resolve()
    args.data = args.data.expanduser().resolve()
    if args.duration is not None and (not __import__('math').isfinite(args.duration) or args.duration <= 0):
        parser.error('--duration must be a finite positive number')
    if not args.me:
        parser.print_help()
        return
    try:
        if not sys.stdout.isatty():
            raise RuntimeError('Run this command in an interactive terminal.')
        if not args.data.is_file():
            raise RuntimeError(f'Missing frame data: {args.data}. Reinstall the distribution or rebuild the frame data from the source project.')
        if not args.silent and not args.audio.is_file():
            raise RuntimeError(f'Missing soundtrack: {args.audio}. Place your local audio file there, or use --audio /path/to/music.mp3. If using an installed package, reinstall it to restore the bundled soundtrack.')
        if not args.silent and not shutil.which('mpv'):
            raise RuntimeError('Playback requires mpv. Install mpv and try again.')
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        if args.background is None:
            args.background = query_background()
            if args.background is None:
                args.background = (6,8,14)
                print('Could not query terminal background; using #06080e. For accurate fades use --background "#RRGGBB".', file=sys.stderr)
        if not args.no_tmux and not args.inside:
            if not shutil.which('tmux'):
                raise RuntimeError('Install tmux or use --no-tmux.')
            launch_tmux(args)
        else:
            play(args)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f'world.execute: {error}', file=sys.stderr)
        return 1
    finally:
        if args.inside and args.session:
            if args.return_session:
                clients=tmux('list-clients','-t',args.session,'-F','#{client_tty}',check=False).stdout.splitlines()
                for client in clients:
                    tmux('switch-client','-c',client,'-t',args.return_session,check=False)
            tmux('kill-session','-t',args.session,check=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
