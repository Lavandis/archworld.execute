"""PTY smoke tests: no audible output and no user tmux sessions touched."""
import fcntl, importlib.util, os, pty, select, signal, struct, subprocess, tempfile, termios, time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('player',ROOT/'src/player.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
def launch(command,env=None,cols=180,rows=50):
    master,slave=pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
    proc=subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
    os.close(slave)
    return proc,master

def drain(proc,master,timeout=15,resize=False):
    output=b'';start=time.monotonic();shrunk=False;grown=False
    while time.monotonic()-start<timeout:
        elapsed=time.monotonic()-start
        if resize and elapsed>.3 and not shrunk:
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',20,80,0,0));shrunk=True
        if resize and elapsed>.8 and not grown:
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',50,180,0,0));grown=True
        if select.select([master],[],[],.05)[0]:
            try: output+=os.read(master,65536)
            except OSError: break
        if proc.poll() is not None:break
    if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
    os.close(master)
    return output

proc,fd=launch([str(ROOT/'world.execute'),'--me','--background','#171c22','--no-tmux','--silent','--duration','1.2'])
out=drain(proc,fd,resize=True);proc.wait()
assert proc.returncode==0,(proc.returncode,out[-300:])
assert b'?1049h' in out and b'?1049l' in out
assert b'Resize terminal' in out
print('PASS: ANSI playback, undersize pause, resize recovery, screen restoration')

for cols,rows in [(80,24),(100,60),(240,30),(320,88)]:
    proc,fd=launch([str(ROOT/'world.execute'),'--me','--background','#171c22','--no-tmux','--silent','--duration','.25'],cols=cols,rows=rows)
    out=drain(proc,fd);proc.wait()
    assert proc.returncode==0,(cols,rows,out[-300:])
    assert b'?1049l' in out
print('PASS: 80x24, portrait, ultrawide and large terminal playback')

proc,fd=launch([str(ROOT/'world.execute'),'--me','--background','#171c22','--no-tmux','--silent'])
time.sleep(.15)
proc.send_signal(signal.SIGTERM)
out=drain(proc,fd);proc.wait()
assert proc.returncode==0 and b'?1049l' in out
print('PASS: SIGTERM restores terminal')

real_popen=subprocess.Popen
def null_audio(argv,*a,**kw):return real_popen([argv[0],'--ao=null',*argv[1:]],*a,**kw)
with tempfile.TemporaryDirectory() as tmp,patch.object(p.subprocess,'Popen',null_audio):
    soundtrack = p.default_audio()
    if not soundtrack.is_file():
        soundtrack = ROOT.parent/'public/audio/world.execute(me).mp3'
    audio=p.Audio(soundtrack,tmp)
    try:
        before=audio.command(['get_property','time-pos'])
        audio.command(['set_property','pause',False]);time.sleep(.3)
        after=audio.command(['get_property','time-pos'])
        assert after>before,(before,after)
        audio.command(['set_property','pause',True])
        print('PASS: real mpv IPC, soundtrack load, advancing audio clock, pause (null output)')
    finally:audio.close()

# An isolated tmux server with a sentinel session. Test the real nested-launch path.
name='world-test-'+str(os.getpid())
def tm(*args,check=True):return subprocess.run(['tmux','-L',name,*args],capture_output=True,text=True,check=check)
try:
    tm('-f','/dev/null','new-session','-d','-s','sentinel','-x','180','-y','50')
    tm('set-option','-t','sentinel','status-style','bg=colour17,fg=colour15')
    tm('set-option','-t','sentinel','status-right','BAT #(printf 88) %H:%M')
    tm('set-option','-t','sentinel','status-position','top')
    tm('set-window-option','-t','sentinel','window-status-current-format','#[bold]#W (CPU)')
    env=dict(os.environ,TERM='xterm-256color');env.pop('TMUX',None)
    proc,fd=launch(['tmux','-L',name,'attach-session','-t','sentinel'],env)
    time.sleep(.3)
    import shlex
    tm('send-keys','-t','sentinel',shlex.join([str(ROOT/'world.execute'),'--me','--background','#171c22','--silent','--duration','2']), 'Enter')
    own=None;start=time.monotonic();output=b''; statuses=set(); indices=set()
    while time.monotonic()-start<8:
        if select.select([fd],[],[],.05)[0]:output+=os.read(fd,65536)
        sessions=tm('list-sessions','-F','#{session_name}').stdout.splitlines()
        matches=[s for s in sessions if s.startswith('world-execute-')]
        if matches and tm('show-options','-v','-t',matches[0],'@world_ready',check=False).stdout.strip() != '1':
            continue
        if matches and own is None:
            own=matches[0]
            style=tm('show-options','-v','-t',own,'status-style').stdout.strip()
            assert style=='bg=colour17,fg=colour15',style
        if matches:
            indices.add(tm('display-message','-p','-t',matches[0],'#{window_index}',check=False).stdout.strip())
            statuses.add(tm('display-message','-p','-t',matches[0],'#{window_name}',check=False).stdout.strip())
            fmt=tm('show-options','-v','-t',matches[0],'status-right',check=False)
            if fmt.returncode == 0:
                assert fmt.stdout.strip() == 'BAT #(printf 88) #{@world_H}:#{@world_M} | #{@world_date}',fmt.stdout
        if own and not matches:break
    assert any('power' in value for value in statuses),(statuses,output[-3000:])
    assert any('ring' in value for value in statuses),statuses
    assert {'0','1'} <= indices,indices
    assert tm('show-options','-v','-t','sentinel','status-right').stdout.strip() == 'BAT #(printf 88) %H:%M'
    assert '@world_session' not in tm('show-options','-v','-t','sentinel','status-format[0]').stdout
    assert own,'Playback session was not created: '+repr(output[-300:])
    assert tm('list-sessions','-F','#{session_name}').stdout.strip()=='sentinel'
    assert tm('list-clients','-F','#{session_name}').stdout.strip()=='sentinel'
    print('PASS: inherited theme, dynamic power/ring status, original status format untouched, own session cleanup and return')
    tm('new-session','-d','-s','history','-x','180','-y','50','exec sleep 86400')
    tm('set-option','-t','history','renumber-windows','off')
    playback=tm('display-message','-p','-t','history','#{window_id}').stdout.strip()
    with patch.object(p,'tmux',tm):
        p.sync_windows('history','[unix] 0:power 1:ring 2:pieces 3:object*')
        listing=tm('list-windows','-t','history','-F','#{window_index}:#{window_name}').stdout.splitlines()
        assert listing==['0:power','1:ring','2:pieces','3:object'],listing
        assert tm('display-message','-p','-t','history','#{window_id}').stdout.strip()==playback
        p.sync_windows('history','[gnu] 0:news*')
        assert tm('list-windows','-t','history','-F','#{window_index}:#{window_name}').stdout.strip()=='0:news'
        assert tm('display-message','-p','-t','history','#{window_id}').stdout.strip()==playback
    tm('kill-session','-t','history')
    assert tm('list-sessions','-F','#{session_name}').stdout.strip()=='sentinel'
    print('PASS: full chapter window list, group reset, playback window survives, placeholders cleaned')
finally:
    tm('kill-server',check=False)
    if 'fd' in locals():
        try:os.close(fd)
        except OSError:pass
    if 'proc' in locals():
        try:proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
