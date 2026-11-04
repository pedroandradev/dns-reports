# Histórico compacto por 90 dias

O histórico mantém **quem acessou qual serviço/domínio**, com horários,
contagem, volume e duração observada. Para economizar disco, eventos antigos
são consolidados antes de sair das tabelas de detalhes.

## Política padrão

| Dados | Detalhes recentes | Histórico |
| --- | --- | --- |
| DNS | Dois dias completos no fuso do painel mais o dia atual | Resumos diários por computador, domínio, tipo e resultado, por 90 dias |
| Acessos ntopng | Fluxos ativos, pendentes e encerrados recentemente | Resumos diários por equipamento/serviço, por 90 dias |
| Consumo | Agregações por hora | 90 dias |
| Cache de correlação DNS | Até 24h/100.000 respostas | Nomes já associados seguem nos registros de acesso |

O valor global retention_days em config.json controla a retenção do histórico
(padrão 90). Registros/resumos vencidos são removidos nas coletas; as páginas
liberadas do SQLite são reutilizadas. O arquivo não cresce indefinidamente
somente por acrescentar novos dias depois que a janela se estabiliza.

O alvo de 100.000 fluxos aplica-se ao detalhe recente. Os fluxos elegíveis são
consolidados antes de sua remoção; não é um limite que apaga o histórico de
90 dias. Fluxos ativos/pendentes não são removidos para cumprir esse alvo.
Encerrados aguardam pelo menos dez minutos de estabilização. O detalhe
encerrado normalmente é consolidado após um dia, ou antes se houver excesso.
O alvo pode ser excedido temporariamente quando não há fluxos seguros para
consolidar. Fontes antigas/pausadas ficam como histórico, sem inventar um fim.

Os antigos parâmetros ntopng_connection_retention_days e
ntopng_connection_limit deixam de controlar o histórico. Não é necessário
preencher novamente a integração ou substituir config.json.

## O que os resumos preservam

**DNS:** computador/IP/hostname, domínio, serviço provável, categoria,
tipo/resultado, bloqueio/motivo, quantidade e primeiro/último registro.
Repetições equivalentes no mesmo dia são contadas em uma linha. Os dias
seguem o fuso do painel e guardam limites UTC para o filtro funcionar também
em um único dia de calendário. A primeira data parcial de um filtro por
instante não entra na parte consolidada; nela são considerados dias completos.

**Acessos:** equipamento, serviço/domínio, evidência da identificação, contagem
de conexões por dia, bytes, primeiro/último horário e estado histórico.
O resumo inclui um destino de exemplo, sem preservar a lista completa de
IPs/portas de todos os fluxos encerrados. O detalhe recente continua completo.

A duração antiga é aproximada com resolução de um minuto. Os minutos são unidos,
sem multiplicar conexões paralelas, e guardados em um mapa de bits comprimido.
Intervalos de duração zero não ganham um minuto artificial. Conexões que
atravessam a meia-noite são divididas em dias UTC; contam como presentes em
cada dia. O volume acumulado do fluxo é lançado uma única vez, no último dia
de atividade conhecido, sem inventar uma distribuição por dias. Para consumo
por período, use as medições horárias de Consumo de rede.

Não some a duração de linhas recentes e históricas para medir atenção humana.
A duração não representa tempo efetivamente assistindo ou usando um aplicativo.
Classificações por domínio/DNS continuam inferências, identificadas no relatório.

## Consulta e exportação

O Visualizador de Acesso combina os fluxos recentes com resumos diários.
Consultas de DNS combina eventos recentes com resumos e usa as quantidades
consolidadas nos totais/rankings. CSV, Excel e PDF identificam os resumos;
nenhum resumo é apresentado como uma consulta ou conexão individual.

Os limites de consulta/exportação continuam protegendo a memória. Uma lista
parcial pede filtros mais específicos; não exclui os dados armazenados.

## Segurança da consolidação

A gravação do resumo e a remoção do detalhe ocorrem na mesma transação.
Uma interrupção reverte ambas; repetir a consolidação não duplica quantidades
ou bytes. Dias DNS já consolidados são selados para impedir que uma importação
posterior repita eventos cujos identificadores individuais já foram removidos.
Eventos DNS muito atrasados de dias selados não são reimportados.

Resumos são expirados após a janela de retenção. Os dias dos acessos são UTC;
o dia mais antigo pode ser mantido inteiro até a próxima limpeza. A janela
refere-se aos registros observados, não a pacotes completos ou conteúdo HTTPS.

## Atualização e disco

A atualização existente faz backup e pausa os serviços, instala a versão nova,
consolida o histórico e executa VACUUM uma vez para devolver espaço ao disco.
Essa manutenção pode levar alguns minutos em bancos grandes. As coletas normais
reutilizam páginas e não executam VACUUM em cada ciclo.

Snapshots de atualização novos guardam queries.sqlite3.gz em vez da cópia
descompactada. A compressão é verificada por SHA-256 após descompactar; o original
só é removido se o conteúdo for idêntico. O restore de código mantém o banco vivo,
como antes. Snapshots antigos podem ser comprimidos com o helper atualizado:

~~~bash
python3 scripts/proxmox/upgrade.py compress /opt/dnsreports/backups/update-IDENTIFICADOR
~~~

O snapshot é privado e pode conter credenciais. Para consultar uma cópia de
recuperação manual, descompacte-a em local privado:

~~~bash
gzip -dc queries.sqlite3.gz > /caminho/privado/queries.sqlite3
~~~

A quantidade de domínios/equipamentos e o ritmo de eventos ainda determinam o
tamanho final. O armazenamento compacto reduz repetições; dimensione e acompanhe
o disco com o volume real da instalação. Nenhum disco é ampliado automaticamente.

Versões anteriores à 1.6 exibem somente a parte recente do banco consolidado.
Os resumos permanecem no banco e voltam a aparecer com a versão compatível.
A consolidação não recria registros já perdidos por retenção anterior ou
durante períodos em que não houve coleta.
