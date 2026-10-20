# P-13: grace.ko を起動で読ませない（grace_init_net でデータアボートし、RCU を止めて wpa_supplicant を固める）
s|^\([ 	]*\)do /sbin/insmod \$kofile$|\1do [ "$kofile" = /lib/modules/grace.ko ] \&\& continue; /sbin/insmod $kofile|
