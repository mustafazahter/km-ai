#!/bin/sh
# Konteyner giriş betiği: yalnız /data yazılabilir mi diye bakar, uyarır ve uygulamayı çalıştırır.
# Container entrypoint: only checks that /data is writable, warns, and execs the app.
#
# İmaj `USER 1000` ile çalışır ve /data imajda 1000'e aittir. Bu betik disk sahipliğine ASLA
# dokunmaz (chown yok, yetki değiştirme yok): platformun disk hazırlama adımı diske yazabilmeli,
# chown edilen disk sonraki yayının başlamasını engeller. Root ile çalıştırılsa bile bir şey değiştirmez.
set -eu

DATA_DIR="${DATA_DIR:-/data}"

if ! ( [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ] ); then
  echo "entrypoint: WARNING $DATA_DIR is not writable by uid $(id -u); memories/schedules cannot be saved. The app expects uid 1000 (local bind mount: chown 1000:1000 ./data)" >&2
fi
exec "$@"
