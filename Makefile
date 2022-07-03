LINUXDEPLOY ?= linuxdeploy-$(shell uname -m).AppImage

dev: runekit/_resources.py

runekit/_resources.py: resources.qrc $(wildcard runekit/**/*.js) $(wildcard runekit/**/*.png)
	pyside2-rcc $< -o $@

# Sdist

dist/runekit.tar.gz: main.py poetry.lock runekit/_resources.py $(wildcard runekit/**/*)
	poetry build -f sdist
	cd dist; cp runekit-*.tar.gz runekit.tar.gz

# Mac

dist/RuneKit.app: RuneKit.spec qt.conf main.py poetry.lock runekit/_resources.py $(wildcard runekit/**/*)
	pyinstaller --clean --noupx -w -n RuneKit --noconfirm \
		--exclude-module tkinter \
		-s -d noarchive --onedir \
		--osx-bundle-identifier de.cupco.runekit \
		$<

.PHONY: fix
fix:
	python3 ./fix_app_qt_folder_names_for_codesign.py ./dist/RuneKit.app

dist/RuneKit.app.zip: dist/RuneKit.app
	python3 ./fix_app_qt_folder_names_for_codesign.py ./dist/RuneKit.app
	codesign -s - --force --all-architectures --timestamp --deep ./dist/RuneKit.app
	cd dist; zip --symlinks -r -9 RuneKit.app.zip RuneKit.app
# AppImage

build/python3.9.12.AppImage:
	mkdir build || true
	wget https://github.com/niess/python-appimage/releases/download/python3.9/python3.9.12-cp39-cp39-manylinux1_x86_64.AppImage -O "$@"
	chmod +x "$@"

build/appdir: build/python3.9.12.AppImage
	$< --appimage-extract
	mv squashfs-root build/appdir

dist/RuneKit.AppImage: dist/runekit.tar.gz build/appdir deploy/runekit-appimage.sh
	build/appdir/usr/bin/python3 -m pip install dist/runekit.tar.gz
	rm $(wildcard build/appdir/*.desktop) $(wildcard build/appdir/usr/share/applications/*.desktop) $(wildcard build/appdir/usr/share/metainfo/*)
	cp deploy/RuneKit.desktop build/appdir/
	cp deploy/RuneKit.desktop build/appdir/usr/share/applications/
	cp deploy/RuneKit.appdata.xml build/appdir/usr/share/metainfo/
	cp deploy/runekit-appimage.sh build/appdir/AppRun
	$(LINUXDEPLOY) --appdir build/appdir --output appimage
	cp RuneKit-*.AppImage "$@"

clean:
	rm -rf dist/RuneKit.app
	rm -f dist/RuneKit.app.zip

.PHONY: dev clean
