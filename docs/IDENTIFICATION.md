# Identificar destinos e entender os registros sem nome

O objetivo é registrar **computador → serviço ou domínio → volume → duração
observada → estado**. Nem todo fluxo de rede contém um nome de site. O painel
mostra a evidência disponível e distingue nome capturado, inferência e falta de
identificação.

## Por que aparece “Não identificado”

| Registro | O que permite concluir |
| --- | --- |
| TLS/QUIC | O transporte foi identificado; isso sozinho não informa o site |
| Unknown | O ntopng não identificou a aplicação desse fluxo |
| TooEarly | A classificação do fluxo ainda não terminou; não é um aplicativo |
| ICMP, STUN, DNS e outros protocolos técnicos | Há comunicação desse tipo, sem identificação de um site específico |
| 54/66 bytes, início igual à última atividade | Poucos dados observados; pode haver apenas sinalização, sem payload suficiente para reconhecer o destino |

O tamanho sozinho não permite dizer se foi SYN, ACK, FIN ou tentativa de
conexão. “Ativa na última coleta” significa presença na lista do ntopng e não
prova de navegação, conexão estabelecida ou uso humano.

Um fluxo Unknown com vários MB exige análise própria: pode faltar metadado na
API, captura do início da conexão ou suporte do classificador. Não se explica
todo Unknown pelo tamanho pequeno.

## Fontes usadas a partir da versão 1.4.0

1. **Aplicação específica do nDPI**, por exemplo TLS.Netflix ou STUN.WhatsApp.
2. **Nome no fluxo ntopng**, extraído de info na API detalhada quando o protocolo
   é compatível com tráfego web. Pode ser SNI/host HTTP, conforme o ntopng
   disponibilizar. O painel guarda somente o hostname, sem caminho, query,
   conteúdo de páginas ou credenciais de URLs. O nome é associado ao servidor
   desse fluxo; fluxos de entrada não atribuem o nome do servidor local ao
   cliente externo.
3. **Correlação DNS (inferência)**, opcional e ligada por padrão: uma resposta
   A/AAAA bem-sucedida e não bloqueada do AdGuard deve ter o mesmo IP de cliente
   e retornar o IP remoto. O início do fluxo deve ocorrer entre o horário da
   consulta e o fim do TTL. Uma pergunta DNS sem resposta IP não basta.
4. **Nome de destino reportado** pelo ntopng. Pode vir do cache de nomes/PTR do
   host, sem ser um nome extraído daquela conexão.
5. **IP e porta**, mantendo o destino não identificado quando faltam evidências.

Uma regra de classificação que transforma um domínio conhecido em “Netflix”
ou outro serviço permanece identificada como inferência. O domínio original
continua disponível nos detalhes. O nDPI também é um classificador; seu rótulo
não comprova que alguém estava assistindo ou usando a interface do aplicativo.

Se o mesmo cliente resolveu vários domínios para o mesmo IP dentro da janela
válida, aparece **DNS ambíguo (vários domínios)** e os candidatos ficam nos
detalhes. O sistema não escolhe o último domínio arbitrariamente, não consulta
DNS de outro computador e não transforma o dono de um IP/CDN em um site certo.

Nomes capturados e aplicações específicas são mantidos para o mesmo fluxo
quando uma leitura posterior omite esses dados. Não são transferidos para
outras conexões que compartilhem o IP. A ambiguidade DNS já observada não é
apagada quando o cache temporário é limpo. Um nome capturado no próprio fluxo
ou uma aplicação específica pode resolver a identificação posteriormente.

A API preferida é GET /lua/rest/v2/get/flow/active_list.lua, com start/length,
ordenada por first_seen. Na 6.6 Community, ela retorna info e os contadores em
um formato diferente da API básica. O cliente normaliza o formato e conserva
a identidade do fluxo. Se o endpoint não existe (HTTP 404/405 na primeira
página), usa flow/active.lua. Falhas de autenticação, registros inválidos,
paginação repetida ou interrupção após a primeira página são falhas de coleta,
sem confirmar encerramentos. Ambas as opções usam limites de páginas/registros.

## Configuração e limites do DNS

Só o administrador pode alterar **Usar respostas DNS do AdGuard para inferir
destinos sem nome**, em Integração ntopng. A atualização preserva URL, senha,
ifid, redes internas, locais e critérios existentes. Não é necessário preencher
a integração novamente. Desativar a opção impede novas correlações; evidências
já arquivadas continuam no histórico.

