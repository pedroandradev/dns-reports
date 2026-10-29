# AdGuard DNS Reports

Painel web independente para **consultar, filtrar, classificar e exportar consultas DNS do [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome)**. Executa em um container LXC Debian 13 no Proxmox VE e permite que usuários autorizados utilizem o navegador, sem instalar scripts nos computadores.

> **Escopo:** o painel analisa consultas DNS recebidas pelo AdGuard. Uma consulta a um domínio não comprova que uma pessoa navegou em determinada página, nem identifica de forma conclusiva o aplicativo utilizado. Não mostra URLs completas nem conteúdo HTTPS.

## Quem está acessando o quê

A página inicial prioriza **computador → serviço/site → tráfego → duração
observada → estado da conexão**, com destinos detalhados e filtros por
equipamento, aplicação e período. Conexões paralelas do mesmo serviço são
agrupadas sem multiplicar sua duração. O consumo de banda permanece complementar.

Somente o administrador configura ou testa o ntopng. As outras contas apenas
consultam e exportam. A integração existente é preservada ao atualizar o CT.

O estado ativo é o observado na última coleta. O fim é estimado por ausência
em coletas completas; falhas de coleta não confirmam encerramento. Veja
[Acessos observados e seus horários](docs/ACCESSES.md).

## Funcionalidades

- Histórico DNS obtido por API, arquivado localmente em SQLite.
- Identificação de clientes por hostname quando disponível; caso contrário, pelo IP.
- Identificação **aproximada** de serviços e categorias pelo domínio consultado.
- Filtros por período, hostname/IP, domínio/serviço e consultas permitidas ou bloqueadas.
- Exportação em **CSV**, **Excel (.xlsx)** e **PDF**.
- Contas individuais: somente o administrador inicial pode gerenciar usuários; os demais consultam e exportam relatórios.
- Coleta programada a cada aproximadamente **2 minutos** e retenção local padrão de **90 dias**.
- Painel HTTPS por Nginx, backend Flask/Gunicorn e certificado inicial autoassinado.

## Consumo de rede com ntopng

A integração opcional com **ntopng Community 6.6** acrescenta rankings por
computador, aplicação e local, conexões observadas e exportação de medições.
O AdGuard e o histórico DNS existente continuam disponíveis.

Atualize o mesmo CT com o comando da seção de atualização e, usando o
administrador existente, abra **Integração ntopng** no painel. Não é necessário
reinstalar ou informar novamente a configuração do AdGuard.

Consulte [Integração ntopng](docs/NTOPNG.md) para configuração, cobertura e
retenção. O tráfego total LAN e a internet observada são apresentados em visões
distintas: consultar fluxos ativos periodicamente não garante registrar todas
as conexões nem medir todo o link.

## Requisitos

- Host **Proxmox VE 8 ou 9**, acesso como `root` para instalar.
- Internet e repositórios Debian acessíveis pelo novo LXC.
- AdGuard Home previamente instalado, com API administrativa acessível a partir do LXC e **registro de consultas DNS habilitado**.
- Rede com DHCP no segmento/bridge usado pelo container (padrão `vmbr0`).
- Storages Proxmox disponíveis: padrão `local-lvm` para o disco e `local` para templates.
- Recursos padrão do container: Debian 13 não privilegiado, **2 vCPU, 1 GB de RAM e 8 GB de disco**. Verifique a capacidade para o volume de consultas.

O instalador não presume endereço IP, servidor DNS ou marca de uma empresa. O LXC recebe IP por DHCP e DNS pelas configurações da rede. O fuso horário é herdado do host Proxmox (com fallback para UTC).

## Organização do código

O instalador público continua disponível em um **único comando**, mas não contém mais código HTML, CSS ou Python embutido. O projeto utiliza pacote Python com estrutura `src/`, Flask application factory e Blueprints.

