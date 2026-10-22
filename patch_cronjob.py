"""Habilita `regions: auto` opcional no Discovery existente; cria backup."""
import argparse
from pathlib import Path

BEGIN = '''\ndef configured_regions(session, account):
    configured = account.get("regions", [])
    if configured != "auto":
        if not isinstance(configured, list):
            raise ValueError("regions deve ser lista ou auto")
        return configured
    enabled = {
        r["RegionName"]
        for r in session.client("ec2", region_name="us-east-1")
        .describe_regions(AllRegions=False)["Regions"]
    }
    supported = set(session.get_available_regions("elasticache"))
    exclusions = set(account.get("exclude_regions", []))
    return sorted((enabled & supported) - exclusions)
\n'''


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--file',type=Path,default=Path('discovery/aws_discovery/__main__.py'))
    a=p.parse_args()
    src=a.file.read_text(encoding='utf-8')
    old='for region in account["regions"]:'
    if 'def configured_regions' in src:
        print('Ja atualizado')
        return
    if src.count(old) != 1:
        raise ValueError('Estrutura inesperada de __main__.py; nada alterado')
    bak=a.file.with_name('__main__.py.pre-auto.bak')
    if not bak.exists():
        bak.write_text(src,encoding='utf-8')
    # Define funcao antes de main para evitar depender de ordem de import.
    marker='def main():'
    if src.count(marker) != 1:
        raise ValueError('Funcao main inesperada')
    src=src.replace(marker, BEGIN + marker, 1)
    src=src.replace(old, 'for region in configured_regions(session, account):', 1)
    a.file.write_text(src,encoding='utf-8')
    print('Auto-regions habilitado; so entra em vigor com regions: auto')

if __name__=='__main__':
    main()
