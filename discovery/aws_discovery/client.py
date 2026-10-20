
import boto3
import os
from botocore.config import Config


AWS_CONFIG = Config(
    retries={
        "mode": "standard",
        "max_attempts": 5,
    },
    connect_timeout=10,
    read_timeout=30,
)


def create_session(account):
    profile = account.get("profile")
    expected_account = str(account["account_id"])

    selected_profile = os.getenv("REDIS_AWS_PROFILE") or profile

    if selected_profile:
        session = boto3.Session(profile_name=selected_profile)
    else:
        session = boto3.Session()

    sts = session.client("sts", config=AWS_CONFIG)
    identity = sts.get_caller_identity()

    actual_account = identity["Account"]

    if actual_account != expected_account:
        raise RuntimeError(
            f"Conta AWS incorreta: esperado "
            f"{expected_account}, recebido {actual_account}"
        )

    return session


def create_elasticache_client(session, region):
    return session.client(
        "elasticache",
        region_name=region,
        config=AWS_CONFIG,
    )
