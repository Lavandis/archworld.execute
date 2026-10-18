import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('player',Path(__file__).resolve().parents[1]/'src/player.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

class LayoutTest(unittest.TestCase):
    def test_center_and_small_terminal(self):
        self.assertEqual(p.origin(160,44),(1,1))
        self.assertEqual(p.origin(180,50),(11,4))
        self.assertIsNone(p.origin(159,50))
        self.assertIsNone(p.origin(180,43))
    def test_changed_rows_only_and_clear_old_content(self):
        self.assertEqual(p.diff_rows(['one','   '],['one','old'],11,4),'\x1b[5;11H   ')
        self.assertEqual(p.diff_rows(['one'],['one'],1,1),'')
        self.assertEqual(p.diff_rows(['one'],None,2,3),'\x1b[3;2Hone')

class AdaptiveTest(unittest.TestCase):
    def test_fit_dimensions(self):
        self.assertEqual(p.viewport(160,44),(1,1,160,44))
        self.assertEqual(p.viewport(320,88),(81,23,160,44))
        self.assertEqual(p.viewport(120,44),(1,6,120,33))
        self.assertIsNone(p.viewport(79,24))
    def test_scaling_keeps_default_background_and_row_width(self):
        import re
        line='\x1b[0m\x1b[22m\x1b[38;2;20;40;60m\x1b[49m'+'AB'*80+'\x1b[0m'
        for width,height in [(120,33),(320,88)]:
            result=p.resize_rows([line]*44,width,height)
            self.assertEqual(len(result),height)
            self.assertEqual(len(re.sub(r'\x1b\[[0-9;]*m','',result[0])),width)
            self.assertIn('\x1b[49m',result[0])
            self.assertNotIn('48;2;',result[0])
    def test_status_preserves_clock_and_active_window(self):
        status={'left':'[unix] '+' '.join(f'{i}:window' for i in range(10))+' 10:pipe*',
                'right':'○ ○ ● ○ load 1.00 0.70 0.40 Sun Jan 13 20:33:38 1974'}
        left,right=p.status_text(status,100)
        self.assertTrue(left.startswith('[unix]'))
        self.assertIn('10:pipe*',left)
        self.assertTrue(right.endswith('1974'))
        self.assertLessEqual(len(left)+len(right)+2,100)

class ThemeTest(unittest.TestCase):
    def test_preserve_plugins_styles_and_replace_clock(self):
        original='#[fg=blue]#S BAT #(printf "%H (nested)") %H:%M %%'
        expected='#[fg=blue]#{@world_session} BAT #(printf "%H (nested)") #{@world_H}:#{@world_M} %%'
        self.assertEqual(p.adapt_format(original),expected)
    def test_session_long_format(self):
        self.assertEqual(p.adapt_format('#{session_name} #W #{battery_percentage}'),
                         '#{@world_session} #W #{battery_percentage}')

class NoDuplicateRowsTest(unittest.TestCase):
    def test_tall_window_does_not_duplicate_source_rows(self):
        rows=[str(i).zfill(3)+' ' * 157 for i in range(44)]
        for columns,height in [(180,70),(200,65),(320,100)]:
            _,_,w,h=p.viewport(columns,height)
            scaled=p.resize_rows(rows,w,h)
            self.assertEqual(len(scaled),44)
            self.assertEqual(len(set(scaled)),44)
            self.assertEqual(w,160)

class ClockCompletionTest(unittest.TestCase):
    def test_time_only_adds_date(self):
        self.assertEqual(p.missing_clock_fields('#{@world_H}:#{@world_M}'), ['#{@world_date}'])
    def test_date_only_adds_time(self):
        self.assertEqual(p.missing_clock_fields('#{@world_Y}-#{@world_m}-#{@world_d}'), ['#{@world_time}'])
    def test_complete_and_partial_dates(self):
        self.assertEqual(p.missing_clock_fields('#{@world_Y}-#{@world_m}-#{@world_d} #{@world_H}:#{@world_M}'), [])
        self.assertEqual(p.missing_clock_fields('#{@world_d} #{@world_H}:#{@world_M}'), ['#{@world_date}'])

class RobustnessTest(unittest.TestCase):
    def test_all_aspects_stay_inside(self):
        for columns,rows in [(80,24),(100,60),(240,30),(160,44),(320,100),(500,22)]:
            x,y,w,h=p.viewport(columns,rows)
            self.assertGreaterEqual(x,1)
            self.assertGreaterEqual(y,1)
            self.assertLessEqual(x+w-1,columns)
            self.assertLessEqual(y+h-1,rows)
            self.assertLessEqual(abs(w/160-h/44),1/44)
    def test_ending_local_clock_and_transition(self):
        from datetime import datetime,timezone,timedelta
        local=timezone(timedelta(hours=-5))
        now=datetime(2028,1,1,0,0,3,tzinfo=local)
        old=datetime(2026,10,17,21,4,tzinfo=timezone.utc)
        clock=p.EndingClock()
        self.assertEqual(clock.resolve(old,190,now),old)
        start=clock.resolve(old,195.856,now)
        middle=clock.resolve(old,200,now)
        self.assertLess(start.timestamp(),middle.timestamp())
        self.assertLess(middle.timestamp(),now.timestamp())
        self.assertEqual(clock.resolve(old,205,now),now)
        later=now+timedelta(seconds=30)
        self.assertEqual(clock.resolve(old,208,later),later)
    def test_viewer_clock_earlier_than_story(self):
        from datetime import datetime,timezone
        now=datetime(2020,1,1,tzinfo=timezone.utc)
        old=datetime(2026,10,17,tzinfo=timezone.utc)
        clock=p.EndingClock()
        clock.resolve(old,195.856,now)
        self.assertEqual(clock.resolve(old,205,now),now)

class FrameRefreshTest(unittest.TestCase):
    def test_old_colored_cells_are_overwritten_with_default_background(self):
        old='\x1b[0m\x1b[48;2;10;20;30m'+'▀'*160+'\x1b[0m'
        blank='\x1b[0m\x1b[49m'+' '*160+'\x1b[0m'
        payload=p.diff_rows([blank]*44,[old]*44,1,1)
        self.assertEqual(payload.count('\x1b[49m'),44)
        for row in range(1,45):
            self.assertIn(f'\x1b[{row};1H'+blank,payload)
    def test_sync_mode_closed_in_same_write(self):
        from unittest.mock import patch
        import io
        output=io.StringIO()
        with patch.object(p.sys,'stdout',output):
            p.write_frame('frame')
        self.assertEqual(output.getvalue(),'\x1b[?2026hframe\x1b[?2026l')

class BackgroundFadeTest(unittest.TestCase):
    def test_osc_precision_and_terminators(self):
        self.assertEqual(p.parse_osc_background(b'noise\x1b]11;rgb:1717/1c1c/2222\x1b\\'),(23,28,34))
        self.assertEqual(p.parse_osc_background(b'\x1b]11;rgb:ff/80/00\x07'),(255,128,0))
        self.assertIsNone(p.parse_osc_background(b'no response'))
    def test_alpha_resolved_to_actual_background(self):
        for background in [(23,28,34),(240,240,240),(0,0,0)]:
            base=';'.join(map(str,background))
            self.assertEqual(p.resolve_colors('\x1b[38;2;23;147;209;0mX',background),
                             '\x1b[38;2;'+base+'mX')
            self.assertEqual(p.resolve_colors('\x1b[48;2;23;147;209;1000m',background),
                             '\x1b[48;2;23;147;209m')
        self.assertEqual(p.resolve_colors('\x1b[38;2;100;100;100;500m',(20,20,20)),
                         '\x1b[38;2;60;60;60m')
    def test_query_restores_tty_on_success_and_timeout(self):
        from unittest.mock import patch
        import contextlib
        for response in [b'\x1b]11;rgb:1717/1c1c/2222\x07',b'']:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(p.os,'open',return_value=17))
                stack.enter_context(patch.object(p.os,'write'))
                stack.enter_context(patch.object(p.os,'close'))
                stack.enter_context(patch.object(p.termios,'tcgetattr',return_value=['original']))
                restored=stack.enter_context(patch.object(p.termios,'tcsetattr'))
                stack.enter_context(patch.object(p.tty,'setraw'))
                stack.enter_context(patch.object(p.select,'select',return_value=([17],[],[])))
                stack.enter_context(patch.object(p.os,'read',return_value=response))
                result=p.query_background(.001)
                self.assertEqual(result,(23,28,34) if response else None)
                restored.assert_called_once_with(17,p.termios.TCSANOW,['original'])

    def test_default_background_remains_default(self):
        self.assertEqual(p.resolve_colors('\x1b[49m ',(23,28,34)),'\x1b[49m ')

if __name__=='__main__':unittest.main()


class AudioPathTest(unittest.TestCase):
    def test_xdg_path(self):
        from unittest.mock import patch
        with patch.object(Path, 'is_file', return_value=False), patch.dict(p.os.environ, {'XDG_DATA_HOME': '/tmp/custom-data'}):
            self.assertEqual(p.default_audio(), Path('/tmp/custom-data/world-execute/music.mp3'))

    def test_empty_and_relative_xdg_fall_back(self):
        from unittest.mock import patch
        for value in ['', 'relative/data']:
            with patch.object(Path, 'is_file', return_value=False), patch.dict(p.os.environ, {'XDG_DATA_HOME': value}):
                self.assertEqual(p.default_audio(), Path.home()/'.local/share/world-execute/music.mp3')


class BundledAudioTest(unittest.TestCase):
    def test_installed_bundle_wins(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'audio').mkdir()
            (root/'audio/music.mp3').write_bytes(b'test')
            with patch.object(p, 'ROOT', root):
                self.assertEqual(p.default_audio(), root/'audio/music.mp3')
