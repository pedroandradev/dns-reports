# AdGuard DNS Reports

Painel web independente para **consultar, filtrar, classificar e exportar consultas DNS do [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome)**. Executa em um container LXC Debian 13 no Proxmox VE e permite que usuários autorizados utilizem o navegador, sem instalar scripts nos computadores.

> **Escopo:** o painel analisa consultas DNS recebidas pelo AdGuard. Uma consulta a um domínio não comprova que uma pessoa navegou em determinada página, nem identifica de forma conclusiva o aplicativo utilizado. Não mostra URLs completas nem conteúdo HTTPS.

## Funcionalidades

- Histórico DNS obtido por API, arquivado localmente em SQLite.
- Identificação de clientes por hostname quando disponível; caso contrário, pelo IP.
- Identificação **aproximada** de serviços e categorias pelo domínio consultado.
- Filtros por período, hostname/IP, domínio/serviço e consultas permitidas ou bloqueadas.
- Exportação em **CSV**, **Excel (.xlsx)** e **PDF**.
- Contas individuais: somente o administrador inicial pode gerenciar usuários; os demais consultam e exportam relatórios.
- Coleta programada a cada aproximadamente **2 minutos** e retenção local padrão de **90 dias**.
- Painel HTTPS por Nginx, backend Flask/Gunicorn e certificado inicial autoassinado.

## Requisitos

- Host **Proxmox VE 8 ou 9**, acesso como `root` para instalar.
- Internet e repositórios Debian acessíveis pelo novo LXC.
- AdGuard Home previamente instalado, com API administrativa acessível a partir do LXC e **registro de consultas DNS habilitado**.
- Rede com DHCP no segmento/bridge usado pelo container (padrão `vmbr0`).
- Storages Proxmox disponíveis: padrão `local-lvm` para o disco e `local` para templates.
- Recursos padrão do container: Debian 13 não privilegiado, **2 vCPU, 1 GB de RAM e 8 GB de disco**. Verifique a capacidade para o volume de consultas.

O instalador não presume endereço IP, servidor DNS ou marca de uma empresa. O LXC recebe IP por DHCP e DNS pelas configurações da rede. O fuso horário é herdado do host Proxmox (com fallback para UTC).

## Instalação

Revise o instalador antes de executar qualquer script remoto como `root`.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)"
```

O assistente solicita:

1. **URL do AdGuard Home**: exemplo `http://192.168.1.10` ou `http://192.168.1.10:3000`, conforme a porta efetivamente configurada.
2. **Usuário e senha do AdGuard**.
3. **Usuário e senha do administrador do painel** (senha com pelo menos 10 caracteres).

As senhas são solicitadas sem eco no terminal. O instalador cria o próximo CTID disponível, instala os componentes, configura o coletor e apresenta a URL `https://IP_DO_LXC/`.

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

O instalador exige pelo menos 10 GiB disponíveis no storage escolhido. A criação falhará se o template, os storages ou a bridge não estiverem disponíveis.

## Acesso e segurança

- O painel usa **HTTPS** e exige autenticação.
- Apenas a conta administradora original pode criar, excluir e redefinir senhas de usuários, na rota `/usuarios`. O backend bloqueia acesso direto por usuários comuns com **403**.
- Os demais usuários podem consultar, filtrar e exportar relatórios.
- Na instalação nova, o Nginx permite redes **RFC 1918** (10/8, 172.16/12, 192.168/16) e loopback. **Restringir a hosts/sub-redes autorizados no firewall e, se necessário, no Nginx é responsabilidade do operador.** Não exponha o painel diretamente à internet.
- As credenciais administrativas do AdGuard são armazenadas **apenas no container**, no arquivo `/opt/dnsreports/config.json`, com permissões de leitura restritas.
- Nunca publique senhas, IPs internos reais, `config.json`, histórico, banco SQLite nem relatórios de usuários no repositório.

> Se informações sensíveis já foram publicadas em commits antigos, removê-las dos arquivos atuais **não apaga o histórico do Git**. Faça rotação de credenciais expostas e considere reescrever o histórico com os procedimentos oficiais do GitHub.

## Consultas e exportação

Após autenticar, selecione um período (1, 7, 30 ou 90 dias, ou intervalo de datas), um cliente, domínio/serviço e estado. Os botões **CSV**, **Excel** e **PDF** exportam os dados filtrados.

- CSV e XLSX: limite de **100.000 linhas por arquivo**.
- PDF: limite de **1.000 linhas**.
- A primeira coleta tenta importar até **7 dias retroativos**, desde que esses dados ainda existam no registro do AdGuard e estejam dentro dos limites de paginação.
- O histórico local não recupera consultas que o AdGuard já apagou. Monitore o espaço de disco para reter 90 dias.
- Para ver **hostnames**, configure registros PTR (DNS reverso) no resolvedor adequado. A ausência de PTR não impede relatórios por IP.

## Atualizar instalação existente

**Faça backup do CT antes de atualizar.** A atualização preserva o diretório de dados, banco e contas existentes, mas deve ser validada no seu ambiente.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
```

Substitua `ID_DO_CONTAINER` pelo ID real, como `105`. Não use o comando de instalação nova para atualizar um container existente.

Instalações anteriores com a antiga identidade visual podem migrar o painel sem recriar o LXC. Configurações locais preservadas incluem URL e credenciais do AdGuard. Na atualização, a retenção será ajustada para 90 dias.

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
