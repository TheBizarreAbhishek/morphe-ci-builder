#!/system/bin/sh

MODDIR=${0%/*}
. "$MODDIR/config"

rm -f "/data/adb/abhi/${MODDIR##*/}.apk"
rmdir "/data/adb/abhi"

rm -f "/data/adb/post-fs-data.d/$PKG_NAME-uninstall.sh"
