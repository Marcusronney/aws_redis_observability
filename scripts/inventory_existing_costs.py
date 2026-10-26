"""Read-only inventory for a separately reviewed AWS shutdown plan.

No stop/delete calls. Stores selected metadata only; never task env, credentials or logs.
Inventory is account-wide and must NOT be interpreted as authorization to delete it.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import boto3
from botocore.config import Config


def inventory(profile, regions):
    session = boto3.Session(profile_name=profile)
    settings = Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 2})
    result = {'generated_at': datetime.now(timezone.utc).isoformat(),
        'account_id': session.client('sts', config=settings).get_caller_identity()['Account'],
        'read_only': True, 'destruction_authorized': False, 'regions': {}, 'errors': []}
    for region in regions:
        resources = result['regions'][region] = {}
        def collect(kind, function):
            try:
                resources[kind] = function()
            except Exception as exc:
                # SDK error bodies can contain identifiers beyond the selected metadata.
                result['errors'].append({'region': region, 'kind': kind, 'error_type': type(exc).__name__})
        ec = session.client('elasticache', region_name=region, config=settings)
        ec2 = session.client('ec2', region_name=region, config=settings)
        ecs = session.client('ecs', region_name=region, config=settings)
        amp = session.client('amp', region_name=region, config=settings)
        def groups():
            return [dict(id=x['ReplicationGroupId'], arn=x.get('ARN'), status=x['Status'],
                members=x.get('MemberClusters', []), snapshot_retention=x.get('SnapshotRetentionLimit'))
                for page in ec.get_paginator('describe_replication_groups').paginate()
                for x in page['ReplicationGroups']]
        collect('replication_groups', groups)
        collect('serverless', lambda: [dict(id=x['ServerlessCacheName'], arn=x.get('ARN'), status=x['Status'],
            snapshot_retention=x.get('SnapshotRetentionLimit'))
            for page in ec.get_paginator('describe_serverless_caches').paginate() for x in page['ServerlessCaches']])
        collect('instances', lambda: [dict(id=x['InstanceId'], state=x['State']['Name'], type=x['InstanceType'],
            vpc=x.get('VpcId'), tags=x.get('Tags', []))
            for page in ec2.get_paginator('describe_instances').paginate()
            for r in page['Reservations'] for x in r['Instances'] if x['State']['Name'] != 'terminated'])
        collect('volumes', lambda: [dict(id=x['VolumeId'], state=x['State'], size_gib=x['Size'], tags=x.get('Tags', []),
            instances=[a['InstanceId'] for a in x.get('Attachments', [])])
            for page in ec2.get_paginator('describe_volumes').paginate() for x in page['Volumes']])
        collect('elastic_ips', lambda: [dict(allocation=x.get('AllocationId'), instance=x.get('InstanceId'),
            association=x.get('AssociationId'), tags=x.get('Tags', [])) for x in ec2.describe_addresses()['Addresses']])
        collect('nat_gateways', lambda: [dict(id=x['NatGatewayId'], state=x['State'], vpc=x['VpcId'], tags=x.get('Tags', []))
            for page in ec2.get_paginator('describe_nat_gateways').paginate() for x in page['NatGateways']
            if x['State'] != 'deleted'])
        collect('client_vpn', lambda: [dict(id=x['ClientVpnEndpointId'], status=x['Status']['Code'])
            for page in ec2.get_paginator('describe_client_vpn_endpoints').paginate() for x in page['ClientVpnEndpoints']])
        def services():
            output = []
            for page in ecs.get_paginator('list_clusters').paginate():
                for cluster in page['clusterArns']:
                    for batch in ecs.get_paginator('list_services').paginate(cluster=cluster):
                        arns = batch['serviceArns']
                        for offset in range(0, len(arns), 10):
                            for x in ecs.describe_services(cluster=cluster, services=arns[offset:offset + 10])['services']:
                                output.append(dict(cluster=cluster, name=x['serviceName'], arn=x['serviceArn'],
                                    desired=x['desiredCount'], running=x['runningCount'], task_definition=x['taskDefinition']))
            return output
        collect('ecs_services', services)
        collect('amp_workspaces', lambda: [dict(id=x['workspaceId'], arn=x['arn'], alias=x.get('alias'), status=x['status'])
            for page in amp.get_paginator('list_workspaces').paginate() for x in page['workspaces']])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='redis-observability-admin')
    parser.add_argument('--regions', nargs='+', default=['us-east-1', 'sa-east-1'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.profile, args.regions)
    args.output.write_text(json.dumps(result, indent=2))
    print('READ_ONLY_INVENTORY: regions=' + str(len(result['regions'])) + ' errors=' + str(len(result['errors'])))
    if result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
