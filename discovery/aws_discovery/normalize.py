
def normalize_endpoint(value):
    if not isinstance(value, dict):
        return None

    address = value.get("Address")
    port = value.get("Port")

    if not address or port is None:
        return None

    return {
        "address": address,
        "port": port,
    }


def allowed_labels(tags, tag_mapping):
    return {
        label: str(tags[key])
        for key, label in tag_mapping.items()
        if key in tags
    }


def add_endpoint(items, value, role):
    normalized = normalize_endpoint(value)

    if normalized:
        entry = {
            **normalized,
            "role": role,
        }

        if entry not in items:
            items.append(entry)


def normalize_cluster(resource, labels):
    endpoints = []

    add_endpoint(
        endpoints,
        resource.get("ConfigurationEndpoint"),
        "configuration",
    )

    for node in resource.get("CacheNodes", []):
        add_endpoint(
            endpoints,
            node.get("Endpoint"),
            "node",
        )

    return {
        "type": "cluster",
        "nodes": [{'node_id': node['CacheNodeId'],
                   'endpoint': normalize_endpoint(node.get('Endpoint'))}
                  for node in resource.get('CacheNodes', []) if node.get('CacheNodeId')],
        "id": resource["CacheClusterId"],
        "arn": resource.get("ARN"),
        "engine": resource.get("Engine"),
        "status": resource.get("CacheClusterStatus"),
        "replication_group_id": resource.get(
            "ReplicationGroupId"
        ),
        "cache_subnet_group": resource.get("CacheSubnetGroupName"),
        "transit_encryption": resource.get(
            "TransitEncryptionEnabled"
        ),
        "auth_token_enabled": resource.get(
            "AuthTokenEnabled"
        ),
        "endpoints": endpoints,
        "labels": labels,
    }


def normalize_replication_group(resource, labels):
    endpoints = []
    nodes = []

    add_endpoint(
        endpoints,
        resource.get("ConfigurationEndpoint"),
        "configuration",
    )

    add_endpoint(
        endpoints,
        resource.get("ReaderEndpoint"),
        "reader",
    )

    for group in resource.get("NodeGroups", []):
        shard_id = group.get("NodeGroupId", "unknown")

        add_endpoint(
            endpoints,
            group.get("PrimaryEndpoint"),
            f"primary:{shard_id}",
        )

        add_endpoint(
            endpoints,
            group.get("ReaderEndpoint"),
            f"reader:{shard_id}",
        )

        for member in group.get("NodeGroupMembers", []):
            endpoint = normalize_endpoint(member.get('ReadEndpoint'))
            if member.get('CacheClusterId') and member.get('CacheNodeId'):
                nodes.append({
                    'cluster_id': member['CacheClusterId'], 'node_id': member['CacheNodeId'],
                    'endpoint': endpoint, 'role': member.get('CurrentRole', 'unknown'),
                    'availability_zone': member.get('PreferredAvailabilityZone', 'unknown'),
                    'shard_id': shard_id})
            add_endpoint(
                endpoints,
                member.get("ReadEndpoint"),
                "node_read",
            )

    return {
        "type": "replication_group",
        "nodes": nodes,
        "user_group_ids": resource.get("UserGroupIds", []),
        "id": resource["ReplicationGroupId"],
        "arn": resource.get("ARN"),
        "status": resource.get("Status"),
        "cluster_enabled": resource.get(
            "ClusterEnabled"
        ),
        "member_clusters": resource.get(
            "MemberClusters", []
        ),
        "transit_encryption": resource.get(
            "TransitEncryptionEnabled"
        ),
        "auth_token_enabled": resource.get(
            "AuthTokenEnabled"
        ),
        "endpoints": endpoints,
        "labels": labels,
    }


def normalize_serverless(resource, labels):
    endpoints = []

    add_endpoint(
        endpoints,
        resource.get("Endpoint"),
        "primary",
    )

    add_endpoint(
        endpoints,
        resource.get("ReaderEndpoint"),
        "reader",
    )

    return {
        "type": "serverless",
        "id": resource["ServerlessCacheName"],
        "arn": resource.get("ARN"),
        "engine": resource.get("Engine"),
        "status": resource.get("Status"),
        "subnet_ids": resource.get("SubnetIds", []),
        "user_group_id": resource.get("UserGroupId"),
        "endpoints": endpoints,
        "labels": labels,
    }
