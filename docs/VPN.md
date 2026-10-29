# Acesso ao painel por VPN

O painel pode ser acessado por uma VPN que tenha rota ate o IP LAN do LXC.
Nao e necessario instalar o servidor VPN dentro do container nem publicar o painel na WAN.

## O que foi identificado no repositorio

- O Nginx escuta em IPv4 nas portas 80/443; Gunicorn permanece em `127.0.0.1:8088`.
- A ACL original permite loopback e RFC 1918 (`10/8`, `172.16/12`, `192.168/16`),
  seguidos por `deny all`. Uma VPN fora dessas faixas pode receber **403**.
- Se o IP de origem visto pelo Nginx ja pertence a RFC 1918, essa ACL padrao
  ja o permite. Timeout ou conexao recusada exige verificar rede/servicos.
- Atualizacoes comuns preservam o Nginx existente. Alterar apenas o modelo no Git
  nao altera uma instalacao anterior, que pode ter uma ACL mais restrita.
- A refatoracao da PR #1 foi integrada em `main`. Branches antigas de refatoracao
  nao devem ser usadas para obter esta correcao.

Essas conclusoes vem do codigo. Sem acesso ao pfSense, Proxmox e logs reais nao e
possivel afirmar qual bloqueio ocorre em uma instalacao especifica.

## Configurar um container existente sem atualizar a aplicacao

No host Proxmox, como root, substitua o ID e a sub-rede pelos valores da sua VPN.
O exemplo e ilustrativo; nao habilita automaticamente todo o bloco CGNAT.

```bash
REPORTS_VPN_CIDRS='100.96.20.0/24' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --configure-access 105
```

Este modo exige que o CT esteja ligado. Ele nao executa pip, nao altera a aplicacao,
credenciais, banco, usuarios, certificados, rotas ou firewall. Apenas ajusta o
site Nginx do DNS Reports e seu snippet, executa `nginx -t` e recarrega o Nginx.

Para testar a PR antes do merge, use a mesma branch no bootstrap e em `REPORTS_REF`:

```bash
REPORTS_REF=fix/vpn-web-access \
REPORTS_VPN_CIDRS='100.96.20.0/24' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/fix/vpn-web-access/Instalar-DNS-Reports-Proxmox.sh)" reports --configure-access 105
```

Use uma branch que contenha esta correcao. O bootstrap baixa a branch indicada
por `REPORTS_REF`, cujo padrao e `main`; baixar somente o bootstrap de outra branch
nao seleciona automaticamente o restante do codigo daquela branch.

### Valores, persistencia e compatibilidade

`REPORTS_VPN_CIDRS` aceita ate 64 CIDRs IPv4 separados por espacos ou virgulas.
Hosts devem usar `/32`; bits de host sao normalizados e entradas repetidas removidas.
IPv6, nomes de host, `all`, `/0` e texto de diretivas sao rejeitados.
O provisionamento atual usa IPv4; clientes VPN IPv6 precisam de uma rota IPv4
ate o painel. Nao basta adicionar uma ACL IPv6 a um listener IPv4.

```bash
REPORTS_VPN_CIDRS='100.96.20.0/24,10.250.20.0/24'
```

- **Variavel ausente em uma atualizacao comum:** preserva o Nginx e as permissoes VPN.
- **Variavel definida:** substitui a lista VPN gerenciada, mantendo as regras LAN
  que ja estavam no site. Informe todas as sub-redes VPN desejadas.
- **Variavel explicitamente vazia:** remove apenas as permissoes VPN gerenciadas.
- **Instalacao nova:** mantem as permissoes RFC 1918/loopback anteriores e cria
  um snippet vazio ou com os CIDRs informados.
- **Atualizacao com `--update CTID`:** tambem aceita a variavel. Para mudar apenas
  acesso, prefira `--configure-access`, que nao atualiza o pacote Python.

Exemplo de remocao:

```bash
REPORTS_VPN_CIDRS='' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --configure-access 105
```

Remover uma regra VPN adicional nao bloqueia uma origem que ja e permitida por
uma regra LAN, como `10.0.0.0/8`. Para segmentacao mais restrita, ajuste a politica
LAN e o firewall conforme as redes autorizadas.

