#!/usr/bin/env bash
# Run on Rocky as root. Never copy the resulting directory into Git.
set -euo pipefail
umask 077
cert_dir=/etc/redis-observability/client-vpn/pki
if [[ -e "$cert_dir" ]]; then
  echo "PKI already exists; refusing to replace certificates" >&2
  exit 1
fi
install -d -m 700 "$cert_dir"
cd "$cert_dir"
openssl genrsa -out ca.key 2048
openssl req -x509 -new -sha256 -key ca.key -days 3650 -out ca.crt \
  -subj /CN=redis-observability-client-vpn-lab-ca
for identity in server rocky-192-168-3-55; do
  openssl genrsa -out "$identity.key" 2048
  openssl req -new -key "$identity.key" -out "$identity.csr" -subj "/CN=$identity"
  if [[ "$identity" == server ]]; then
    printf 'extendedKeyUsage=serverAuth\nsubjectAltName=DNS:server\n' > "$identity.ext"
  else
    printf 'extendedKeyUsage=clientAuth\n' > "$identity.ext"
  fi
  openssl x509 -req -sha256 -in "$identity.csr" -CA ca.crt -CAkey ca.key \
    -CAcreateserial -out "$identity.crt" -days 365 -extfile "$identity.ext"
  openssl verify -CAfile ca.crt "$identity.crt"
done
echo "Exclusive PKI created in $cert_dir; private keys were not printed."
