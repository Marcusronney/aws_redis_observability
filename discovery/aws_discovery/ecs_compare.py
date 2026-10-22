"""Comparacao read-only entre Redis descoberto e coletor ECS."""

import yaml

AMP_REGION = "us-east-1"
AMP_WORKSPACE = "ws-a32bf55d-d10a-4d22-9873-a937f133db67"


def compare_collector(
    ecs, ec2, service, account_id, region,
    redis_name, endpoint, redis_vpc, redis_security_groups, tls,
):
    checks = {}
    details = []

    checks["service_active"] = service["status"] == "ACTIVE"
    checks["desired_count"] = service["desired"] == 1
    checks["running_count"] = service["running"] == 1

    definition = ecs.describe_task_definition(
        taskDefinition=service["task_definition"]
    )["taskDefinition"]

    containers = {
        c["name"]: c
        for c in definition.get("containerDefinitions", [])
    }

    checks["containers_present"] = all(
        name in containers
        for name in ("redis-exporter", "adot-collector")
    )

    if not checks["containers_present"]:
        return {
            "matches": False,
            "checks": checks,
            "reason": "Containers obrigatorios ausentes",
        }

    exporter = {
        item["name"]: item["value"]
        for item in containers["redis-exporter"].get("environment", [])
    }

    adot = {
        item["name"]: item["value"]
        for item in containers["adot-collector"].get("environment", [])
    }

    expected_scheme = "rediss" if tls else "redis"
    checks["redis_endpoint"] = (
        exporter.get("REDIS_ADDR")
        == f"{expected_scheme}://{endpoint}"
    )

    try:
        config = yaml.safe_load(adot["AOT_CONFIG_CONTENT"])
        scrape = config["receivers"]["prometheus"][
            "config"
        ]["scrape_configs"][0]

        labels = scrape["static_configs"][0]["labels"]

        remote = (
            config["exporters"].get("prometheusremotewrite")
            or config["exporters"].get("prometheus_remote_write")
            or {}
        )

        sigv4 = config.get("extensions", {}).get("sigv4auth", {})

        checks["account_label"] = (
            str(labels.get("aws_account_id")) == str(account_id)
        )
        checks["region_label"] = labels.get("aws_region") == region
        checks["redis_label"] = labels.get("redis_name") == redis_name
        checks["amp_endpoint"] = (
            f"/workspaces/{AMP_WORKSPACE}/api/v1/remote_write"
            in remote.get("endpoint", "")
        )
        checks["sigv4"] = (
            sigv4.get("region") == AMP_REGION
            and sigv4.get("service") == "aps"
            and remote.get("auth", {}).get("authenticator") == "sigv4auth"
        )

    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        checks["adot_configuration"] = False
        details.append(f"Configuracao ADOT invalida: {exc}")

    network = service.get("network", {}).get("awsvpcConfiguration", {})
    collector_subnets = network.get("subnets", [])
    collector_sgs = network.get("securityGroups", [])

    checks["network_defined"] = bool(
        collector_subnets and collector_sgs
    )

    if checks["network_defined"]:
        subnet_data = ec2.describe_subnets(
            SubnetIds=collector_subnets
        )["Subnets"]

        checks["same_vpc"] = (
            bool(subnet_data)
            and all(s["VpcId"] == redis_vpc for s in subnet_data)
        )

        redis_sg_data = ec2.describe_security_groups(
            GroupIds=redis_security_groups
        )["SecurityGroups"]

        allowed = False

        for group in redis_sg_data:
            for rule in group.get("IpPermissions", []):
                protocol = str(rule.get("IpProtocol"))

                valid_port = (
                    protocol == "-1"
                    or (
                        protocol == "tcp"
                        and rule.get("FromPort", 65536) <= int(endpoint.rsplit(":", 1)[1])
                        <= rule.get("ToPort", -1)
                    )
                )

                sources = {
                    entry["GroupId"]
                    for entry in rule.get("UserIdGroupPairs", [])
                    if entry.get("GroupId")
                }

                if valid_port and sources.intersection(collector_sgs):
                    allowed = True

        checks["redis_sg_permission"] = allowed
    else:
        checks["same_vpc"] = False
        checks["redis_sg_permission"] = False

    matches = all(checks.values())

    return {
        "matches": matches,
        "checks": checks,
        "reason": (
            "Configuracao consistente"
            if matches else "Divergencias encontradas"
        ),
        "details": details,
    }
