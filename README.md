# VALLO | Consulta Acesso de Redes

Painel web interno para consultar e exportar o histórico de **consultas DNS** do [AdGuard Home](https://adguard.com/pt_br/adguard-home/overview.html), com identificação de computadores, classificação aproximada de serviços e controle de usuários.

O projeto instala a aplicação em um **container LXC Debian 13 no Proxmox VE**. Após a instalação, os usuários acessam os relatórios pelo navegador, sem instalar scripts em seus computadores.

> **Importante:** consultas DNS não comprovam navegação efetiva em um site ou uso de um aplicativo. O painel registra nomes de domínio consultados, não URLs completas nem conteúdo do tráfego.

## Funcionalidades

- Painel em português com identidade **VALLO — Consulta Acesso de Redes**.
- Histórico de consultas DNS importado da API do AdGuard Home.
- Hostname quando disponível (DNS reverso ou informações do cliente); caso contrário, IP.
- Classificação **aproximada** por serviço/categoria, preservando o domínio original.
- Filtros por período, computador, domínio/serviço e situação (permitido/bloqueado).
- Exportação dos dados filtrados em **CSV**, **Excel (.xlsx)** e **PDF**.
- Contas individuais com login e senha.
- **Somente a conta administradora inicial** pode criar, excluir e redefinir senhas de outros usuários. As demais contas podem consultar e exportar relatórios.
- Banco SQLite no próprio container, com retenção configurada para **90 dias** e coleta automática a cada aproximadamente **2 minutos**.

## Requisitos e configuração atual

O instalador foi preparado para o ambiente da VALLO e utiliza estas configurações **sem perguntar durante a instalação**:

| Item | Configuração |
| --- | --- |
| Proxmox | VE 8/9 com `pct`, `pveam`, `pvesh` e acesso à internet |
| Sistema do LXC | Debian 13, não privilegiado |
| ID do LXC | Próximo ID disponível no Proxmox |
| Bridge | `vmbr0` |
| Rede | IPv4 por **DHCP** |
| DNS configurado no LXC | `172.20.0.254` (pfSense) |
| AdGuard Home | `http://172.20.0.253` |
| Armazenamento de disco | `local-lvm` |
| Armazenamento de templates | `local` |
| Recursos | 2 vCPU, 1 GB RAM, 8 GB de disco |
| Histórico próprio | 90 dias |
| Coleta | Timer systemd, aproximadamente a cada 2 minutos |
| Acesso web | HTTPS com certificado autoassinado |
| Rede autorizada no Nginx | `172.20.0.0/16` |

A bridge, o DHCP e os storages devem existir e estar operacionais. O servidor AdGuard precisa estar acessível a partir do novo container. O instalador usa valores específicos da rede VALLO; **revise o código antes de utilizá-lo em outra infraestrutura**.

## Instalação automática

Execute no **shell do Proxmox como `root`**:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/refs/heads/main/Instalar-DNS-Reports-Proxmox.sh)"
```

O assistente solicita apenas:

1. Usuário do painel administrativo do AdGuard.
2. Senha do painel administrativo do AdGuard.
3. Nome de usuário do administrador do novo painel.
4. Senha do administrador do novo painel (mínimo de 10 caracteres).

Ele cria o LXC, instala Python, Flask, Gunicorn e Nginx, aplica as permissões, configura HTTPS e inicia o coletor e o painel.

Ao concluir, o endereço é exibido como:

```text
https://IP_RECEBIDO_VIA_DHCP/
```

**Recomendado:** crie uma reserva DHCP no pfSense para o novo container. Como o certificado inicial foi emitido para o IP recebido durante a instalação, uma alteração posterior do IP pode ocasionar alerta de certificado.

### Conferir o IP do container

Substitua `103` pelo ID mostrado ao final da instalação:

```bash
pct exec 103 -- ip -4 -o addr show dev eth0
```

O navegador poderá alertar sobre o certificado **autoassinado**. Para uso corporativo, considere instalar um certificado confiável e restringir o acesso a equipes autorizadas.

## Atualizar um painel já instalado

**Faça backup do container antes de atualizar.** Para o container atualmente utilizado pela VALLO (ID **103**):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/refs/heads/main/Instalar-DNS-Reports-Proxmox.sh)" vallo --update 103
```

Para outro ID, substitua `103`. A atualização altera os arquivos da aplicação e faz a migração necessária da estrutura de usuários; foi projetada para **preservar credenciais e banco de dados existentes**, mas backup e validação continuam recomendados.

