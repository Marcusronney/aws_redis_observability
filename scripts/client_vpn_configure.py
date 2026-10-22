"""Build a restricted OpenVPN client config; do not rewrite exported credentials.

Run on Rocky after exporting AWS's .ovpn, before starting systemd.
The source CA remains embedded. The client certificate/key remain separate.
"""
import argparse
from pathlib import Path


def render(source):
    forbidden = ("redirect-gateway", "auth-user-pass", "<key>", "<cert>", "script-security", "up ", "down ")
    if any(line.strip().startswith(forbidden) for line in source.splitlines()):
        raise ValueError("Unexpected routing, credentials or scripts in exported config")
    return source.rstrip() + "\n" + "\n".join([
        "cert /etc/redis-observability/client-vpn/pki/rocky-192-168-3-55.crt",
        "key /etc/redis-observability/client-vpn/pki/rocky-192-168-3-55.key",
        "dev tun-redis-sa",
        'pull-filter ignore "redirect-gateway"',
        'pull-filter ignore "dhcp-option DNS"',
        "route-nopull",
        "route 172.31.0.0 255.255.0.0",
        "auth-nocache",
        "persist-key",
        "persist-tun",
        "resolv-retry infinite",
        "verb 3",
    ]) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exported", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # AWS export already contains `dev tun`; select a predictable interface.
    source = "\n".join(line for line in args.exported.read_text().splitlines()
                       if not line.strip().startswith("dev "))
    result = render(source)
    with args.output.open("x") as stream:
        args.output.chmod(0o600)
        stream.write(result)
