
def list_paginated(client, operation, response_key, **kwargs):
    paginator = client.get_paginator(operation)
    resources = []

    for page in paginator.paginate(**kwargs):
        resources.extend(page.get(response_key, []))

    return resources


def discover_resources(client):
    clusters = list_paginated(
        client,
        "describe_cache_clusters",
        "CacheClusters",
        ShowCacheNodeInfo=True,
    )

    replication_groups = list_paginated(
        client,
        "describe_replication_groups",
        "ReplicationGroups",
    )

    serverless = list_paginated(
        client,
        "describe_serverless_caches",
        "ServerlessCaches",
    )

    return {
        "clusters": clusters,
        "replication_groups": replication_groups,
        "serverless": serverless,
    }


def get_resource_tags(client, arn):
    if not arn:
        return {}

    response = client.list_tags_for_resource(
        ResourceName=arn
    )

    return {
        item["Key"]: item.get("Value", "")
        for item in response.get("TagList", [])
    }
