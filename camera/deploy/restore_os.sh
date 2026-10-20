#!/bin/sh
# UnitV2 上で root として実行する（uninstall.sh から呼ばれる。/home/m5stack/hve_backup/ にも写してある）。
# apply_os.sh が変えた OS のファイルを、退避した原本で**そのまま**戻す（cp -p）。
# 戻したあとに再起動するまでは、止めたもの（hostapd・組み込みのサービス）は止まったまま。

set -e

BACKUP=/home/m5stack/hve_backup
INITD=/etc/init.d
AVAHI_CONF=/etc/avahi/avahi-daemon.conf

[ "$(id -u)" = 0 ] || { echo "root で実行すること" >&2; exit 1; }
[ -f "$BACKUP/S23oadfsko" ] && [ -f "$BACKUP/S90wifi-conf" ] && [ -f "$BACKUP/avahi-daemon.conf" ] \
    || { echo "$BACKUP に原本が無い。何も変えない" >&2; exit 1; }

# アプリを止めて自動起動を外す
[ -x $INITD/S86hve ] && $INITD/S86hve stop || true
rm -f $INITD/S86hve

restore() {
    # $1: 戻し先  （原本は $BACKUP/<basename>）
    cp -p "$BACKUP/$(basename "$1")" "$1.hve_new"
    mv "$1.hve_new" "$1"
    echo "restored: $1"
}
restore $INITD/S23oadfsko
restore $INITD/S90wifi-conf
restore $AVAHI_CONF

if [ -f $INITD/off.S85runpayload ]; then
    mv $INITD/off.S85runpayload $INITD/S85runpayload
    echo "restored: S85runpayload"
fi
sync

$INITD/S50avahi-daemon stop || true
sleep 1
$INITD/S50avahi-daemon start
echo "done（原本に戻した。組み込みのサービスと自前の AP は次の再起動で戻る）"
