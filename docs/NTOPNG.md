# ntopng: acessos observados e consumo complementar

O DNS-REPORTS mantém a integração atual com o AdGuard Home e adiciona uma fonte
opcional de tráfego ntopng. A integração foi desenhada a partir das respostas
REST v2 do ntopng 6.6 Community. Não requer reinstalar o container ou trocar a
licença para consultar os endpoints usados aqui.

## Visão principal de acessos

A versão 1.3.0 prioriza computador, serviço/site, tráfego, duração e estado em
/acessos e na página inicial. A consulta DNS permanece em /dns; os rankings de
banda seguem em /trafego. Consulte [ACCESSES.md](ACCESSES.md) para agrupamento,
identificação do destino, ativo/encerrado estimado e permissões.

## Atualizar uma instalação existente

No host Proxmox, use o modo de atualização com o ID do CT existente:

~~~bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
~~~

A versão deve estar integrada à main. Para homologar a branch desta alteração:

~~~bash
REPORTS_REF=feat/access-sessions \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/feat/access-sessions/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
~~~

O comando não cria outro CT e não solicita novamente as credenciais do AdGuard
ou a senha do administrador. Preserva o arquivo config.json, segredo de sessão,
retenção configurada, usuários, senhas, histórico DNS, IP, certificado e Nginx.
O acesso VPN configurado localmente permanece. Não defina REPORTS_VPN_CIDRS
durante a atualização comum, a menos que queira alterar explicitamente essa ACL.

Os serviços da aplicação são pausados durante o backup e a implantação.
Há indisponibilidade temporária do painel durante a atualização. Antes de
produção, mantenha também um backup/snapshot do CT pelo Proxmox.

A migração adiciona tabelas traffic_* ao mesmo banco SQLite. Não remove nem
converte a tabela de consultas DNS. O ntopng fica desabilitado até o administrador
salvar sua configuração.

## Configurar pelo painel

Entre com a conta administradora existente e abra **Integração ntopng**:

1. Informe a URL do ntopng acessível a partir do CT.
2. Informe um usuário do ntopng com permissão de leitura dos dados necessários
   e sua senha. A senha não é devolvida ao navegador; campo vazio mantém a salva.
3. Informe o ifid da interface LAN. O identificador é configurável.
4. Informe todas as redes internas que devem ser tratadas como corporativas,
   incluindo redes de unidades/VPN. Use CIDRs; não use /0.
5. Cadastre os locais, se desejar, e marque Habilitar coleta.
6. Use **Testar e salvar** para verificar conexão/interface e gravar. **Salvar
   configuração** permite gravar mesmo quando a origem está temporariamente fora.

Exemplos ilustrativos, sem endereços de uma instalação real:

~~~text
URL: https://ntopng.exemplo.local:3001
ifid: 0
Redes internas:
10.20.0.0/16
10.30.0.0/16

Locais:
Unidade A = 10.20.0.0/16
Escritório = 10.20.1.0/24
Unidade B = 10.30.0.0/16
~~~

A rede mais específica determina o local. Equipamentos sem correspondência
aparecem como Sem local. Um prefixo de IP só identifica um local se sua rede
realmente segue essa divisão; não há geolocalização automática dos computadores.
A associação registrada em medições anteriores não muda ao renomear os locais.

Use uma interface LAN por integração. Somar capturas simultâneas da LAN e WAN
pode contar o mesmo tráfego duas vezes. Antes do NAT é possível preservar a
identificação dos clientes. O coletor não altera interfaces, firewall ou VPN.

### Conectividade e HTTPS

127.0.0.1 no painel refere-se ao próprio CT, não ao pfSense. Um teste feito no
shell do firewall não comprova acesso a partir do container.

Se necessário, confira no pfSense os listeners e permita apenas o IP do CT
até a porta interna do ntopng. Não publique essa administração na WAN.
Utilize HTTPS no ntopng ou um túnel protegido; HTTP transmite a autenticação
Basic e os dados sem criptografia. A opção de validar certificado é ligada
por padrão. Para uma CA interna, instale a CA no container e atualize o
repositório de certificados Debian. As unidades usam
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt.

O cliente não segue redirecionamentos e valida o envelope rc/rsp. Respostas HTML
de login ou falhas da API não são aceitas como uma lista vazia de tráfego.

## O que os números significam

Existem duas visões distintas em **Consumo de rede**:

| Visão | Origem dos bytes | Cobertura |
| --- | --- | --- |
| Tráfego LAN | Diferenças de contadores por equipamento e, separadamente, por aplicação | Inclui tráfego interno visível à captura; não é o total exclusivo da internet |
| Internet observada | Diferenças de bytes dos fluxos ativos com uma ponta nas redes internas e outra pública fora delas | Parcial: conexões curtas, primeira/última parte dos fluxos e perdas de captura podem ficar ausentes |

A primeira leitura cria uma base. Não importa contadores acumulados antes de
habilitar a integração como se fossem consumo novo. Reinício, recriação do host,
queda de contador ou reativação da fonte cria uma nova base, sem inventar bytes.
Uma falha na consulta não substitui a base anterior por zero. Bases de equipamentos sem atualização por 24 horas e bases de fluxos sem atualização por 10 minutos expiram; seu retorno cria uma nova base.