```text
.
├── Instalar-DNS-Reports-Proxmox.sh   # bootstrap único
├── pyproject.toml                     # empacotamento Python
├── src/adguard_dns_reports/
│   ├── __init__.py                    # create_app()
│   ├── wsgi.py                        # entrada Gunicorn
│   ├── auth.py                        # login e usuários
│   ├── reports.py                     # dashboard, filtros e exportação
│   ├── security.py                    # CSRF, cabeçalhos e senhas
│   ├── collector.py                   # coleta periódica do AdGuard
│   ├── classification.py              # serviços e categorias DNS
│   ├── database.py                    # SQLite e migração
│   ├── config.py                      # leitura de configuração
│   ├── templates/                     # páginas Jinja/HTML
│   └── static/                        # CSS
├── scripts/proxmox/
│   ├── install.sh                     # provisionamento LXC
│   ├── nginx/                         # configuração proxy/HTTPS
│   └── systemd/                       # serviços e timer
├── tests/                             # testes automatizados
└── docs/ARCHITECTURE.md               # documentação técnica
```

Veja [Arquitetura](docs/ARCHITECTURE.md) para o fluxo de instalação, serviços e compatibilidade de migração.

O bootstrap baixa uma cópia do repositório e executa o shell de implantação. A aplicação é instalada por `pip` como pacote Python; os arquivos de interface ficam em `templates/` e `static/`.

## Instalação

Revise o instalador antes de executar qualquer script remoto como `root`.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)"
```

O assistente solicita:

1. **URL do AdGuard Home**: exemplo `http://192.168.1.10` ou `http://192.168.1.10:3000`, conforme a porta efetivamente configurada.
2. **Usuário e senha do AdGuard**.
3. **Usuário e senha do administrador do painel** (senha com pelo menos 10 caracteres).

O instalador mostra o título ASCII **DNS-REPORTS**, progresso em oito etapas e um indicador animado durante cada tarefa. A saída detalhada dos componentes fica em um log privado, cujo caminho é informado. Veja [Instalador e recuperação](docs/INSTALLER.md).

As senhas são solicitadas sem eco no terminal. O bootstrap baixa o projeto completo e o instalador cria o próximo CTID disponível, instala os componentes, configura o coletor e apresenta a URL `https://IP_DO_LXC/`.

**Aviso:** o certificado inicial é autoassinado. O navegador pode exibir aviso de segurança; configure um certificado confiável antes de uso mais amplo. Reserve o IP via DHCP para que o endereço permaneça estável.

### Personalizar infraestrutura (opcional)

Os valores abaixo são **exemplos**, não parâmetros obrigatórios:

```bash
REPORTS_BRIDGE=vmbr1 \
REPORTS_STORAGE=ssd-lvm \
REPORTS_TEMPLATE_STORAGE=local \
REPORTS_TIMEZONE=Etc/UTC \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)"
```

| Variável | Padrão |
| --- | --- |
| `REPORTS_BRIDGE` | `vmbr0` |
| `REPORTS_STORAGE` | `local-lvm` |
| `REPORTS_TEMPLATE_STORAGE` | `local` |
| `REPORTS_TIMEZONE` | Fuso horário do Proxmox; fallback `Etc/UTC` |
| `REPORTS_VPN_CIDRS` | Sem permissões VPN adicionais; lista de CIDRs IPv4 opcional |

O LXC padrão utiliza disco de 8 GB. Reserve espaço conforme o volume de dados. A atualização exige espaço livre adicional para o backup da aplicação e do banco. A criação falhará se o template, os storages ou a bridge não estiverem disponíveis.

## Acesso e segurança

- O painel usa **HTTPS** e exige autenticação.
- Apenas a conta administradora original pode criar, excluir e redefinir senhas de usuários, na rota `/usuarios`. O backend bloqueia acesso direto por usuários comuns com **403**.
- Os demais usuários podem consultar, filtrar e exportar relatórios.
- Na instalação nova, o Nginx permite redes **RFC 1918** (10/8, 172.16/12, 192.168/16) e loopback. **Restringir a hosts/sub-redes autorizados no firewall e, se necessário, no Nginx é responsabilidade do operador.** Não exponha o painel diretamente à internet.
- As credenciais administrativas do AdGuard são armazenadas **apenas no container**, no arquivo `/opt/dnsreports/config.json`, com permissões de leitura restritas.
- Nunca publique senhas, IPs internos reais, `config.json`, histórico, banco SQLite nem relatórios de usuários no repositório.

> Se informações sensíveis já foram publicadas em commits antigos, removê-las dos arquivos atuais **não apaga o histórico do Git**. Faça rotação de credenciais expostas e considere reescrever o histórico com os procedimentos oficiais do GitHub.

## Acesso pela VPN

Configure explicitamente as sub-redes VPN que precisam acessar o IP interno do
container. O comando abaixo altera apenas a ACL do Nginx de um CT existente,
mantendo o acesso LAN, certificados, credenciais e dados:

