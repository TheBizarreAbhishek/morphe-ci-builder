#!/system/bin/sh
MODDIR=$MODPATH
. "$MODPATH/utils.sh"

ui_print ""
if [ -n "$MODULE_ARCH" ] && [ "$MODULE_ARCH" != "$ARCH" ]; then
	abort "ERROR: Wrong arch
Your device: $ARCH
Module: $MODULE_ARCH"
fi
if [ "$ARCH" = "arm" ]; then
	ARCH_LIB=armeabi-v7a
elif [ "$ARCH" = "arm64" ]; then
	ARCH_LIB=arm64-v8a
elif [ "$ARCH" = "x86" ]; then
	ARCH_LIB=x86
elif [ "$ARCH" = "x64" ]; then
	ARCH_LIB=x86_64
else abort "ERROR: unreachable: ${ARCH}"; fi

set_perm_recursive "$MODPATH/bin" 0 0 0755 0777

umount_all

if OP=$(dumpsys package "$PKG_NAME") && [ "$OP" ]; then
	if echo "$OP" | grep -m1 pkgFlags | grep -Fq UPDATED_SYSTEM_APP; then
		pmex uninstall-system-updates "$PKG_NAME" >/dev/null 2>&1
	fi
else
	if pmex install-existing "$PKG_NAME" >/dev/null 2>&1; then
		pmex uninstall-system-updates "$PKG_NAME" >/dev/null 2>&1
	fi
fi

INS=true
if BASEPATH=$(get_basepath); then
	if [ "${BASEPATH:1:4}" != data ]; then
		ui_print "* Detected $PKG_NAME as a system app"
		SCNM="/data/adb/post-fs-data.d/$PKG_NAME-uninstall.sh"
		mkdir -p /data/adb/post-fs-data.d
		echo "mount -t tmpfs none $BASEPATH" >"$SCNM"
		chmod +x "$SCNM"
		ui_print "* Created the uninstall script."
		ui_print ""
		ui_print "* Reboot and reflash the module!"
		abort
	fi

	VERSION=$(get_app_version)
	if [ "$VERSION" ] && [ "$VERSION" = "$PKG_VER" ]; then
		ui_print "* $PKG_NAME is up-to-date ($VERSION)"
		INS=false
	else
		if [ ! -f "$MODPATH/stock/base.apk" ]; then
			ui_print "ERROR: Version mismatch"
			ui_print "  installed: '$VERSION'"
			ui_print "  module:    '$PKG_VER'"
			abort
		fi
		if [ "$VERSION" ]; then
			ui_print "* Version mismatch: Device has $VERSION, Module requires $PKG_VER"
			ui_print "* Uninstalling updates/existing app..."
			op=$(pmex uninstall "$PKG_NAME")
			ui_print "  - Status: $op"
		fi
		INS=true
	fi

	# TODO:
	# elif "${MODPATH:?}/bin/$ARCH/cmpr" "$BASEPATH/base.apk" "$MODPATH/$PKG_NAME.apk"; then
	# 	ui_print "* $PKG_NAME is up-to-date"
	# 	INS=false
	# fi
fi

