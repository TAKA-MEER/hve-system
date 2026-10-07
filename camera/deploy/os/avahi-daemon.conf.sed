# P-12: avahi に wlan0 を見させる（hve-lift.local を引くため）
s|^allow-interfaces=eth0, wlan1[ ]*$|allow-interfaces=eth0, wlan0, wlan1|