Não utilize o comando de **instalação nova** para tentar atualizar um container existente.

## Usuários e permissões

Após fazer login como **administrador inicial**, abra a seção **Usuários do painel** (rota `/usuarios`). Ali é possível criar novas contas, redefinir senhas e excluir usuários comuns.

| Permissão | Administrador inicial | Usuário comum |
| --- | :---: | :---: |
| Visualizar consultas e gráficos | ✅ | ✅ |
| Filtrar por domínio, período e computador | ✅ | ✅ |
| Exportar CSV, Excel e PDF | ✅ | ✅ |
| Criar e gerenciar contas | ✅ | ❌ |

O controle é aplicado **no servidor**, não apenas ocultando o menu. Usuários comuns que tentarem acessar `/usuarios` diretamente recebem **HTTP 403**. A conta administrativa original não pode ser excluída pela interface.

## Consultar e exportar relatórios

1. Entre no painel em `https://IP_DO_LXC/`.
2. Selecione o período (24 horas, 7, 30 ou 90 dias, ou datas personalizadas).
3. Opcionalmente, escolha computador/hostname, domínio ou serviço e situação.
4. Use os botões **CSV**, **Excel** ou **PDF** na seção de consultas.

As exportações respeitam os filtros selecionados. Atualmente, há limite de **100.000 registros por arquivo CSV/Excel** e **1.000 por PDF**. Para períodos grandes, divida a consulta em intervalos menores.

### Registro dos hostnames

Para o nome do computador aparecer, é necessário haver informações do cliente no AdGuard ou **DNS reverso (PTR)** funcional. No ambiente da VALLO, o pfSense pode servir os registros PTR dos clientes DHCP.

Exemplo de teste a partir do Windows:

```cmd
nslookup 172.20.11.153 172.20.0.253
```

Quando não houver hostname disponível, o painel pode mostrar somente o IP.

### Coleta e armazenamento

- A primeira coleta tenta importar até **7 dias anteriores**, respeitando os registros ainda disponíveis no AdGuard e limites de paginação.
- As próximas coletas são programadas para aproximadamente **2 minutos**.
- O banco local retém registros por até **90 dias**, sujeitos ao espaço em disco e à continuidade das coletas.
- Se algum cliente não utilizar o AdGuard como DNS, suas consultas podem não aparecer.
- A classificação dos serviços é uma **inferência a partir do domínio DNS**, não uma identificação conclusiva do aplicativo executado.

## Comandos de manutenção

Troque `103` pelo ID correto:

```bash
# Estado dos serviços
pct exec 103 -- systemctl status dnsreports nginx --no-pager

# Últimas mensagens do painel
pct exec 103 -- journalctl -u dnsreports -n 50 --no-pager

# Últimas mensagens da coleta
pct exec 103 -- journalctl -u dnsreports-collector -n 50 --no-pager

# Verificar agendamento das coletas
pct exec 103 -- systemctl list-timers --all | grep dnsreports

# Executar uma coleta manual
pct exec 103 -- systemctl start dnsreports-collector.service

# Testar HTTPS dentro do container
pct exec 103 -- curl -k -I https://127.0.0.1/login
```

Arquivos principais no LXC:

- Aplicação: `/opt/dnsreports/`
- Configuração privada do AdGuard e credenciais iniciais: `/opt/dnsreports/config.json`
- Banco e histórico: `/opt/dnsreports/data/`
- Serviços: `dnsreports.service` e `dnsreports-collector.service`
- Agendamento: `dnsreports-collector.timer`

## Segurança e cuidados

- O instalador executa comandos como **root no Proxmox**. Leia o código e utilize somente a versão do repositório em que você confia.
- **Não publique** senhas, arquivos `config.json`, bancos de dados nem relatórios com dados de usuários no GitHub.
- Restrinja o acesso ao painel à rede e aos responsáveis autorizados. O Nginx do instalador libera apenas `172.20.0.0/16`.
- O acesso HTTPS inicial usa certificado autoassinado; prefira um certificado confiável em produção.
- O sistema consulta a API do AdGuard, mas **não substitui** o AdGuard, o pfSense nem o ntopng.
- As informações de consultas DNS podem ser dados pessoais. Defina política interna de acesso, uso e retenção adequada à finalidade.

---

**Projeto interno VALLO — Consulta Acesso de Redes.**