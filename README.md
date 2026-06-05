# EdgeOrch

EdgeOrch é uma plataforma de orquestração de containers LXC em nós Proxmox usando oneM2M como barramento de comunicação. O projeto inclui um cliente web/desktop para pedir e gerir máquinas, Application Entities (AEs) que executam as operações nos nós Proxmox, e scripts de suporte para preparar a estrutura de provisioning no CSE.

## Funcionalidades

- Criação de containers LXC através de pedidos publicados no CSE oneM2M.
- Seleção automática do nó Proxmox com base em inventário, CPU, memória, disco e reservas recentes.
- Gestão de máquinas já criadas: reiniciar, desligar e apagar.
- Migração/rebalanceamento entre nós Proxmox quando há desequilíbrio de carga.
- Canal privado de resultados por cliente, com ACP oneM2M dedicado.
- Inventário periódico publicado por cada AE de nó.
- Terminal SSH integrado no cliente web para aceder aos containers.
- Cliente desktop empacotável com PyInstaller e pywebview.

## Arquitetura

```text
Cliente EdgeOrch
  |
  | publica pedidos create/reboot/shutdown/delete/migrate
  v
ACME CSE oneM2M
  |
  | containers: requests, claims, results, inventory, rebalance_decisions
  v
AEs Proxmox por nó
  |
  | executam operações via API Proxmox
  v
Containers LXC
```

O cliente publica pedidos em `/{CSE_BASE}/{PROVISIONING_AE}/requests`. As AEs de nó competem por claims em `claims`, executam o pedido no Proxmox e publicam o resultado no container privado do cliente indicado em `reply_to`.

## Estrutura

```text
AEs/
  AE1/main.py                 AE de provisioning para um nó Proxmox
  AE2/main.py                 AE de provisioning para outro nó Proxmox
Client/
  web_client.py               App FastAPI local
  desktop_app.py              Launcher desktop com pywebview
  client.py                   Cliente CLI interativo
  provisioning_service.py     Workflows de criação, ações e inventário
  rebalance_manager.py        Lógica de rebalanceamento/migração
  lease_manager.py            Gestão de leases das máquinas
  onem2m_client.py            Cliente HTTP oneM2M
  templates/                  UI HTML
  static/                     JavaScript/CSS e xterm.js
  desktop_app.spec            Spec PyInstaller
CSE/
  edgeorch-cse/acme.ini.example
  ae-provisioning/setup_provisioning.py
  ae-provisioning/wait_for_cse.sh
```

## Requisitos

- Python 3.11 ou superior.
- Um CSE oneM2M ACME acessível por HTTP.
- Um cluster/nós Proxmox com API Token válido.
- Templates LXC disponíveis no storage configurado.
- Conectividade SSH para os containers criados, se quiser usar o terminal integrado.

As dependências do cliente estão em `Client/requirements.txt`.

## Configuração

O projeto usa variáveis de ambiente carregadas por `.env`. No cliente, a ordem de procura é:

1. caminho em `EDGEORCH_ENV_FILE`, se definido;
2. diretório runtime da app;
3. diretório do bundle;
4. diretório atual;
5. pasta `Client/`.

### Cliente

Crie um ficheiro `.env` em `Client/` ou indique-o com `EDGEORCH_ENV_FILE`.

```env
CSE_URL=http://127.0.0.1:8080
CSE_BASE=cse-in
PROVISIONING_AE=AE_Provisioning
ONEM2M_RELEASE=4

CLIENT_ORIGIN=AUTO
CLIENT_AE_NAME=AUTO

WEB_HOST=127.0.0.1
WEB_PORT=8000

CT_LOGIN_USER_HINT=root
CT_LOGIN_PASSWORD_HINT=ubuntu

WORKER_PROXMOX_NODES=sdei-mm01,sdei-mm02
WORKER_NODE_LABELS=sdei-mm01=AE1,sdei-mm02=AE2
WORKER_NODE_AE_IPS=sdei-mm01=192.168.0.141,sdei-mm02=192.168.0.142
WORKER_NODE_AE_NAMES=sdei-mm01=AE_Proxmox_Monitor_MM01,sdei-mm02=AE_Proxmox_Monitor
WORKER_NODE_MAX_CPU=sdei-mm01=4,sdei-mm02=4
WORKER_NODE_MAX_MEMORY_MB=sdei-mm01=16384,sdei-mm02=16384

MACHINE_LEASE_SECONDS=300
MACHINE_RENEWAL_PROMPT_SECONDS=15
REBALANCE_MIN_COUNT_GAP=2
REBALANCE_MIN_TOTAL_MACHINES=3
REBALANCE_PROPOSAL_TIMEOUT_SECONDS=15
```

`CLIENT_ORIGIN=AUTO` e `CLIENT_AE_NAME=AUTO` geram uma identidade baseada no utilizador ou nome do computador, permitindo usar o mesmo build em máquinas diferentes.

### AE de provisioning

Cada AE deve ter a sua própria configuração, normalmente junto do respetivo `main.py`.

