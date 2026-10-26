"""Reproduce the project diagram as editable SVG and PNG (Windows/System.Drawing)."""
import html
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'imagens'
W, H = 3200, 2180
C = dict(ink='#172B49', muted='#51647E', blue='#1672CF', green='#12835E',
         purple='#7551C4', orange='#D77913', line='#D5DFEB')
commands = []


def box(x, y, w, h, fill='#FFFFFF', stroke=C['line'], radius=18, dash=False):
    commands.append(dict(op='box', x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, radius=radius, dash=dash))


def text(x, y, value, size=25, color=C['ink'], bold=False, width=1100):
    commands.append(dict(op='text', x=x, y=y, value=value, size=size, color=color, bold=bold, width=width))


def arrow(points, color=C['blue'], dash=False, width=4):
    commands.append(dict(op='arrow', points=points, color=color, dash=dash, width=width))


def card(x, y, w, h, title, lines=(), color=C['blue'], badge=None, dash=False, size=25):
    box(x, y, w, h, stroke=color, dash=dash)
    offset = 22
    if badge:
        box(x+18, y+18, 55, 46, fill=color, stroke=color, radius=10)
        text(x+25, y+25, badge, 18, '#FFFFFF', True, 45)
        offset = 88
    text(x+offset, y+20, title, size, color, True, w-offset-18)
    for i, line in enumerate(lines):
        text(x+22, y+65+i*30, line, 22, width=w-42)


