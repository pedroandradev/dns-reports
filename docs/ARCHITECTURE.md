# Arquitetura

## Fluxo

`Instalar-DNS-Reports-Proxmox.sh` baixa o código completo do GitHub e invoca `scripts/proxmox/install.sh`. O shell cria/atualiza o container Debian 13, instala o pacote Python pelo `pip`, configura `systemd` e `nginx`. Não contém HTML ou código de negócio.

O serviço Gunicorn inicia `adguard_dns_reports.wsgi:app`, que usa `create_app()` para registrar blueprints independentes de autenticação e relatórios.

O `dnsreports-collector.timer` invoca o comando `adguard-reports-collect` periodicamente. O coletor consulta a API de consultas DNS do AdGuard, classifica domínios e registra no SQLite.

## Diretórios
- `src/adguard_dns_reports/__init__.py`: fábrica de aplicação Flask.
- `src/adguard_dns_reports/auth.py`: autenticação e gestão de contas (somente admin).
- `src/adguard_dns_reports/reports.py`: filtros, dashboard e exportação.
- `src/adguard_dns_reports/collector.py`: coleta periódica DNS.
- `src/adguard_dns_reports/classification.py`: regras de classificação por domínio.
- `src/adguard_dns_reports/database.py`: esquema SQLite e migrações.
- `src/adguard_dns_reports/config.py`: leitura da configuração privada.
- `src/adguard_dns_reports/security.py`: CSRF, hash de senhas e cabeçalhos.
- `src/adguard_dns_reports/templates/`: interface HTML/Jinja.
- `src/adguard_dns_reports/static/`: CSS.
- `scripts/proxmox/`: provisionamento e unidades de sistema.
- `tests/`: verificações estruturais e de comportamento.

## Compatibilidade

A implantação preserva `/opt/dnsreports/config.json` e `/opt/dnsreports/data/queries.sqlite3` e continua usando os mesmos nomes de serviços systemd. A partir desta versão o código é instalado como pacote Python; não é necessário manter os antigos `app.py` e `core.py` embutidos.

A instalação nova requer DHCP e parâmetros de storage/bridge compatíveis. Para o primeiro uso de uma versão nova, faça backup e teste em um CT de homologação antes de atualizar produção.

## Acesso VPN

O modo `--configure-access CTID` do instalador chama
`scripts/proxmox/configure_access.py` dentro do CT. Esse utilitário Python usa
apenas a biblioteca padrão e não importa a aplicação nem lê credenciais.
Ele mantém a ACL LAN do site, gerencia um snippet adicional de CIDRs VPN,
faz backup e valida o Nginx antes da recarga, restaurando os arquivos em caso
de falha. Atualizações sem `REPORTS_VPN_CIDRS` preservam a política existente.
Detalhes de migração e rede: [VPN.md](VPN.md).

## Tráfego ntopng e atualização

- traffic/client.py: transporte REST v2 com paginação, limites e mensagens sem credenciais.
- traffic/settings.py: configuração administrativa privada e associação configurável de locais.
- traffic/collector.py: leitura de contadores e fluxos; integração opcional em serviço separado.
- traffic/storage.py: tabelas aditivas, bases de comparação e agregações por hora.
- traffic/views.py e exports.py: filtros, relatórios e exportações separados do histórico DNS.
- templates/traffic*.html e ntopng_settings.html: interface de tráfego e configuração.
- scripts/proxmox/lib/ui.sh: apresentação do instalador, tarefas silenciosas e indicadores.
- scripts/proxmox/upgrade.py: snapshot consistente e restauração da aplicação.

O coletor ntopng possui timer próprio e lock, sem depender da disponibilidade do
AdGuard. A configuração nova fica no banco privado, mantendo o config.json
existente. As credenciais não entram em templates/exportações/logs. Usuários
comuns não podem alterar a integração.

Consulte [NTOPNG.md](NTOPNG.md) para semântica dos contadores e limites de cobertura
e [INSTALLER.md](INSTALLER.md) para implantação e recuperação.

## Acessos como visão principal

traffic/access.py identifica o serviço/destino e acompanha presença/ausência
somente em snapshots completos. Campos novos de traffic_connections são
adicionados sem transformar dados legados em conexões ativas.
traffic/access_views.py oferece a página inicial e /acessos, agrupando fluxos
retidos por equipamento/serviço e calculando a união dos intervalos.
traffic/access_exports.py mantém a semântica de estado/estimativa nos arquivos.

A consulta é somente de leitura. A integração continua restrita ao papel de
administrador no gate de autenticação e no endpoint de configuração.
Detalhes: [ACCESSES.md](ACCESSES.md).

traffic/names.py valida hostnames e extrai somente o host de info da API
detalhada, descartando caminhos e parâmetros. dns_evidence.py arquiva respostas
A/AAAA recentes em dns_resolutions e correlaciona cliente/IP/TTL; não consulta
nomes por dono do IP. A tabela é aditiva e limitada a 24h/100.000 respostas.
traffic_connections recebe name_source e dns_candidates para manter evidência
e ambiguidades por fluxo. Consulte [IDENTIFICATION.md](IDENTIFICATION.md).
