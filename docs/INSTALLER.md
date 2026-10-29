# Instalador e atualização no mesmo container

## Interface do instalador

O link público continua sendo Instalar-DNS-Reports-Proxmox.sh. O bootstrap
baixa o pacote e chama o instalador modular.

Após o download, o instalador mostra um título ASCII grande DNS-REPORTS,
o modo (instalação/atualização), o CT e oito etapas:

1. Validação e configuração.
2. Preparação do container.
3. Dependências do sistema.
4. Proteção dos dados.
5. Aplicação Python.
6. Serviços e banco.
7. Proxy HTTPS.
8. Inicialização e validação.

A barra avança por etapas concluídas. Durante tarefas longas, um indicador
animado informa que o componente continua sendo instalado. Não representa uma
estimativa de tempo ou percentual interno do apt/pip. Em terminais sem suporte
a animação, são emitidas linhas estáticas com início e resultado.

A saída completa dos componentes fica em um arquivo privado no host Proxmox,
por padrão /var/log/dnsreports/install-DATA-PID.log. O caminho aparece no início,
na conclusão e em caso de erro. As senhas são solicitadas sem eco; o instalador
não ativa shell tracing nem imprime argumentos dos comandos.

Variáveis opcionais:

| Variável | Uso |
| --- | --- |
| REPORTS_VERBOSE=1 | Exibir a saída detalhada dos componentes no terminal |
| REPORTS_NO_COLOR=1 | Usar apresentação sem cores/animação |
| REPORTS_INSTALL_LOG=/caminho/arquivo.log | Escolher o arquivo de diagnóstico |

O layout é original, inspirado na apresentação compacta de instaladores da
comunidade Proxmox. Não baixa nem executa bibliotecas externas desses projetos.

## Atualização de produção

Use --update ID_DO_CONTAINER no host Proxmox. O modo de instalação sem argumentos
cria um CT novo e não deve ser usado para atualizar uma instalação existente.

A atualização não solicita novamente credenciais e não recria rede, disco ou CT.
Mantém o config.json em conteúdo, incluindo o segredo de sessão e a retenção.
As tabelas existentes de DNS/usuários são preservadas e as novas tabelas são
criadas de forma aditiva. O Nginx/certificado/ACL local permanece, exceto quando
o operador pede explicitamente uma mudança com REPORTS_VPN_CIDRS.

O painel fica temporariamente indisponível enquanto os serviços são pausados.
Um lock no host impede duas atualizações simultâneas do mesmo CT.

### Backup automático

Antes de alterar o pacote, o instalador pausa os coletores e a aplicação e cria:

~~~text
/opt/dnsreports/backups/update-DATA-IDENTIFICADOR/
  app/                  # config, venv, source e app/core legados, quando presentes
  units/                # unidades systemd anteriores
  queries.sqlite3       # cópia consistente via SQLite Backup API
  upgrade.py            # auxiliar para recuperação
  manifest.json         # escrito somente ao concluir o backup
~~~

Os backups são privados, versionados e não são sobrescritos pela execução seguinte.
É necessário espaço para a cópia da aplicação/banco e margem para instalação
de pacotes. Se o backup falhar, a instalação do pacote não prossegue.

Mantenha também um snapshot/backup do CT no Proxmox: o backup da aplicação não
substitui uma cópia do sistema inteiro. Backups antigos não são apagados
automaticamente; faça sua manutenção conforme a política de retenção.

### Falha e recuperação

Se uma etapa de atualização falhar depois da pausa dos serviços, o instalador
tenta restaurar código, venv, config e unidades anteriores e reiniciar o painel.
O log e o diretório de backup são informados. Arquivos da tentativa malsucedida
ficam guardados em failed-* dentro do backup para diagnóstico.

A recuperação automática mantém o banco vivo, pois as migrações são aditivas,
evitando descartar registros novos. A cópia consistente do banco está disponível
para recuperação manual se houver um problema independente no banco.

Antes de uma recuperação manual, identifique o backup desejado e pause os
serviços. No console do CT:

~~~bash
systemctl stop dnsreports-collector.timer dnsreports-collector.service dnsreports.service
systemctl disable --now dnsreports-ntopng.timer
systemctl stop dnsreports-ntopng.service
python3 /opt/dnsreports/backups/update-DATA-IDENTIFICADOR/upgrade.py restore /opt/dnsreports/backups/update-DATA-IDENTIFICADOR
systemctl daemon-reload
systemctl restart dnsreports.service
systemctl enable --now dnsreports-collector.timer
~~~

Se a versão restaurada já tinha o timer ntopng, habilite-o novamente.
Não copie um snapshot antigo do banco sobre o banco vivo sem avaliar os
registros posteriores ao backup.

Não desligue o host/CT durante a atualização. Uma interrupção abrupta sem
execução dos handlers exige inspeção dos backups e recuperação pelo operador.

## Configurar ntopng após atualizar

Faça login com o administrador existente e abra Integração ntopng. Somente os
dados novos de conexão com o ntopng precisam ser informados. A instalação antiga
do AdGuard continua funcionando. Consulte [NTOPNG.md](NTOPNG.md).
