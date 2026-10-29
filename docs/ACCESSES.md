# Acessos por computador e serviço/site

A página inicial agora responde à pergunta **quem está acessando o quê**.
As consultas DNS continuam em /dns e o consumo de banda fica como visão
complementar em /trafego. /acessos é um endereço direto para o painel principal.

## Como ler um registro

Exemplo ilustrativo:

~~~text
PC-EXEMPLO → Netflix → 5,00 GB → 2h de conexão observada → Encerrada (estimativa)
~~~

Cada registro apresenta computador, IP/MAC, serviço ou site, evidência da
identificação, destinos detalhados, tráfego acumulado dos fluxos retidos,
duração observada, estado e horários.

A prioridade para o destino é:

1. Aplicação identificada pelo nDPI, como TLS.Netflix ou QUIC.YouTube.
   Prefixos de transporte são retirados para exibir Netflix ou YouTube.
2. Nome do próprio fluxo na API detalhada do ntopng, quando disponível.
3. Correlação com resposta DNS do mesmo cliente/IP, dentro do TTL, como inferência.
4. Nome de destino reportado pelo ntopng, quando disponível. Se uma regra
   conhecida relacionar esse domínio a um serviço, a identificação aparece
   explicitamente como inferência por domínio.
5. IP e porta, com aplicação não identificada. Vários domínios candidatos para
   um IP mantêm a ambiguidade; uma pergunta DNS isolada não identifica o site.

Detalhes, protocolos técnicos e diagnóstico: [IDENTIFICATION.md](IDENTIFICATION.md).

Ao expandir o registro, aparecem nomes/IPs/portas e aplicações originais dos
fluxos. Um nome reportado pelo ntopng pode ser de resolução de nomes; não é
necessariamente SNI/Host extraído daquela conexão.

“Unidade/setor” identifica onde o equipamento está cadastrado. O destino que
ele acessa está em “Serviço/site”; são informações distintas.

## Estado e horário de encerramento

| Estado | Significado |
| --- | --- |
| Ativa na última coleta | O fluxo apareceu na lista de ativos retornada pelo ntopng |
| Aguardando confirmação | Deixou de aparecer, mas ainda não cumpriu o critério de ausência |
| Encerrada (estimativa) | Ausência por pelo menos o intervalo configurado e duas coletas completas |
| Sem confirmação recente | Coleta falhou, ficou incompleta ou sem atualização por mais de 180 segundos |
| Acompanhamento pausado | Integração desabilitada ou registro de uma origem anterior |
| Histórico sem acompanhamento | Registro anterior à migração que ainda não foi reobservado |

O endpoint ativo do ntopng 6.6 usado nesta integração não fornece horário exato
de FIN/RST ou de fechamento da aplicação. O painel não o fabrica. Para um acesso
encerrado por estimativa, mostra quando foi visto pela última vez, a primeira
ausência e quando a ausência foi verificada. O intervalo apresentado é quando
deixou de aparecer na coleta, não uma prova do instante do fechamento TCP.

Uma falha de rede, página de API repetida/truncada ou registro inválido não
confirma ausência. Um intervalo longo sem coleta reinicia a confirmação de
ausência; o tempo sem observar a origem não é contado como confirmação.
Fluxo que reaparece volta ao estado ativo sem duplicar bytes.

O administrador pode ajustar “Confirmar ausência após”, de 60 a 3.600 segundos,
com padrão 120. Também é a tolerância para agrupar conexões próximas do mesmo
equipamento e serviço. Consultas de usuários comuns nunca alteram esse critério.

## Agrupamento, duração e tráfego

- Acessos são agrupamentos observados de fluxos do mesmo equipamento, origem,
  VLAN, serviço/site e escopo. Visitas separadas por uma lacuna maior que a
  tolerância formam registros distintos.
- Netflix/YouTube podem abrir vários fluxos simultâneos. A duração é a união
  dos intervalos first_seen/last_seen reportados, sem somar o tempo paralelo.
  Essa duração não mede atenção, tela aberta ou tempo efetivamente assistindo.
- Os bytes são a soma dos contadores acumulados de cada fluxo único retido.
  Atualizações da mesma conexão não duplicam os bytes.
- Um registro pode começar antes da primeira observação do painel, se esse
  início foi informado pelo ntopng. Os bytes também podem conter esse trecho
  anterior. O filtro seleciona acessos relacionados ao período; não rateia
  retroativamente o tráfego para um dia específico.
- O acompanhamento é por amostragem. Conexões que começam e terminam entre
  coletas podem não aparecer.
- O agrupamento usa o critério atual; alterar a tolerância pode alterar como
  os registros retidos são agrupados. Os fluxos originais são preservados.

A retenção e o limite de fluxos são os mesmos do registro de conexões: padrão
7 dias/100.000 fluxos, configuráveis conforme NTOPNG.md. Dados removidos por
retenção não fazem parte dos agrupamentos. As medições horárias de consumo
continuam separadas e seguem a retenção geral.

## Consulta e exportação

Filtros por computador/IP/MAC, serviço/site/destino, estado, unidade/setor,
escopo e período. A página mostra 25 agrupamentos por vez.

A atualização automática é de 30 segundos, pode ser desligada e é suspensa
quando há edição do filtro ou detalhes abertos. Ela não acelera a coleta na origem.

Uma consulta processa até 10.000 fluxos retidos. Se exceder esse limite, avisa
que a visão é parcial e exige refinar o filtro antes de exportar.
CSV/Excel exportam os agrupamentos do filtro; PDF é limitado a 1.000.
As exportações incluem estado, evidência, duração e observação do fim estimado,
sem credenciais da integração. O estado é o conhecido no momento da consulta,
não uma reconstrução exata do estado em uma data passada.

## Permissões

Somente a conta com papel de administrador pode acessar, testar, habilitar,
desabilitar ou salvar a integração. A proteção ocorre no backend, além de
ocultar os controles na navegação. Sessões com papel forjado não substituem
o papel consultado no banco.

Os outros usuários visualizam e exportam acessos, DNS e consumo. Não alteram a
configuração ntopng ou a associação de unidades. As páginas de relatório não
possuem ações de edição da origem.

## Atualizar sem reinstalar

A versão 1.4.0 mantém o instalador e --update ID_DO_CONTAINER. A migração adiciona
colunas de acompanhamento à tabela de conexões existente. Configuração ntopng,
credenciais, DNS, usuários e consultas anteriores permanecem.

Registros antigos recebem estado histórico, não “ativo” ou “encerrado” sem uma
nova observação. O valor padrão do novo critério de ausência é carregado sem
exigir reconfigurar a integração existente.

Antes do merge, para homologar a branch:

~~~bash
REPORTS_REF=fix/destination-identification \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedroandradev/dns-reports/fix/destination-identification/Instalar-DNS-Reports-Proxmox.sh)" reports --update ID_DO_CONTAINER
~~~

Depois do merge, pode-se usar o mesmo comando público da main com --update.
Consulte INSTALLER.md para backup, indisponibilidade temporária e recuperação.

## Referências

- [ntopng: fluxos, duração e aplicações](https://www.ntop.org/guides/ntopng/user_interface/network_interface/flows/flows.html)
- [Código do endpoint ativo na versão 6.6](https://github.com/ntop/ntopng/blob/6.6-stable/scripts/lua/rest/v2/get/flow/active.lua)
