PREFIX ?= /usr
DESTDIR ?=
DATADIR = $(PREFIX)/share/archworld.execute

.PHONY: install
install:
	test -s data/frames.jsonl.gz
	test -s audio/music.mp3
	install -Dm755 world.execute "$(DESTDIR)$(DATADIR)/world.execute"
	install -Dm644 src/player.py "$(DESTDIR)$(DATADIR)/src/player.py"
	install -Dm644 data/frames.jsonl.gz "$(DESTDIR)$(DATADIR)/data/frames.jsonl.gz"
	install -Dm644 audio/music.mp3 "$(DESTDIR)$(DATADIR)/audio/music.mp3"
	install -Dm644 LICENSE "$(DESTDIR)$(PREFIX)/share/licenses/archworld.execute/LICENSE"
	install -Dm644 README.md "$(DESTDIR)$(PREFIX)/share/doc/archworld.execute/README.md"
	install -Dm644 NOTICE.md "$(DESTDIR)$(PREFIX)/share/doc/archworld.execute/NOTICE.md"
	install -d "$(DESTDIR)$(PREFIX)/bin"
	ln -sf "$(DATADIR)/world.execute" "$(DESTDIR)$(PREFIX)/bin/world.execute"
	ln -sf "$(DATADIR)/world.execute" "$(DESTDIR)$(PREFIX)/bin/archworld.execute"
