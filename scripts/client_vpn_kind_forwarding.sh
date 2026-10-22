#!/usr/bin/env bash
# Narrow, reversible rules. Inspect existing routing/firewall BEFORE running.
set -euo pipefail
action=${1:-check}
vpn_if=tun-redis-sa
kind_cidr=172.22.0.0/16
vpc_cidr=172.31.0.0/16
[[ "$action" =~ ^(check|apply|remove)$ ]] || exit 2
if [[ "$action" == check ]]; then
  ip -4 route
  ip -4 addr show "$vpn_if"
  sysctl net.ipv4.ip_forward
  iptables -S DOCKER-USER
  iptables -t nat -S POSTROUTING
  exit
fi
[[ $(sysctl -n net.ipv4.ip_forward) == 1 ]] || {
  echo 'IP forwarding disabled; inspect host configuration first' >&2; exit 1;
}
if [[ "$action" == apply ]]; then
  ip link show "$vpn_if" >/dev/null
  ip route get 172.31.0.1 | grep -q "dev $vpn_if" || {
    echo 'VPC route does not use the Client VPN interface' >&2; exit 1;
  }
fi
rule() {
  local table=$1 chain=$2; shift 2
  if [[ "$action" == apply ]]; then
    iptables -t "$table" -C "$chain" "$@" 2>/dev/null || iptables -t "$table" -I "$chain" 1 "$@"
  else
    if iptables -t "$table" -C "$chain" "$@" 2>/dev/null; then
      iptables -t "$table" -D "$chain" "$@"
    fi
  fi
}
# Kind nodes already SNAT Pod traffic to the Docker network. No global ACCEPT.
rule filter DOCKER-USER -s "$kind_cidr" -d "$vpc_cidr" -o "$vpn_if" \
  -p tcp --dport 6379 -m comment --comment redis-client-vpn-lab -j ACCEPT
rule filter DOCKER-USER -s "$vpc_cidr" -d "$kind_cidr" -i "$vpn_if" \
  -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment redis-client-vpn-lab -j ACCEPT
rule nat POSTROUTING -s "$kind_cidr" -d "$vpc_cidr" -o "$vpn_if" \
  -p tcp --dport 6379 -m comment --comment redis-client-vpn-lab -j MASQUERADE
