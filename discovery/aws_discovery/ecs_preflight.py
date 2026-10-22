"""Preflight read-only de rede para coletores ECS Fargate."""

import argparse
import json
from pathlib import Path

import boto3


def classify_route_table(route_table):
    for route in route_table.get("Routes", []):
        if route.get("DestinationCidrBlock") != "0.0.0.0/0":
            continue

        if route.get("State") != "active":
            continue

        gateway = route.get("GatewayId", "")
        nat = route.get("NatGatewayId", "")

        if gateway.startswith("igw-"):
            return "PUBLIC_IGW", "ENABLED"

        if nat.startswith("nat-"):
            return "PRIVATE_NAT", "DISABLED"

    return None, None


def inspect_vpc_network(ec2, vpc_id):
    subnets = []

    paginator = ec2.get_paginator("describe_subnets")

    for page in paginator.paginate(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ):
        subnets.extend(page["Subnets"])

    route_tables = []

    paginator = ec2.get_paginator("describe_route_tables")

    for page in paginator.paginate(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ):
        route_tables.extend(page["RouteTables"])

    main_table = next(
        (
            table
            for table in route_tables
            if any(
                assoc.get("Main", False)
                for assoc in table.get("Associations", [])
            )
        ),
        None,
    )

    candidates = []

    for subnet in subnets:
        subnet_id = subnet["SubnetId"]

        if subnet.get("State") != "available":
            continue

        explicit = next(
            (
                table
                for table in route_tables
                if any(
                    assoc.get("SubnetId") == subnet_id
                    and assoc.get("AssociationState", {}).get(
                        "State", "associated"
                    ) == "associated"
                    for assoc in table.get("Associations", [])
                )
            ),
            None,
        )

        table = explicit or main_table

        if table is None:
            continue

        network_type, public_ip = classify_route_table(table)

        if network_type is None:
            continue

        candidates.append({
            "subnet_id": subnet_id,
            "availability_zone": subnet["AvailabilityZone"],
            "route_table": table["RouteTableId"],
            "network_type": network_type,
            "assign_public_ip": public_ip,
        })

    candidates.sort(
        key=lambda item: (
            item["network_type"] != "PRIVATE_NAT",
            item["availability_zone"],
            item["subnet_id"],
        )
    )

    return candidates


def build_plan(source, profile):
    if source.get("mode") != "COLLECTION_POLICY_DRY_RUN":
        raise ValueError("Entrada incorreta")

    resources = []

    for item in source["resources"]:
        result = {
            "redis": item["redis"],
            "region": item["region"],
            "account_id": item["account_id"],
            "collection_mode": item["collection_mode"],
            "original_action": item["planned_action"],
            "preflight_action": None,
            "network_candidates": [],
            "blockers": [],
        }

        if item["collection_mode"] == "LOCAL":
            result["preflight_action"] = "KEEP_LOCAL"
            resources.append(result)
            continue

        if item["collection_mode"] == "BLOCKED":
            result["preflight_action"] = "SKIP"
            resources.append(result)
            continue

        if item["planned_action"] == "UNCHANGED":
            result["preflight_action"] = "UNCHANGED"
            resources.append(result)
            continue

        if item["planned_action"] == "REVIEW_UPDATE":
            result["preflight_action"] = "MANUAL_REVIEW"
            result["blockers"].append(
                "Atualizacao automatica ainda nao habilitada"
            )
            resources.append(result)
            continue

        if item["planned_action"] != "REVIEW_CREATE":
            raise ValueError("Acao desconhecida")

        session = boto3.Session(profile_name=profile)

        identity = session.client("sts").get_caller_identity()

        if identity["Account"] != str(item["account_id"]):
            result["preflight_action"] = "BLOCKED"
            result["blockers"].append(
                "Conta AWS diferente da conta descoberta"
            )
            resources.append(result)
            continue

        ec2 = session.client("ec2", region_name=item["region"])

        candidates = inspect_vpc_network(
            ec2, item["vpc"]
        )

        result["network_candidates"] = candidates

        if not candidates:
            result["blockers"].append(
                "Nenhuma subnet com rota IGW/NAT elegivel"
            )

        auth = item.get("authentication")

        if auth == "ENABLED" or auth == "USER_GROUP_CONFIGURED":
            result["blockers"].append(
                "Autenticacao requer Secret e configuracao do exporter"
            )
        elif auth == "REQUIRES_VALIDATION":
            result["blockers"].append(
                "Autenticacao ainda nao validada"
            )
        else:
            result["blockers"].append(
                "Estado de autenticacao desconhecido"
            )

        result["preflight_action"] = (
            "BLOCKED"
            if result["blockers"]
            else "CREATE_CANDIDATE"
        )

        resources.append(result)

    return {
        "mode": "ECS_PREFLIGHT_READ_ONLY",
        "resources": resources,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile",
        default="redis-observability-admin",
    )

    args = parser.parse_args()

    source = json.loads(
        args.input.read_text(encoding="utf-8")
    )

    plan = build_plan(source, args.profile)

    args.output.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for item in plan["resources"]:
        print(
            item["region"],
            item["redis"],
            item["preflight_action"],
        )
        for blocker in item["blockers"]:
            print("  BLOQUEIO:", blocker)

    print("READ_ONLY: nenhuma alteracao na AWS")


if __name__ == "__main__":
    main()
