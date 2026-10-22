"""Clientes boto3 para discovery multirregiao/multiaccount.

Em conta diferente, exige role_arn explicito; nunca usar credenciais de conta errada.
"""
import boto3
import os
from botocore.config import Config

AWS_CONFIG = Config(retries={"mode":"standard","max_attempts":5},
                    connect_timeout=10, read_timeout=30)


def create_session(account):
    selected_profile = os.getenv("REDIS_AWS_PROFILE") or account.get("profile")
    base = boto3.Session(profile_name=selected_profile) if selected_profile else boto3.Session()
    session = base
    if account.get("role_arn"):
        response = base.client("sts", config=AWS_CONFIG).assume_role(
            RoleArn=account["role_arn"], RoleSessionName="RedisObservabilityDiscovery")
        c = response["Credentials"]
        session = boto3.Session(aws_access_key_id=c["AccessKeyId"],
                               aws_secret_access_key=c["SecretAccessKey"],
                               aws_session_token=c["SessionToken"])
    actual_account = session.client("sts", config=AWS_CONFIG).get_caller_identity()["Account"]
    expected_account = str(account["account_id"])
    if actual_account != expected_account:
        raise RuntimeError(f"Conta AWS incorreta: esperado {expected_account}, recebido {actual_account}")
    return session


def create_elasticache_client(session, region):
    return session.client("elasticache", region_name=region, config=AWS_CONFIG)
