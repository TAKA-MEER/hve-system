#!/bin/sh
# UnitV2 上で root として実行する（install.sh から呼ばれる）。OS のファイルを最小限だけ変え、
# **変える前の原本を /home/m5stack/hve_backup/ に残す**（すでにあれば上書きしない。再配備しても原本が残る）。
#
#   P-11  S90wifi-conf   : 自前の AP（hostapd・wlan1）を起動しない
#   P-12  avahi-daemon.conf : allow-interfaces に wlan0 を足す
#   P-13  S23oadfsko     : grace.ko を読ませない
#   —     S85runpayload  : 組み込みのサービスを自動起動から外す（off.S85runpayload へ改名）
#   —     S86hve         : アプリの自動起動を入れる
#
# wlan0・wpa_supplicant の設定には触らない。S95ip-conf（br0 = 10.254.239.1。USB の有線）にも触らない。
# 変更はファイルを作ってから mv で入れ替える（途中で電源が落ちても半端なファイルが残らない）。

set -e

HVE_HOME=/home/m5stack/hve
DEPLOY=$HVE_HOME/camera/deploy
BACKUP=/home/m5stack/hve_backup
INITD=/etc/init.d
AVAHI_CONF=/etc/avahi/avahi-daemon.conf

[ "$(id -u)" = 0 ] || { echo "root で実行すること" >&2; exit 1; }
[ -f "$DEPLOY/S86hve" ] || { echo "$DEPLOY/S86hve が無い（先にアプリを転送する）" >&2; exit 1; }

mkdir -p "$BACKUP"

# --- 1. 原本の退避（初回だけ） ---
backup_once() {
    # $1: 元のパス
    b="$BACKUP/$(basename "$1")"
    if [ ! -f "$b" ]; then
        cp -p "$1" "$b"
        echo "backup: $1 -> $b"
    fi
}
backup_once $INITD/S23oadfsko
backup_once $INITD/S90wifi-conf
backup_once $AVAHI_CONF
backup_once $INITD/S85runpayload
cp "$DEPLOY/restore_os.sh" "$BACKUP/restore_os.sh"
sync

# --- 2. sed で直す（原本のバックアップから作るので、何度流しても同じ結果） ---
edit() {
    # $1: 対象のパス  $2: sed スクリプト
    t=$1
    b="$BACKUP/$(basename "$t")"
    cp -p "$t" "$t.hve_new"
    sed -f "$2" "$b" > "$t.hve_new"
    cmp -s "$b" "$t.hve_new" && { rm -f "$t.hve_new"; echo "error: $t が変わらない（sed が当たっていない）" >&2; exit 1; }
    mv "$t.hve_new" "$t"
    echo "edited: $t"
}
edit $INITD/S23oadfsko "$DEPLOY/os/S23oadfsko.sed"
edit $INITD/S90wifi-conf "$DEPLOY/os/S90wifi-conf.sed"
edit $AVAHI_CONF "$DEPLOY/os/avahi-daemon.conf.sed"

# --- 3. 自動起動の入れ替え ---
if [ -f $INITD/S85runpayload ]; then
    mv $INITD/S85runpayload $INITD/off.S85runpayload
    echo "disabled: S85runpayload -> off.S85runpayload"
fi
cp -p "$DEPLOY/S86hve" $INITD/S86hve
chmod 755 $INITD/S86hve
sync

# --- 4. いま動いているものに反映する（再起動なしで同じ状態にする） ---
# hostapd（wlan1 の自前の AP）を止める
for p in $(ps | grep '[h]ostapd' | awk '{print $1}'); do kill "$p"; echo "killed hostapd $p"; done
# 組み込みのサービス（S85runpayload stop は温度監視まで殺すので使わない。PID で止める）
for p in $(ps | grep -E '[s]erver_core\.py|[p]ayload/server\.py' | awk '{print $1}'); do kill "$p"; echo "killed payload $p"; done
sleep 1
# avahi に新しい allow-interfaces を読ませる
$INITD/S50avahi-daemon stop || true
sleep 1
$INITD/S50avahi-daemon start
# アプリ
$INITD/S86hve restart
echo "done"
