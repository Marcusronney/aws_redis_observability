AWS - us-east-1
│
└── VPC: redis-observability-vpc
    │   CIDR: 10.80.0.0/16
    │
    ├── Subnet privada A
    │   10.80.10.0/24
    │
    ├── Subnet privada B
    │   10.80.20.0/24
    │
    ├── Route Table privada
    │   10.80.0.0/16 → local
    │
    ├── Security Group ElastiCache
    │   Entrada: nenhuma inicialmente
    │
    └── ElastiCache Subnet Group
        └── Futuro Redis / Valkey



Acessar a AWS do terminal
````
aws sso login \
  --profile redis-observability-admin \
  --use-device-code
````

Validar cnoexão
````
aws sts get-caller-identity \
  --profile redis-observability-admin
````

Token auth AWS secure group Cache
`````
DASDASDASDNDJASHDASUH#1
````


