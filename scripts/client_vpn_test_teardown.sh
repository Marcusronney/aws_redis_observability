#!/usr/bin/env bash
# Dedicated pilot only. Existing Redis/ECS/AMP/WireGuard are never destroyed.
set -uo pipefail
source_dir=/root/redis-vpn-test-source
session_dir=/root/redis-vpn-session
export AWS_PROFILE=redis-observability-admin AWS_DEFAULT_REGION=sa-east-1 AWS_PAGER=''
failed=0
if [[ -f "$session_dir/teardown-complete" ]]; then
  echo 'Pilot teardown already completed.'
  exit 0
fi
if [[ -e "$session_dir/cluster-changed" ]]; then
  python3 "$source_dir/scripts/client_vpn_restore.py" --session "$session_dir" || failed=1
fi
if [[ -f "$session_dir/sa-test-tags-added" ]]; then
  # Only the two previously absent opt-in tags temporarily added by this test.
  aws elasticache remove-tags-from-resource --region sa-east-1 \
    --resource-name arn:aws:elasticache:sa-east-1:762012032320:serverlesscache:redis-observability-sa-test \
    --tag-keys RedisObservability RedisObservabilityAuth || failed=1
fi
if [[ -e "$session_dir/pod-created" ]]; then
  kubectl delete pod redis-client-vpn-check -n redis-observability --ignore-not-found=true || failed=1
fi
systemctl stop redis-vpn-prometheus-forward.service 2>/dev/null || true
if systemctl cat openvpn-client@redis-sa.service >/dev/null 2>&1; then
  systemctl disable --now openvpn-client@redis-sa.service || failed=1
fi
bash "$source_dir/scripts/client_vpn_kind_forwarding.sh" remove || failed=1
module_dir="$source_dir/terraform/environments/client-vpn"
if [[ -f "$module_dir/terraform.tfstate" ]]; then
  terraform -chdir="$module_dir" plan -destroy -input=false -out=teardown.tfplan || exit 1
  terraform -chdir="$module_dir" show -json teardown.tfplan > "$session_dir/teardown-plan.json" || exit 1
  python3 - "$session_dir/teardown-plan.json" <<'PY' || exit 1
import json, sys
allowed = {'aws_security_group','aws_vpc_security_group_egress_rule',
           'aws_vpc_security_group_ingress_rule','aws_cloudwatch_log_group',
           'aws_cloudwatch_log_stream','aws_ec2_client_vpn_endpoint',
           'aws_ec2_client_vpn_network_association','aws_ec2_client_vpn_authorization_rule'}
plan = json.load(open(sys.argv[1]))
for item in plan.get('resource_changes', []):
    if item.get('mode') == 'data':
        continue
    assert item['type'] in allowed, item['address']
    assert item['change']['actions'] in (['delete'], ['no-op']), item['address']
print('Verified: destroy plan contains only exclusive Client VPN pilot resources')
PY
  terraform -chdir="$module_dir" apply -input=false teardown.tfplan || exit 1
fi
for arn_file in "$session_dir/acm-server-arn" "$session_dir/acm-server-updated-arn"; do
  [[ -f "$arn_file" ]] || continue
  cert_arn=$(cat "$arn_file")
  aws acm describe-certificate --region sa-east-1 --certificate-arn "$cert_arn" \
    --query Certificate.InUseBy --output json > "$session_dir/certificate-in-use.json" || failed=1
  if [[ $(cat "$session_dir/certificate-in-use.json") == '[]' ]]; then
    aws acm delete-certificate --region sa-east-1 --certificate-arn "$cert_arn" || failed=1
  else
    echo 'Certificate still in use; retained' >&2
    failed=1
  fi
done
if [[ "$failed" == 0 ]]; then
  date -u +%FT%TZ > "$session_dir/teardown-complete"
  echo 'Pilot removed; previous monitoring restored. Existing AWS collectors retained.'
fi
exit "$failed"
