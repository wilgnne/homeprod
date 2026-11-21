# Herdr Core via SSH

A imagem Ubuntu 24.04 instala o binário Linux estático do Herdr Core (`hc`) 0.1.21 para `amd64` e `arm64`, com SHA-256 fixado por arquitetura. A etapa de download usa certificados CA, curl, tar e sha256sum. Rust, Zig 0.16 e just são dependências da compilação do Herdr a partir do código-fonte. [README oficial](https://github.com/herdrdev/herdr-core), [manifesto da versão](https://releases.herdr.dev/core/0.1.21/manifest.json).

A imagem final inclui OpenSSH, Git, GitHub CLI (`gh`), curl, wget e Codex CLI. O Codex é instalado durante o build em `/opt/codex`, com o comando disponível em `/usr/local/bin/codex`. Defina `CODEX_VERSION` no `.env` para fixar uma versão. A configuração do usuário em `~/.codex` fica no volume do home. [Instalador oficial do Codex](https://chatgpt.com/codex/install.sh).

## DNS e Traefik

- Escolha o nome em `HERDR_SSH_HOST` no `.env` e aponte esse alias no DNS para o host do Traefik. Use CNAME para um nome que já resolva para esse host ou A/AAAA para seus endereços. O alias é usado pelo cliente SSH.
- No Traefik, configure um entrypoint TCP estático dedicado. O exemplo usa o nome `herdr-ssh` e a porta `2222/tcp`. Se o Traefik rodar em Docker, publique `2222:2222/tcp` no serviço do Traefik e permita acesso TCP nessa porta a partir da rede dos clientes.
- Mantenha o Traefik e o Herdr na rede Docker externa `proxy`, com o provider Docker habilitado no Traefik. O Herdr escuta na porta interna 22 e só o Traefik publica a porta de acesso.
- SSH puro não envia SNI. O roteador TCP com ``HostSNI(`*`)`` encaminha todas as conexões desse entrypoint ao OpenSSH do Herdr. Reserve esse entrypoint para o Herdr e deixe-o sem roteadores TLS. Para outro alias apontar para outro backend SSH no mesmo endereço IP, use outra porta e outro entrypoint. [Regras TCP do Traefik](https://doc.traefik.io/traefik/reference/routing-configuration/tcp/routing/rules-and-priority/).

Exemplo de configuração estática do Traefik:

```yaml
entryPoints:
  herdr-ssh:
    address: ":2222/tcp"
```

Se alterar o nome do entrypoint, atualize `HERDR_SSH_ENTRYPOINT` no `.env`. Se alterar a porta externa publicada pelo Traefik, atualize `HERDR_SSH_PORT` para os clientes.

## Preparação e acesso

Em `herdr/`, copie `.env.example` para `.env` e ajuste os valores. Crie o arquivo em `HERDR_AUTHORIZED_KEYS_FILE` com uma chave **pública** OpenSSH autorizada por linha. Caminhos relativos partem de `herdr/`. O contêiner lê esse arquivo na inicialização, então reinicie o serviço após editar as chaves. O único usuário de login é `herdr`, autenticado por chave pública.

Após preparar a rede, o DNS e o entrypoint, os comandos abaixo validam a configuração e iniciam o serviço. Use um arquivo `.env` com sintaxe de shell para o exemplo de conexão.

```sh
docker compose config -q
docker compose up -d --build

. ./.env
ssh -p "$HERDR_SSH_PORT" "herdr@$HERDR_SSH_HOST" hc --version
hc machine add "herdr@$HERDR_SSH_HOST:$HERDR_SSH_PORT" --name homeprod
```

Antes de aceitar a chave do servidor na primeira conexão, compare a impressão digital com a saída de `docker compose exec herdr ssh-keygen -lf /var/lib/herdr-ssh/ssh_host_ed25519_key`. O endereço com porta é aceito por `hc machine add`. [Máquinas via SSH](https://github.com/herdrdev/herdr-core/blob/master/docs/machines.md).

## Persistência

| Volume | Dados |
| --- | --- |
| `herdr-home` | Home do usuário, incluindo `~/.local/state/herdr-core/` e `~/.config/herdr-core/` |
| `herdr-workspaces` | Projetos em `/home/herdr/workspaces` |
| `herdr-host-keys` | Identidade SSH gerada na primeira inicialização |

Preserve os três volumes ao recriar ou atualizar o contêiner. O estado gravado e os arquivos persistem, mas os processos e terminais em execução terminam quando o contêiner para. [Estado e sockets do Herdr](https://github.com/herdrdev/herdr-core/blob/master/docs/security.md#state-and-endpoints).