install() {
	if [ ! -f "$MODPATH/stock/base.apk" ]; then
		abort "ERROR: Stock $PKG_NAME apk was not found"
	fi
	install_err=""
	VERIF1=$(settings get global verifier_verify_adb_installs)
	VERIF2=$(settings get global package_verifier_enable)
	settings put global verifier_verify_adb_installs 0
	settings put global package_verifier_enable 0

	SZ=$(stat -c "%s" "$MODPATH"/stock/*.apk | awk '{sum += $0} END {print sum}')
	for IT in 1 2; do
		ui_print "* Installing target stock version $PKG_VER..."
		ui_print "  - Creating installation session..."
		if ! SES=$(pmex install-create --user 0 -i com.android.vending -r -S "$SZ"); then
			ui_print "  - ERROR: install-create failed"
			install_err="$SES"
			break
		fi
		SES=${SES#*[} SES=${SES%]*}

		for apki in "$MODPATH/stock"/*.apk; do
			set_perm "${apki}" 1000 1000 644 u:object_r:apk_data_file:s0
			ui_print "  - Writing: $(basename "${apki}")..."
			if ! op=$(pmex install-write -S "$SZ" "$SES" "$(basename "${apki}")" "${apki}"); then
				ui_print "  - ERROR: install-write failed"
				install_err="$op"
				break
			fi
		done
		if [ "$install_err" ]; then break; fi

		ui_print "  - Committing changes..."
		if ! op=$(pmex install-commit "$SES"); then
			ui_print "  - ERROR: install-commit failed ($op)"
			if echo "$op" | grep -q -e INSTALL_FAILED_VERSION_DOWNGRADE -e INSTALL_FAILED_UPDATE_INCOMPATIBLE; then
				ui_print "  - Mismatch detected. Attempting full uninstall..."
				if ! op=$(pmex uninstall "$PKG_NAME"); then
					ui_print "  - ERROR: pm uninstall failed ($op)"
					if [ $IT = 2 ]; then
						install_err="ERROR: pm uninstall failed."
						break
					fi
				fi
				continue
			fi
			install_err="$op"
			break
		fi
		ui_print "  - Installation successful!"
		if BASEPATH=$(get_basepath); then
			:
		else
			install_err=" "
			break
		fi
		break
	done
	settings put global verifier_verify_adb_installs "$VERIF1"
	settings put global package_verifier_enable "$VERIF2"
	if [ "$install_err" ]; then
		abort "$install_err"
	fi
}
if [ $INS = true ] && ! install; then abort; fi
BASEPATHLIB=${BASEPATH}/lib/${ARCH}
if [ $INS = true ] || [ -z "$(ls -A1 "$BASEPATHLIB")" ]; then
	ui_print "* Extracting native libraries for $ARCH..."
	if [ ! -d "$BASEPATHLIB" ]; then mkdir -p "$BASEPATHLIB"; else rm -f "$BASEPATHLIB"/* >/dev/null 2>&1 || :; fi
	if op=$(unzip -o -j "$MODPATH/stock/base.apk" "lib/${ARCH_LIB}/*" -d "$BASEPATHLIB" 2>&1); then
		set_perm_recursive "${BASEPATH}/lib" 1000 1000 755 755 u:object_r:apk_data_file:s0
		ui_print "  - Extraction complete."
	else
		ui_print "  - ERROR: Extraction failed: '$op'"
		echo >&2 "ERROR: extracting native libs failed: '$op'"
	fi
fi

set_perm "$MODPATH/base.apk" 1000 1000 644 u:object_r:apk_data_file:s0

ui_print "* Preparing systemless mount..."
# move out the apk from /data/adb/modules/.. to /data/adb/abhi to not trip some root detections
mkdir -p "/data/adb/abhi"
mv -f "$MODPATH/base.apk" "$RVPATH"

ui_print "* Binding mount to: $BASEPATH/base.apk"
if ! op=$(su -M -c mount -o bind "$RVPATH" "$BASEPATH/base.apk" 2>&1); then
	ui_print "  - ERROR: Mount failed!"
	ui_print "  - $op"
else
	ui_print "  - Mount successful."
fi
am force-stop "$PKG_NAME"

ui_print "* Optimizing app speed profile (compile)..."
op=$(cmd package compile -m speed-profile -f "$PKG_NAME" 2>&1)
ui_print "  - Status: $op"
# nohup cmd package compile -m speed-profile -f "$PKG_NAME" >/dev/null 2>&1

if [ "$KSU" ]; then
	DUMPSYS=$(dumpsys package "$PKG_NAME" 2>&1)
	UID=$(echo "$DUMPSYS" | grep -m1 uid=)
	UID=${UID#*=} UID=${UID%% *}
	if [ -z "$UID" ]; then
		UID=$(echo "$DUMPSYS" | grep -m1 userId=)
		UID=${UID#*=} UID=${UID%% *}
	fi
	if [ "$UID" ]; then
		if ! OP=$("${MODPATH:?}/bin/$ARCH/ksu_profile" "$UID" "$PKG_NAME" 2>&1); then
			ui_print "  $OP"
			ui_print "* Because you are using a fork of KernelSU, "
			ui_print "  you need to go to your root manager app and"
			ui_print "  disable 'Unmount modules' for $PKG_NAME"
		fi
	else
		ui_print "ERROR: UID could not be found for $PKG_NAME"
	fi
fi

rm -rf "${MODPATH:?}/bin" "$MODPATH/stock/"

ui_print "* Done"
ui_print "  by morphe-ci-builder"
ui_print " "

if [ -n "$GITHUB_URL" ]; then
	ui_print "* Opening repository on GitHub..."
	am start -a android.intent.action.VIEW -d "$GITHUB_URL" >/dev/null 2>&1 || :
fi
