#!/bin/sh
# PC から UnitV2 へアプリを配備する（WP-CAM-05）。
#
#   camera/deploy/install.sh [UnitV2 のアドレス]
#
# アドレスの既定は USB の有線の固定アドレス 10.254.239.1（環境変数 HVE_UNITV2_HOST でも指定できる）。
# ユーザー・パスワードは m5stack ／ 12345678（HVE_UNITV2_USER・HVE_UNITV2_PASS で変えられる）。
# 必要なもの: ssh・sshpass・tar（PC 側）。scp は UnitV2 で使えないので tar を ssh に流す。
#
# やること: アプリを /home/m5stack/hve/camera/ へ送る → OS の原本を /home/m5stack/hve_backup/ へ退避 →
#   OS の小さな変更（P-11・P-12・P-13・S85runpayload の自動起動を外す・S86hve を入れる）→ アプリを起動。
# 戻すのは camera/deploy/uninstall.sh。

set -e

HOST=${1:-${HVE_UNITV2_HOST:-10.254.239.1}}
USER=${HVE_UNITV2_USER:-m5stack}
PASS=${HVE_UNITV2_PASS:-12345678}
CAMERA_DIR=$(cd "$(dirname "$0")/.." && pwd)

SSH="sshpass -p $PASS ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $USER@$HOST"

echo "== $HOST へ配備する =="
$SSH 'mkdir -p /home/m5stack/hve/camera'
# 古いファイルが残らないよう、アプリのディレクトリを消してから展開する（config/params.toml も入れ替わる）
$SSH 'cd /home/m5stack/hve/camera && rm -rf hve_camera hve_video config web vendor deploy'
tar c --format=ustar \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='node_modules' \
    --exclude='package-lock.json' --exclude='tests' \
    -C "$CAMERA_DIR" hve_camera hve_video config web vendor deploy \
    | $SSH 'tar x -C /home/m5stack/hve/camera'

echo "== OS の変更とアプリの起動（root） =="
echo "$PASS" | $SSH 'sudo -S sh /home/m5stack/hve/camera/deploy/apply_os.sh'

echo "== 配備した。確認の手順は docs/使い方.md の v2 の節 =="