As permissoes adicionais ficam em `/etc/nginx/snippets/dnsreports-vpn.conf`.
O configurador adiciona um `include` antes de `deny all` em cada bloco `server`
do site `/etc/nginx/sites-available/dnsreports`, mantendo os allows anteriores,
TLS e proxy. O snippet nao contem `deny all`, para nao interromper a ACL existente.

A migracao automatica aceita ACLs de servidor com allows explicitos seguidos por
um unico `deny all`. Ela recusa ACLs em `location`, includes de terceiros,
`satisfy any` e ordens de regras ambiguas, sem alterar esses arquivos.
Em um site customizado, revise a politica e insira manualmente o include no
escopo HTTPS adequado antes do bloqueio final, preservando as restricoes de cada
location. Depois execute `nginx -t` e recarregue o servico. Um include em
`server` nao substitui ACLs proprias de `location`.

O snippet e gerenciado pelo configurador; nao coloque ajustes manuais nele.
O modo de acesso serializa as alteracoes com `flock`.
Antes de gravar, cria copias com sufixo `.vpn-backup-DATA_UTC` junto aos arquivos.
Gravacoes usam substituicao atomica por arquivo e preservam dono/permissoes.
Se a validacao ou recarga falhar, restaura os arquivos anteriores e tenta
recarregar a politica anterior. Confira o estado do servico se a propria
restauracao da recarga falhar. Um encerramento abrupto do host exige conferir
os backups; nao ha transacao atomica entre dois arquivos.

Para reversao manual, escolha os backups da mesma data; restaure o site e o
snippet em conjunto. Se o snippet nao existia antes, restaure o site antigo e
remova o snippet novo. Valide com `nginx -t` antes de recarregar.

## pfSense e caminho de rede

O repositorio nao contem configuracao do seu pfSense ou firewall Proxmox, e o
instalador nao os altera. Configure regras com o menor destino necessario:

| Ponto | Verificacao |
| --- | --- |
| Cliente VPN | Rota para o IP/sub-rede LAN do LXC pelo tunel; sem sobreposicao com a LAN remota do cliente |
| OpenVPN | Anunciar a LAN do LXC em IPv4 Local Network(s); permitir o trafego na aba OpenVPN ou na interface atribuida correta |
| WireGuard | Incluir o destino LAN do LXC nos AllowedIPs do cliente e permitir trafego na interface/grupo correto |
| Regra do pfSense | Origem: sub-rede VPN autorizada; destino: IP do LXC; protocolo TCP; porta de destino 443 |
| Retorno | Gateway do CT deve conhecer a rota para a sub-rede VPN; quando o gateway nao e o pfSense da VPN, adicionar a rota no roteador adequado |
| Proxmox/CT | Se ha firewall habilitado em datacenter/node/CT, permitir a mesma origem VPN e destino TCP/443 no caminho aplicavel |
| DNS | O nome do painel deve resolver para o IP interno acessivel pela VPN |

Nao abra a porta 8088, o painel Proxmox (8006), a administracao do AdGuard ou
TCP/443 do painel na WAN para resolver este acesso. A autenticacao e o HTTPS
continuam obrigatorios.

Prefira roteamento preservando a origem VPN. Se existir SNAT, o Nginx vera o
endereco traduzido, nao o cliente: confira `access.log` antes de escolher os
CIDRs. Permitir um gateway compartilhado nao distingue clientes VPN de outros
clientes traduzidos; nesse caso a restricao deve ocorrer tambem no firewall
antes do NAT. Nao adicione SNAT nem confie em `X-Forwarded-For` como atalho.

As ACLs do Nginx usam a origem da conexao. O modelo de producao nao configura
`real_ip_header` nem confia em cabecalhos de IP enviados pelo cliente.
O redirecionamento HTTP/80 com `return 301` ocorre antes da fase de ACL:
um 301 **nao prova** que HTTPS esta permitido. A protecao do painel e validada
na porta 443; a porta 80 e opcional se os clientes usam diretamente HTTPS.

## Diagnostico por sintoma

No cliente conectado a VPN, teste primeiro pelo IP:

