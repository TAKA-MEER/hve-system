#!/bin/sh
# 配備を戻す（WP-CAM-05）。OS のファイルを退避した原本のままに戻し、S86hve を外し、
# 組み込みのサービス（S85runpayload）の自動起動を戻す。
#
#   camera/deploy/uninstall.sh [UnitV2 のアドレス] [--purge]
#
# --purge: アプリ本体（/home/m5stack/hve）とログも消す。原本のバックアップ（hve_backup）は残す。
# 戻したあと、組み込みのサービスと自前の AP は次の再起動で戻る。

set -e

HOST=""
PURGE=0
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        *) HOST=$a ;;
    esac
done
HOST=${HOST:-${HVE_UNITV2_HOST:-10.254.239.1}}
USER=${HVE_UNITV2_USER:-m5stack}
PASS=${HVE_UNITV2_PASS:-12345678}

SSH="sshpass -p $PASS ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $USER@$HOST"

echo "== $HOST の配備を戻す =="
# 退避してある restore_os.sh を使う（アプリを消したあとでも戻せるように）
echo "$PASS" | $SSH 'sudo -S sh /home/m5stack/hve_backup/restore_os.sh'
if [ "$PURGE" = 1 ]; then
    $SSH 'rm -rf /home/m5stack/hve /home/m5stack/hve_log'
    echo "アプリとログを消した"
fi
echo "== 戻した。組み込みのサービスと自前の AP を戻すには UnitV2 を再起動する =="