Contadores de host trazem bytes enviados e recebidos. A lista de aplicações traz
total por aplicação. O endpoint de fluxos ativos usado não fornece contadores
exatos por sentido; percentuais arredondados não são convertidos em downloads
ou uploads supostamente exatos. Campos sem essa informação ficam vazios.

O coletor usa paginação, não apenas os primeiros/top 5 equipamentos. Há limites
de 10.000 registros por lista e orçamento de tempo para consultas de aplicações.
A ordem dos equipamentos gira quando o orçamento é atingido, para não deixar
sempre os mesmos sem coleta. O painel informa falhas/coletas parciais. Estado ok significa que as consultas responderam; a visão de internet continua sendo parcial.

A classificação usa o nome retornado pelo ntopng: Netflix, TLS.Netflix,
MS_OneDrive, TLS, QUIC ou Unknown, por exemplo. Um nome de destino reportado
também pode vir de resolução de nomes; não é necessariamente um hostname HTTP/SNI
extraído daquela conexão. Não se afirma qual página foi aberta, qual vídeo foi
assistido ou por quanto tempo uma pessoa olhou a tela.

Os campos server.is_dhcp/is_broadcast da API 6.6 podem refletir o cliente; não são
usados para decidir se o destino é interno. A classificação considera endereços,
redes explicitamente configuradas e destinos públicos.

### Histórico e espaço em disco

- Coleta: timer separado, 30 segundos depois do término do ciclo anterior.
- Medições: agregadas por equipamento, aplicação, local e hora UTC, seguindo a
  retenção existente em config.json (padrão 90 dias).
- Cada diferença pertence à hora da leitura final. Não há rateio presumido
  entre minutos/dias quando o intervalo atravessa uma fronteira.
- Filtros consideram horas inteiras a partir do início selecionado e a última
  leitura até o fim selecionado. Uma primeira hora parcial não entra no total.
- Conexões detalhadas: últimos 7 dias e até 100.000 fluxos, o que limitar primeiro.
  Uma conexão observada repetidamente é atualizada, não inserida em duplicidade.
- Os bytes acumulados de cada conexão não devem ser somados aos rankings, que
  utilizam diferenças entre leituras. O contador LAN e o ranking por aplicação
  são fontes independentes; somá-los duplica consumo.

Os limites de conexões podem ser configurados pelo operador no config.json:
ntopng_connection_retention_days (limitado à retenção geral) e
ntopng_connection_limit (1.000 a 1.000.000). Valores maiores exigem mais disco.
O painel mostra a política efetiva e o coletor aplica a limpeza.

A agregação limita o crescimento, mas o tamanho final depende da quantidade de
equipamentos/aplicações e do histórico DNS já existente. Monitore o disco do CT.
O ranking de aplicações continua disponível nas medições retidas mesmo depois
de os fluxos detalhados mais antigos serem removidos.

Para uma auditoria completa de todas as conexões, será necessária uma origem
persistente de fluxos/exportação compatível com a edição do ntopng ou outro
coletor. Consultas periódicas à API Community não oferecem essa garantia.

## Serviços e diagnóstico

~~~bash
pct exec ID_DO_CONTAINER -- systemctl status dnsreports dnsreports-ntopng.timer --no-pager
pct exec ID_DO_CONTAINER -- journalctl -u dnsreports-ntopng -n 50 --no-pager
pct exec ID_DO_CONTAINER -- systemctl start dnsreports-ntopng.service
~~~

O serviço utiliza o usuário restrito dnsreports, bloqueio contra execuções
simultâneas, validação TLS, timeouts e acesso de escrita apenas em data/.
Os dados de integração e credenciais ficam em traffic_settings, dentro do banco
privado. Somente o administrador altera a integração; usuários comuns consultam
e exportam relatórios. Não publique o banco ou seus backups.

Exportações CSV, Excel e PDF preservam o filtro e identificam o escopo/qualidade.
CSV/Excel: até 100.000 registros agregados; PDF: até 1.000. Valores de bytes
exatos são mantidos nas exportações; apresentação em kB/MB/GB usa base decimal.

## Referências e compatibilidade

- [ntopng 6.6: hosts ativos](https://github.com/ntop/ntopng/blob/6.6-stable/scripts/lua/rest/v2/get/host/active.lua)
- [ntopng 6.6: aplicações do host](https://github.com/ntop/ntopng/blob/6.6-stable/scripts/lua/rest/v2/get/host/l7/stats.lua)
- [ntopng 6.6: fluxos ativos](https://github.com/ntop/ntopng/blob/6.6-stable/scripts/lua/rest/v2/get/flow/active.lua)
- [REST v2: autenticação e envelope](https://www.ntop.org/guides/ntopng/api/rest/api_v2.html)

As respostas foram verificadas com amostras fornecidas de uma instalação 6.6
Community. Testes automatizados usam dados sintéticos, sem IPs, nomes ou MACs
da rede de produção. A implantação real em Proxmox/pfSense deve ser homologada.