```env
CSE_URL=http://127.0.0.1:8080
CSE_BASE=cse-in
PROVISIONING_AE=AE_Provisioning
AE_NAME=AE_Proxmox_Monitor
AE_ORIGIN=Cae-proxmox-monitor
ONEM2M_RELEASE=4

PROXMOX_HOST=https://192.168.0.141:8006
PROXMOX_NODE=sdei-mm01
PROXMOX_TOKEN_ID=user@pam!token-name
PROXMOX_TOKEN_SECRET=token-secret
PROXMOX_VERIFY_SSL=false
PROXMOX_TEMPLATE_STORAGE=local
PROXMOX_ROOTFS_STORAGE=local-lvm

CT_DEFAULT_PASSWORD=ubuntu
CT_SWAP_MB=512
CT_UNPRIVILEGED=true
CT_ONBOOT=true
CT_START_AFTER_CREATE=true
CT_FEATURES=nesting=1

POLL_INTERVAL_SECONDS=5
INVENTORY_PUBLISH_INTERVAL_SECONDS=15
STATE_FILE=/root/ae-python/state.json
```

Para múltiplos nós, altere pelo menos `AE_NAME`, `AE_ORIGIN`, `PROXMOX_HOST` e `PROXMOX_NODE` em cada instância.

## Preparar o CSE

O ficheiro `CSE/edgeorch-cse/acme.ini.example` contém uma configuração base para um CSE ACME IN em `0.0.0.0:8080`.

Depois de iniciar o CSE, prepare a AE de provisioning e os containers `requests`, `claims` e `results`:

```powershell
cd CSE\ae-provisioning
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install requests python-dotenv
python setup_provisioning.py
```

No Linux, o script `wait_for_cse.sh` pode ser usado para aguardar que o CSE esteja disponível em `127.0.0.1:8080`.

## Executar o cliente

Instale as dependências:

```powershell
cd Client
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Cliente web:

```powershell
python web_client.py
```

Depois abra `http://127.0.0.1:8000`, ou o host/porta configurado em `WEB_HOST` e `WEB_PORT`.

Cliente desktop:

```powershell
python desktop_app.py
```

Smoke test do desktop launcher:

```powershell
python desktop_app.py --smoke-test
```

Cliente CLI:

```powershell
python client.py
```

## Executar uma AE Proxmox

Em cada nó Proxmox, copie a pasta da AE, configure o `.env` e execute:

```bash
cd AEs/AE1
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv urllib3
python main.py
```

A AE cria/garante a sua AE oneM2M, publica inventário periodicamente e processa pedidos pendentes.

## Empacotar o cliente desktop

O projeto inclui `Client/desktop_app.spec` para PyInstaller.

```powershell
cd Client
pip install -r requirements.txt pyinstaller
pyinstaller desktop_app.spec
```

O executável fica em:

```text
Client/dist/EdgeOrch/EdgeOrch.exe
```

Se quiser distribuir configuração junto do executável, coloque um `.env` ao lado de `EdgeOrch.exe`. A app desktop prefere esse ficheiro em runtime.

## Fluxo de operação

1. O cliente garante a sua AE privada e o container privado de resultados.
2. O utilizador cria um pedido LXC pela UI, CLI ou API local.
3. O pedido é publicado em `requests` com um `request_id` único e `reply_to`.
4. As AEs de nó consultam pedidos, publicam claims e escolhem o vencedor.
5. A AE vencedora executa a operação no Proxmox.
6. O resultado é publicado no container privado do cliente.
7. O cliente atualiza o inventário e permite ações posteriores sobre a máquina.

## Endpoints do cliente web

- `GET /` - interface principal.
- `GET /api/machines` - inventário de máquinas geridas.
- `POST /api/machines` - criar container LXC.
- `POST /api/machines/{vmid}/actions/{action_name}` - ações `start`, `reboot`, `shutdown` ou `delete`.
- `GET /api/leases/status` - estado dos leases.
- `POST /api/leases/prompts/{prompt_id}/renew` - renovar lease.
- `POST /api/leases/prompts/{prompt_id}/decline` - recusar renovação.
- `GET /api/rebalance/status` - estado do rebalanceamento.
- `POST /api/rebalance/proposals/{proposal_id}/accept` - aceitar migração proposta.
- `POST /api/rebalance/proposals/{proposal_id}/decline` - recusar migração proposta.
- `WS /ws/terminal` - terminal SSH via WebSocket.

## Notas de segurança

- Não coloque tokens Proxmox ou passwords reais no repositório.
- Use API Tokens Proxmox com permissões mínimas necessárias.
- `PROXMOX_VERIFY_SSL=false` é útil em laboratório, mas em produção deve usar certificados válidos.
- O terminal SSH usa a password root indicada pelo utilizador ou pela hint configurada.
- Os resultados do cliente escondem `root_password` antes de ecoar dados para a interface.

## Problemas comuns

- `Missing environment variable`: confirme que o `.env` está no local certo ou defina `EDGEORCH_ENV_FILE`.
- CSE inacessível: confirme `CSE_URL`, porta 8080 e headers oneM2M.
- Sem resultados para pedidos: verifique se pelo menos uma AE está a correr e se consegue ler `requests`.
- Nó não escolhido para criação: confirme inventário, storage, limites `WORKER_NODE_MAX_CPU` e `WORKER_NODE_MAX_MEMORY_MB`.
- Terminal SSH indisponível: confirme que o CT tem IP, está `running` e aceita SSH na porta 22.

