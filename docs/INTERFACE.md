# Relatórios, configurações e temas

A interface 1.5.0 usa o mesmo layout em todos os relatórios e na administração.
A navegação não muda ao abrir DNS ou usuários.

## Menu de relatórios

| Opção | Endereço | Conteúdo |
| --- | --- | --- |
| Visualizador de Acesso | /acessos ou / | Computador, serviço/destino, tráfego, duração observada e estado |
| Consultas de DNS | /dns | Consultas recebidas pelo AdGuard, bloqueios, domínios e exportação |
| Consumo de rede | /trafego | Medições e rankings de tráfego por computador, aplicação e local |

O item correspondente à página é destacado, incluindo na versão para celular.
Os atalhos de seção que duplicavam “Consultas DNS” no menu foram removidos.
As exportações DNS ficam junto ao registro de consultas, com os filtros atuais.
Os relatórios mantêm suas fontes e critérios; mudar de seção não altera a coleta.

## Engrenagem de Configurações

A engrenagem no topo abre /configuracoes, disponível somente para o
administrador. Essa área reúne:

- Integração ntopng: origem, credenciais, redes, locais e critérios existentes.
- Usuários do painel: criação de contas e redefinição de senhas.
- Aparência: o mesmo controle de tema disponível no topo.

Dentro da administração, as abas mostram Visão geral, Integração ntopng e
Usuários do painel. Os endereços anteriores /integracoes/ntopng e /usuarios
continuam funcionando para preservar links existentes.

Usuários de visualização acessam os três relatórios, suas exportações e o
botão de tema. A configuração administrativa continua protegida no backend,
inclusive por URL direta ou sessão com papel forjado.

## Tema claro e escuro

O botão de sol/lua alterna o tema em relatórios, administração e login.
Na primeira visita, acompanha a preferência clara/escura do sistema.
Uma escolha manual é salva em localStorage, na chave dnsreports.theme, e
mantida ao trocar de página, recarregar e entrar/sair. Abas do mesmo navegador
e origem acompanham a alteração.

A escolha é do navegador, não uma configuração global da instalação nem uma
preferência gravada na conta do usuário. Se o navegador bloquear armazenamento
local, a troca continua funcionando na página, mas não persiste ao recarregar.
As cores de formulários, tabelas, avisos e estados também acompanham o tema.

## Organização e compatibilidade

- document.html: documento, estilos, script de tema e favicon.
- base.html: marca, navegação de relatórios, cabeçalho e abas administrativas.
- _icons.html e _theme_toggle.html: ícones e controle compartilhado.
- dashboard.html, accesses.html, traffic.html, users.html e ntopng_settings.html:
  conteúdo específico de cada página, sem duplicar o menu.
- style.css: cores semânticas para os dois temas e layout responsivo.
- theme.js: inicialização do tema antes dos estilos e atualização dos controles.

Scripts e estilos permanecem em arquivos locais, compatíveis com a política
CSP existente; não exigem CDN, bibliotecas externas ou acesso à internet.

A atualização utiliza o mesmo --update ID_DO_CONTAINER. Não altera banco,
retenção, credenciais, usuários, Nginx ou ACL LAN/VPN. A nova interface precisa
estar integrada à main antes de usar o comando público de atualização.
