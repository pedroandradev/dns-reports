"""DNS domain to probable service classification."""
# Specific patterns first. Labels describe DNS domains, not proof of app usage.
SERVICES = [
    ('YouTube', 'Streaming', ('youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com')),
    ('Netflix', 'Streaming', ('netflix.com', 'nflxvideo.net', 'nflximg.net', 'nflxso.net')),
    ('Spotify', 'Streaming', ('spotify.com', 'scdn.co', 'spotifycdn.com')),
    ('Twitch', 'Streaming', ('twitch.tv', 'ttvnw.net')),
    ('Instagram', 'Redes sociais', ('instagram.com', 'cdninstagram.com')),
    ('Facebook', 'Redes sociais', ('facebook.com', 'fbcdn.net', 'fbsbx.com')),
    ('TikTok', 'Redes sociais', ('tiktok.com', 'tiktokcdn.com', 'tiktokv.com', 'byteoversea.com')),
    ('X / Twitter', 'Redes sociais', ('x.com', 'twitter.com', 'twimg.com')),
    ('LinkedIn', 'Redes sociais', ('linkedin.com', 'licdn.com')),
    ('WhatsApp', 'Comunicação', ('whatsapp.com', 'whatsapp.net')),
    ('Telegram', 'Comunicação', ('telegram.org', 't.me')),
    ('Microsoft Teams', 'Comunicação', ('teams.microsoft.com', 'teams.live.com', 'skype.com')),
    ('Zoom', 'Comunicação', ('zoom.us', 'zoom.com')),
    ('Google Meet', 'Comunicação', ('meet.google.com',)),
    ('Discord', 'Comunicação', ('discord.com', 'discordapp.com', 'discord.gg')),
    ('OneDrive / SharePoint', 'Trabalho', ('sharepoint.com', 'onedrive.com', '1drv.com', 'storage.live.com')),
    ('Microsoft 365', 'Trabalho', ('office.com', 'office365.com', 'outlook.com', 'graph.microsoft.com', 'microsoftonline.com', 'login.microsoftonline.com')),
    ('Google Drive', 'Trabalho', ('drive.google.com', 'docs.google.com', 'sheets.google.com')),
    ('Google Workspace', 'Trabalho', ('gmail.com', 'mail.google.com', 'workspace.google.com')),
    ('TOTVS', 'Trabalho', ('totvs.com', 'totvs.com.br', 'cloudtotvs.com.br')),
    ('Windows Update', 'Sistema / atualizações', ('windowsupdate.com', 'update.microsoft.com', 'delivery.mp.microsoft.com')),
    ('Microsoft', 'Sistema / serviços', ('microsoft.com', 'msftconnecttest.com', 'msftncsi.com', 'azure.com', 'azureedge.net')),
    ('Google', 'Sistema / serviços', ('google.com', 'googleapis.com', 'gstatic.com', 'googleusercontent.com')),
    ('Cloudflare', 'Infraestrutura', ('cloudflare.com', 'cloudflare-dns.com')),
    ('Amazon AWS', 'Infraestrutura', ('amazonaws.com', 'aws.amazon.com', 'cloudfront.net')),
    ('AnyDesk', 'Acesso remoto', ('anydesk.com',)),
    ('TeamViewer', 'Acesso remoto', ('teamviewer.com',)),
    ('Ubiquiti', 'Infraestrutura', ('ui.com', 'ubnt.com')),
]


def classify(domain):
    d = (domain or '').lower().strip('. ')
    for service, category, patterns in SERVICES:
        if any(d == p or d.endswith('.' + p) for p in patterns):
            return service, category
    return 'Não identificado', 'Outros'