```bash
curl -k -I --connect-timeout 5 https://IP_DO_LXC/login
```

`-k` serve apenas para diagnosticar o certificado autoassinado; no uso normal,
instale um certificado confiavel compativel com o nome/IP acessado.

| Resultado | Interpretacao e proximo passo |
| --- | --- |
| Timeout / sem resposta | Verificar rota no cliente, regras/logs do pfSense, retorno e firewall Proxmox/CT; a ACL HTTP nao costuma causar timeout TCP |
| Conexao recusada | Conferir CT ligado, Nginx ativo e listener 443 |
| 403 com `access forbidden by rule` no error.log | Conferir IP de origem real, ACL ativa e CIDR VPN; 403 sem essa mensagem tambem pode vir da aplicacao |
| 502 | Nginx foi alcancado; conferir Gunicorn/dnsreports em loopback |
| Aviso TLS | Conferir confianca, validade e SAN do certificado; e uma camada distinta da rota/ACL |
| 200 em /login ou redirecionamento para login | Caminho HTTPS acessivel; validar autenticacao e permissoes |

No host Proxmox:

```bash
pct exec ID_DO_CT -- nginx -t
pct exec ID_DO_CT -- nginx -T
pct exec ID_DO_CT -- systemctl status nginx dnsreports --no-pager
pct exec ID_DO_CT -- ss -lntp
pct exec ID_DO_CT -- ip -4 route
pct exec ID_DO_CT -- tail -n 50 /var/log/nginx/access.log
pct exec ID_DO_CT -- tail -n 50 /var/log/nginx/error.log
```

Confirme em `nginx -T` que o site habilitado inclui o snippet e que nao existe
outro server/listener atendendo o endereco. Nao publique logs com IPs reais,
configuracoes privadas ou dados de usuarios. No pfSense, use os logs de firewall
e captura de pacotes na interface VPN e na LAN para localizar onde o fluxo para.

## Validacao e testes

Depois da aplicacao, valide de uma maquina LAN e de uma maquina VPN:

1. Abrir `https://IP_DO_LXC/login`, autenticar e abrir um relatorio.
2. Confirmar que uma origem fora da ACL recebe 403 na porta 443 (se essa origem
   chega ao CT; se o firewall a bloqueia antes, o resultado esperado e bloqueio de rede).
3. Repetir a configuracao e confirmar o mesmo resultado.
4. Atualizar sem a variavel e verificar que a permissao VPN continua ativa.

Em desenvolvimento:

```bash
python -m pip install -e . pytest tzdata
# Debian/Ubuntu, para o teste real de Nginx:
sudo apt-get install nginx openssl
pytest -q
for script in Instalar-DNS-Reports-Proxmox.sh scripts/proxmox/install.sh; do bash -n "$script"; done
```

Os testes cobrem CIDRs validos/invalidos, injecao, migracao e idempotencia,
preservacao da LAN, substituicao/remocao da lista, backup/reversao, modo de acesso
com Proxmox simulado e HTTPS/proxy/ACL com Nginx real. O teste Nginx usa somente
loopback e um cabecalho de origem confiado **apenas na configuracao de teste**;
isso nao e instalado em producao. Sem nginx/openssl, esse teste e marcado como
skip; instale essas dependencias localmente para executa-lo. No Windows, caminhos de teste podem ser
informados por `REPORTS_TEST_NGINX`, `REPORTS_TEST_OPENSSL` e `REPORTS_TEST_BASH`.

Os testes nao substituem a homologacao no Proxmox/pfSense real.

## Referencias

- [Nginx: ordem e heranca de allow/deny](https://nginx.org/en/docs/http/ngx_http_access_module.html)
- [pfSense: regras OpenVPN](https://docs.netgate.com/pfsense/en/latest/vpn/openvpn/firewall-rules.html)
- [pfSense: diagnostico OpenVPN e rotas de retorno](https://docs.netgate.com/pfsense/en/latest/troubleshooting/openvpn.html)
- [pfSense: WireGuard, regras e NAT](https://docs.netgate.com/pfsense/en/latest/vpn/wireguard/rules.html)
