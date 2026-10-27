#!/bin/sh
# PC から UnitV2 経由で UNO のファームを書き込む（WP-CAM-09）。
#
#   camera/deploy/flash_uno.sh [UnitV2 のアドレス] [hex]
#
# アドレス・ユーザー・パスワードの決め方は install.sh と同じ（HVE_UNITV2_HOST・HVE_UNITV2_USER・
# HVE_UNITV2_PASS）。hex の既定は firmware/cam_io/.pio/build/uno/firmware.hex。
# 待つ秒数は環境変数 HVE_FLASH_WAIT_S（既定 60）。
# 必要なもの: ssh・sshpass（PC 側）。scp は UnitV2 で使えないので cat を ssh に流す。
#
# やること: uno_flash.py と hex を UnitV2 の /tmp（RAM）へ送る → S86hve stop（/dev/ttyS1 を空ける）→
#   「UNO のリセットを押す」と出して書き込む → 成功しても失敗しても S86hve start で戻し、送ったファイルを消す。
# 終了コードは uno_flash.py のもの（0 成功。値の意味は uno_flash.py の EXIT_*）。

set -e

HOST=${1:-${HVE_UNITV2_HOST:-10.254.239.1}}
HEX=${2:-firmware/cam_io/.pio/build/uno/firmware.hex}
USER=${HVE_UNITV2_USER:-m5stack}
PASS=${HVE_UNITV2_PASS:-12345678}
WAIT_S=${HVE_FLASH_WAIT_S:-60}
DEPLOY_DIR=$(cd "$(dirname "$0")" && pwd)

SSH="sshpass -p $PASS ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $USER@$HOST"

if [ ! -f "$HEX" ]; then
    echo "hex が無い: $HEX" >&2
    echo "先にビルドする: pio run -d firmware/cam_io -e uno" >&2
    exit 1
fi

# どこで終わっても（失敗・Ctrl-C を含む）アプリを戻し、RAM 上の /tmp に送ったものを消す。
# 終了コードは書き込みのものを保つ。
restore() {
    status=$?
    trap - EXIT
    echo "== アプリを戻す =="
    echo "$PASS" | $SSH 'sudo -S /etc/init.d/S86hve start' || echo "アプリを戻せなかった。S86hve start を手で実行する" >&2
    $SSH 'rm -f /tmp/uno_flash.py /tmp/uno_flash.hex' || true
    exit "$status"
}
trap restore EXIT
trap 'exit 130' INT TERM

echo "== $HOST へ送る =="
cat "$DEPLOY_DIR/uno_flash.py" | $SSH 'cat > /tmp/uno_flash.py'
cat "$HEX" | $SSH 'cat > /tmp/uno_flash.hex'

echo "== アプリを止める（/dev/ttyS1 を空ける） =="
echo "$PASS" | $SSH 'sudo -S /etc/init.d/S86hve stop'

echo "== 書き込む。UNO のリセットボタンを押す（$WAIT_S 秒以内） =="
rc=0
echo "$PASS" | $SSH "sudo -S python3 /tmp/uno_flash.py /dev/ttyS1 /tmp/uno_flash.hex $WAIT_S" || rc=$?

exit "$rc"