```bash
REPORTS_VPN_CIDRS='100.96.20.0/24' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --configure-access ID_DO_CONTAINER
```

O CIDR é um exemplo: substitua-o pela origem VPN efetivamente vista pelo Nginx.
A variável também funciona na instalação e em `--update`. Em atualizações
comuns, deixá-la ausente preserva a ACL; defini-la substitui a lista VPN
gerenciada, e defini-la como vazia remove apenas essa lista adicional.
As regras LAN anteriores permanecem.

O configurador valida CIDRs IPv4, cria backups, testa o Nginx e restaura os
arquivos se o teste ou a recarga falhar. Configurações Nginx customizadas que
exijam interpretação da política são recusadas para revisão manual.

Uma VPN RFC 1918 já é aceita pela ACL padrão. Se houver timeout, verifique
rotas e firewall. O instalador não altera pfSense nem firewall Proxmox.
Consulte [VPN: configuração, pfSense, diagnóstico e reversão](docs/VPN.md),
incluindo como testar a branch da correção antes do merge.

## Consultas e exportação

Após autenticar, selecione um período (1, 7, 30 ou 90 dias, ou intervalo de datas), um cliente, domínio/serviço e estado. Os botões **CSV**, **Excel** e **PDF** exportam os dados filtrados.

- CSV e XLSX: limite de **100.000 linhas por arquivo**.
- PDF: limite de **1.000 linhas**.
- A primeira coleta tenta importar até **7 dias retroativos**, desde que esses dados ainda existam no registro do AdGuard e estejam dentro dos limites de paginação.
- O histórico local não recupera consultas que o AdGuard já apagou. Monitore o espaço de disco para reter 90 dias.
- Para ver **hostnames**, configure registros PTR (DNS reverso) no resolvedor adequado. A ausência de PTR não impede relatórios por IP.

## Atualizar instalação existente

**Faça backup do CT antes de atualizar.** A atualização mantém o diretório de dados, banco e contas existentes e troca o serviço para usar o pacote Python. Faça backup e homologue antes de atualizar produção.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
```

O instalador cria um backup consistente e versionado antes da implantação, aplica migrações aditivas e verifica a resposta HTTP do painel. Em falha, tenta recuperar a aplicação anterior. Consulte [Atualização e recuperação](docs/INSTALLER.md).

Substitua `ID_DO_CONTAINER` pelo ID real, como `105`. Não use o comando de instalação nova para atualizar um container existente.

Instalações anteriores com a antiga identidade visual podem migrar o painel sem recriar o LXC. Configurações locais preservadas incluem URL e credenciais do AdGuard. A retenção já configurada é preservada. A integração ntopng fica desabilitada até ser configurada pelo administrador.

## Diagnóstico e manutenção

Substitua `ID_DO_CONTAINER` pelo ID da instalação nos exemplos:

```bash
pct exec ID_DO_CONTAINER -- systemctl status dnsreports nginx --no-pager
pct exec ID_DO_CONTAINER -- journalctl -u dnsreports -n 50 --no-pager
pct exec ID_DO_CONTAINER -- journalctl -u dnsreports-collector -n 50 --no-pager
pct exec ID_DO_CONTAINER -- systemctl list-timers --all | grep dnsreports
pct exec ID_DO_CONTAINER -- systemctl start dnsreports-collector.service
```

Estrutura interna:

- Código e ambiente Python: `/opt/dnsreports/`
- Credenciais/configuração privada: `/opt/dnsreports/config.json`
- Histórico e SQLite: `/opt/dnsreports/data/`
- Serviços: `dnsreports.service`, `dnsreports-collector.service` e `dnsreports-collector.timer`

## Privacidade e responsabilidade

Registros DNS podem revelar hábitos, dispositivos e informações pessoais. Restrinja os acessos, mantenha política de retenção transparente e observe leis e políticas de privacidade aplicáveis (por exemplo, LGPD ou GDPR, conforme a localidade).

O software é uma ferramenta de relatórios DNS, **não** um sistema de captura de tráfego, histórico de navegação completo ou identificação comprovada de aplicativos.

## Projeto

O projeto é independente e não é um produto oficial do AdGuard Home ou do Proxmox.

Para sugerir melhorias, abra uma issue ou pull request neste repositório.
