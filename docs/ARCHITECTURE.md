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