O AdGuard precisa receber o IP real dos computadores e registrar respostas.
Se todas as consultas chegam com o IP do firewall/resolvedor intermediário,
não é possível associá-las com segurança aos clientes individuais. Consultas
enviadas por outro DNS/DoH/DoT fora do AdGuard não alimentam esta correlação.
Confira o campo client do querylog e mantenha os relógios dos serviços
sincronizados. A correlação exige IPs de cliente únicos nas redes acompanhadas;
ela não diferencia VLANs/VRFs com endereços sobrepostos nem prova identidade
por MAC após uma troca de concessão DHCP.

A tabela aditiva dns_resolutions mantém no máximo 100.000 respostas recentes
por 24 horas, com TTL limitado a 24 horas e até 64 respostas por evento. Uma
busca mantém até 17 candidatos para distinguir caso único de ambíguo; a
apresentação é limitada. Os limites podem reduzir a cobertura em redes muito
movimentadas. “Um candidato” significa um candidato observado neste cache,
não certeza de acesso humano. Prefetch, serviços em segundo plano e IPs
compartilhados continuam sendo limitações da inferência.

A coleta DNS continua com seu timer existente, independente do timer ntopng.
Uma coleta posterior do fluxo pode aproveitar uma resposta DNS importada com
atraso. Respostas antigas não eram arquivadas antes desta versão: a atualização
captura respostas novas e as do intervalo de sobreposição normal da coleta.
Não reconstrói respostas históricas ausentes. Nomes já salvos nos fluxos seguem
a retenção do histórico de conexões.

## Como consultar

O filtro **Identificação** permite consultar todos os registros, os que têm
serviço/domínio, os sem nome, os pendentes e os protocolos técnicos. Nenhuma
dessas categorias é descartada. Na mesma prioridade de estado, serviços e
domínios aparecem primeiro, seguidos dos desconhecidos com maior volume.
Isso evita que dezenas de fluxos mínimos ocultem os acessos mais informativos.

Os detalhes mostram IP/porta, aplicação original e candidatos DNS. CSV/Excel
incluem tipo, evidência, observação e candidatos; PDF mantém evidência e
observação na coluna do serviço.

## Conferir um caso na origem

No shell do pfSense que executa o ntopng, substitua ifid e o IP ilustrativo:

~~~sh
curl -sS --connect-timeout 5 --max-time 15 --user admin \
  'http://127.0.0.1:3000/lua/rest/v2/get/flow/active_list.lua?ifid=0&host=10.20.1.25&start=0&length=5&sort=last_seen&order=desc'
~~~

A senha é solicitada interativamente. Compare client.ip, server.ip/port,
application.name, info, first_seen/last_seen e bytes.total com os detalhes do
painel. Para compartilhar diagnóstico, bastam esses campos com info reduzido
ao hostname, sem caminhos ou tokens.

- Se o ntopng conhece o nome em info mas o painel não, investigue o coletor/API.
- Se a API também só contém IP e TLS/Unknown, confira se a captura observa os
  dois sentidos e o início de uma conexão nova na interface LAN correta.
- Se o DNS é a fonte esperada, confira IP do cliente, resposta A/AAAA, horário
  e TTL no AdGuard.
- TLS com SNI visível pode fornecer nomes mesmo criptografando o conteúdo.
  ECH pode ocultar o nome interno; um túnel/VPN pode ocultar os destinos
  transportados. A captura passiva não garante recuperar esses nomes.
- Um IP de CDN ou PTR genérico, sozinho, não justifica identificar um site.

Não é necessário mudar Nginx, ACL VPN ou desativar criptografia para aplicar
esta atualização. Ela melhora o uso dos metadados disponíveis; não promete
identificar todos os destinos criptografados.

## Atualizar o mesmo container

Depois do merge na main, no host Proxmox:

~~~bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/main/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
~~~

Para homologar a branch antes do merge:

~~~bash
REPORTS_REF=fix/destination-identification \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/fix/destination-identification/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
~~~

O instalador mantém backup/restauração e a migração é aditiva. Configurações
ntopng/AdGuard, contas, histórico e ACL LAN/VPN existentes são preservados.
As APIs detalhadas foram conferidas no código oficial 6.6 e testadas com dados
sintéticos; a identificação precisa ser homologada na instalação real.

## Referências

- [ntopng 6.6: API detalhada](https://github.com/ntop/ntopng/blob/6.6-stable/scripts/lua/rest/v2/get/flow/active_list.lua)
- [ntopng 6.6: info e classificação pendente](https://github.com/ntop/ntopng/blob/6.6-stable/src/Flow.cpp)
- [ntopng: fluxos e metadados TLS](https://www.ntop.org/guides/ntopng/user_interface/network_interface/flows/flows.html)
- [AdGuard Home: querylog e respostas DNS](https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml)
- [Cloudflare: ECH e ocultação do nome interno](https://developers.cloudflare.com/ssl/edge-certificates/ech/)