def draw():
    box(0, 0, W, H, fill='#F4F7FC', stroke='#F4F7FC', radius=0)
    text(60, 30, 'AWS Redis Observability', 60, bold=True, width=2100)
    text(60, 105, 'Kind fora da AWS • descoberta automática • Vault • exporters • Prometheus', 31, width=2900)
    text(2540, 55, 'Arquitetura • 26/10/2026', 25, C['muted'], width=600)
    # Large visual boundaries.
    for x, w, title, color in [(60,1180,'1  Ambiente local • Rocky Linux',C['blue']),
                               (1280,700,'2  Descoberta e reconciliação',C['purple']),
                               (2020,1120,'3  AWS • conta 762012032320',C['orange'])]:
        box(x,190,w,1610,stroke=color)
        text(x+25,210,title,32,color,True,w-45)
    text(90,260,'VM 192.168.3.55 • Docker • controle e coleta no ambiente local',24,width=1130)
    card(100,310,520,145,'Terraform',[
        'vault-lab • network-identity • network-link',
        'ON/OFF • IAM • objetos K8s • estados'],badge='TF')
    card(650,310,550,145,'Timer regional na VM',[
        'redis-regions.timer / service',
        'automatic_regions.py • status.json'],color=C['purple'],badge='PY')
    box(100,505,1100,1120,fill='#F1F7FF',stroke=C['blue'])
    text(125,522,'Kind: redis-monitoring  |  control-plane + worker',29,C['blue'],True,1050)
    box(130,585,1040,645,fill='#FFFFFF',stroke='#98BCE6')
    text(150,598,'Namespace redis-vault-lab',27,C['blue'],True,950)
    card(165,655,435,135,'CronJob → Job → Pod Python',[
        'discovery • execução a cada minuto',
        'APIs AWS • validação • reconciliação'],badge=None,size=25)
    card(650,655,485,135,'Configuração e identidade',[
        'ConfigMaps • ServiceAccount • RBAC',
        'Secret X.509 + aws_signing_helper'],badge=None,size=25)
    card(165,870,285,160,'Vault + PVC',[
        'K8s auth • KV-v2',
        'TLS 8200 • vault-data',
        'KV persistente no PVC'],color=C['purple'])
    card(500,870,280,160,'Secret Redis',[
        'exporter-credentials',
        'Mapa por endpoint',
        'Volume de leitura'],color=C['purple'])
    card(840,870,295,160,'exporter-auth',[
        'Deployment + Service',
        '9121 • senha via Secret',
        'Sidecar: reload + ack'],color=C['green'])
    card(840,1080,295,125,'exporter-noauth',[
        'Deployment + Service',
        '9121 • sem senha'],color=C['green'])
    card(165,1080,600,125,'ScrapeConfigs dinâmicos',[
        'Alvos por nó/endpoint • labels de negócio',
        'Criação, atualização e remoção segura'])
    box(130,1275,1040,310,fill='#EDF7F4',stroke='#8AC5AC')
    text(150,1290,'Namespace monitoring',27,C['green'],True,950)
    card(165,1365,350,150,'Prometheus Operator',[
        'Observa os ScrapeConfigs',
        'Atualiza a configuração'],size=25)
    card(650,1365,485,150,'Prometheus local',[
        'Pull das métricas via exporter',
        'redis_up • memória • clientes'],color=C['green'])
    box(650,1530,485,45,fill='#FFFFFF',stroke=C['green'],radius=10)
    text(670,1536,'Grafana configurado no Helm',22,C['green'],True,445)
    text(165,1540,'Alertmanager desabilitado (Helm)',20,C['muted'],width=440)
    arrow([(1090,1530),(1090,1515)],C['green'],width=3)
    card(130,1660,1070,105,'WireGuard da VM • interface wg0',[
        'Rede dos Pods → host Rocky → túnel privado para a EC2'],color=C['green'],size=27)
    # Center: explicitly separate the two schedules and the eligibility gate.
    steps = [
        ('Regiões habilitadas','Timer: ~5 min após terminar cada ciclo','Conta autorizada • regiões descobertas'),
        ('Inventário completo','Todos os Redis/Valkey, com e sem tags','status.json • não é métrica Prometheus'),
        ('Autorizar novas conexões','Tag: RedisObservability=vault-lab','Terraform: peering, rotas, SG e SSM'),
        ('Executar o Pod discovery','CronJob 1 min → Job → Pod Python','Endpoints, nós, AZs e tags pelas APIs'),
        ('Validar credencial e alvo','Vault → Secret → reload confirmado','DNS + TLS + AUTH/PING + exporter'),
        ('Reconciliar monitoramento','ScrapeConfigs → Operator → Prometheus','Preserva alvos em falhas e inventário parcial'),
        ('Coletar métricas reais','Prometheus → exporter → Redis','redis_up=1 confirma consulta ao Redis'),
        ('Atualizar e remover','Tags de negócio viram labels','Exclusão exige observações confirmadas'),
    ]
    for i,(title,a,b) in enumerate(steps):
        y=300+i*170
        card(1310,y,640,135,f'{i+1}. {title}',[a,b],color=C['purple'],size=27)
        if i<7:arrow([(1630,y+135),(1630,y+165)],C['purple'])
    # AWS identity/control plane.
    text(2050,265,'Identidade e APIs • HTTPS 443',29,C['orange'],True,1040)
    card(2070,330,470,155,'IAM Roles Anywhere',[
        'Certificado X.509',
        'Trust Anchor + Profile',
        'Identidade de máquina'],color=C['orange'],badge='IAM')
    card(2595,330,500,155,'Role de descoberta',[
        'RedisObservabilityDiscoveryRole',
        'Leitura de inventário e tags',
        'Usada pelo Pod Python'],color=C['orange'])
    card(2070,540,470,130,'AWS STS',[
        'Credenciais temporárias',
        'AssumeRole quando autorizado'],color=C['orange'],badge='STS')
    card(2595,540,500,155,'Role da automação de rede',[
        'RedisObservabilityNetworkAutomationRole',
        'Peering, rotas, regras e SSM',
        'Identidade separada da descoberta'],color=C['orange'])
    card(2070,750,1025,130,'APIs AWS • plano de controle',[
        'ElastiCache: caches, grupos, nós, endpoints e tags',
        'EC2: regiões e rede • SSM: configura somente o gateway'],color=C['orange'],badge='API',size=28)
    # AWS data plane: topology is illustrative, no cache shown as currently running.
    box(2050,920,1060,835,fill='#FFF9EF',stroke='#E8BA79')
    text(2070,935,'Rede privada • VPC central us-east-1',28,C['orange'],True,1000)
    card(2080,995,990,145,'EC2 WireGuard • gateway central',[
        'i-0f18f326bfde065db • VPC 10.80.0.0/16',
        'Encaminhamento IPv4 + SNAT • rotas e security groups'],color=C['green'],badge='EC2',size=29)
    card(2080,1200,990,130,'ElastiCache • Redis / Valkey',[
        'Caches da VPC central • conexão direta pela rede privada',
        'TLS 6379 • AUTH quando configurado • nós e AZs'],color=C['green'],badge='DB',size=29)
    card(2080,1400,990,115,'VPC Peering direto • rotas • regras TCP 6379',[
        'Terraform por VPC • retorno /32 para o gateway com SNAT'],color=C['orange'],dash=True,size=27)
    card(2080,1580,990,135,'ElastiCache em outras VPCs / regiões',[
        'Exemplos: Canadá, Mumbai e São Paulo • CIDRs sem conflito',
        'Conectividade e coleta inter-regional: a homologar'],color=C['orange'],dash=True,size=29)
    # Local edges, kept in the gutters between nodes.
    arrow([(360,455),(360,505)])
    arrow([(650,385),(620,385)],C['purple'])
    arrow([(925,455),(925,485),(1220,485),(1220,625),(1120,625),(1120,655)])
    text(920,600,'publica escopo',20,C['blue'],width=245)
    arrow([(650,720),(600,720)],'#7C8AA1',True)
    arrow([(300,790),(300,870)],C['purple'])
    text(175,815,'JWT + TLS • lê KV',21,C['purple'],width=350)
    arrow([(550,790),(550,870)],C['purple'])
    text(475,815,'publica',21,C['purple'],width=240)
    arrow([(780,910),(840,910)],C['purple'])
    arrow([(180,790),(145,790),(145,1050),(250,1050),(250,1080)])
    arrow([(340,1205),(340,1365)])
    arrow([(515,1440),(650,1440)])
    arrow([(805,1365),(805,985),(840,985)],C['green'])
    arrow([(805,1140),(840,1140)],C['green'])
    text(650,1240,'HTTP /scrape :9121',21,C['green'],width=430)
    arrow([(1135,990),(1153,990),(1153,1640),(1030,1640),(1030,1660)],C['green'])
    arrow([(1135,1140),(1153,1140)],C['green'])
    # Identity and API edges.
    arrow([(2305,485),(2305,540)],C['orange'])
    arrow([(2540,600),(2570,600),(2570,410),(2595,410)],C['orange'])
    arrow([(2540,615),(2595,615)],C['orange'])
    arrow([(2845,485),(3115,485),(3115,725),(2845,725),(2845,750)],C['blue'])
    arrow([(2845,695),(2845,750)],C['orange'])
    # Control-plane connection routed above the three columns, not through nodes.
    arrow([(1180,310),(1180,180),(1995,180),(1995,405),(2070,405)],C['orange'],width=3)
    text(1288,148,'X.509 → credenciais temporárias AWS',20,C['orange'],width=680)
    arrow([(600,735),(620,735),(620,825),(1255,825),(1255,790),(2008,790),(2008,815),(2070,815)])
    text(650,833,'Pod → AWS • HTTPS 443',20,C['blue'],width=470)
    # Main metric connection and the central/remote branches.
    arrow([(1200,1715),(1997,1715),(1997,1060),(2080,1060)],C['green'],width=5)
    text(1305,1738,'WireGuard UDP • Redis TLS encapsulado',23,C['green'],width=650)
    arrow([(2575,1140),(2575,1200)],C['green'])
    arrow([(3070,1060),(3095,1060),(3095,1457),(3070,1457)],C['orange'],True)
    arrow([(2575,1515),(2575,1580)],C['orange'],True)
    # Legend and scope prevent reading proposed expansion as validated deployment.
    box(60,1840,1140,270)
    text(85,1860,'Como ler as setas',29,bold=True,width=1050)
    for y,col,dash,label in [(1920,C['blue'],False,'Controle: descoberta e ScrapeConfigs'),
                             (1960,C['purple'],False,'Credenciais: Vault, Secret e reload'),
                             (2000,C['green'],False,'Coleta: solicitações; respostas pelo mesmo caminho'),
                             (2040,C['orange'],True,'Extensão implementada, ainda sem homologação real')]:
        arrow([(90,y+14),(165,y+14)],col,dash)
        text(185,y,label,24,width=990)
    box(1230,1840,750,270,fill='#F3F0FB',stroke='#B5A4D8')
    text(1255,1860,'Escopo e evidências',29,C['purple'],True,700)
    for i,line in enumerate(['Validado: coleta real em us-east-1.',
                              'Inventário: 17 regiões, com e sem tags.',
                              'Inventário não confirma coleta nem redis_up.',
                              'A homologar: rede/coleta inter-regional,',
                              'multiaccount e failover Multi-AZ.']):
        text(1255,1915+i*33,line,23,width=700)
    box(2020,1840,1120,270,fill='#FFF9EF',stroke='#E8BA79')
    text(2045,1860,'Estado salvo e componentes históricos',29,C['orange'],True,1060)
    for i,line in enumerate(['Diagrama do fluxo ON; último checkpoint 23/10: OFF.',
                              'Caches de teste removidos • EC2s paradas • PVC preservado.',
                              'ECS/Fargate e AMP não integram a coleta atual.',
                              'Discos, Elastic IP e histórico AMP foram preservados.',
                              'Sem AWS Client VPN, NAT Gateway ou Transit Gateway.']):
        text(2045,1915+i*33,line,23,width=1060)
    text(60,2130,'Fonte: código Terraform/Python e evidências do repositório • Sem senhas ou chaves no diagrama • Arquitetura funcional, não inventário ao vivo',22,C['muted'],width=3070)


def save():
    OUT.mkdir(exist_ok=True)
    name='arquitetura-projeto-atual'
    cache=ROOT/'.work-vault'
    cache.mkdir(exist_ok=True)
    manifest=cache/(name+'.draw.json')
    manifest.write_text(json.dumps(dict(width=W,height=H,commands=commands),ensure_ascii=False),encoding='utf-8')
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
         '<title>AWS Redis Observability — arquitetura implementada</title>',
         '<desc>Kind externo, Terraform, discovery, Vault, exporters, IAM, APIs e conectividade AWS. Expansão inter-regional ainda não homologada.</desc>']
    for c in commands:
        if c['op']=='box':
            dash=' stroke-dasharray="12 8"' if c['dash'] else ''
            svg.append(f'<rect x="{c["x"]}" y="{c["y"]}" width="{c["w"]}" height="{c["h"]}" rx="{c["radius"]}" fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="2"{dash}/>')
        elif c['op']=='text':
            svg.append(f'<text x="{c["x"]}" y="{c["y"]+c["size"]}" fill="{c["color"]}" font-family="Segoe UI, Arial, sans-serif" font-size="{c["size"]}" font-weight="{700 if c["bold"] else 400}">{html.escape(c["value"])}</text>')
        else:
            pts=c['points'];dash=' stroke-dasharray="12 8"' if c['dash'] else ''
            svg.append(f'<polyline points="'+ ' '.join(f'{x},{y}' for x,y in pts)+f'" fill="none" stroke="{c["color"]}" stroke-width="{c["width"]}" stroke-linejoin="round"{dash}/>')
            x,y=pts[-1];px,py=pts[-2];dx,dy=x-px,y-py;length=(dx*dx+dy*dy)**.5;ux,uy=dx/length,dy/length
            points=[(x,y),(x-ux*14-uy*7,y-uy*14+ux*7),(x-ux*14+uy*7,y-uy*14-ux*7)]
            svg.append('<polygon points="'+' '.join(f'{a},{b}' for a,b in points)+f'" fill="{c["color"]}"/>')
    svg.append('</svg>')
    (OUT/(name+'.svg')).write_text('\n'.join(svg),encoding='utf-8')
    subprocess.run(['powershell','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
                    str(ROOT/'scripts/render_architecture_diagram.ps1'),str(manifest),
                    str(OUT/(name+'.png'))],check=True)


if __name__=='__main__':
    draw()
    save()

